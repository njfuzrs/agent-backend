"""bridge 模块：签发 / 吊销 / 列表。

契约：
- POST /ctl/bridge/sessions 无凭据 401；X-Upload-Token 当鉴权也 401
- 明文 token 只在响应里出现一次，库里只有 sha256
- device_id / org_id 从凭据取，body 里塞别的组织无效
- 同组织在线 50 条后再签发 → 429；别的组织不受影响
- controller token 重签使旧的立即作废；强制断开 reason 必填且进审计
- 列表不含 token；waiting 超过 10 分钟的顺手标 expired
"""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest

os.environ.setdefault("AUTH_PASSWORD", "ci-not-a-secret")
os.environ.setdefault("UPLOAD_TOKEN", "ci-not-a-secret")

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture
def client(tmp_path, monkeypatch):
    """每个用例独立 SQLite。sidecar 不在这里起，本组只测 REST。"""
    db_path = tmp_path / "bridge.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.core import db as db_mod
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", db_url)
    monkeypatch.setattr(settings.control_plane, "CTL_ENROLL_ENABLED", True)
    monkeypatch.setattr(settings.control_plane, "BRIDGE_WS_PUBLIC_URL", "")

    engine = create_async_engine(db_url, echo=False)

    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "async_session", session_factory)

    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(alembic_cfg, "head")

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as tc:
        yield tc

    engine.sync_engine.dispose()


def _login(client) -> None:
    from app.core.config import settings

    resp = client.post(
        "/api/v1/auth/login",
        json={"username": settings.AUTH_USERNAME, "password": settings.AUTH_PASSWORD},
    )
    assert resp.status_code == 200, resp.text


def _issue_code(client, org_id: str = "corp-shanghai", team_id: str | None = "infra") -> str:
    _login(client)
    body = {"org_id": org_id, "org_name": org_id, "note": "test"}
    if team_id:
        body["team_id"] = team_id
    resp = client.post("/api/v1/identity/enroll-codes", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["code"]


def _enroll(client, code: str, device_id: str, org_id: str = "corp-shanghai") -> str:
    resp = client.post(
        "/api/v1/ctl/enroll",
        json={
            "device_id": device_id,
            "user_id": "zhangsan@corp.com",
            "org_id": org_id,
            "team_id": "infra",
            "platform": "darwin",
            "ver": "0.1.604",
        },
        headers={"X-Enroll-Token": code},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["credential"]


def _create(client, cred: str, body: dict | None = None):
    return client.post(
        "/api/v1/ctl/bridge/sessions",
        json={} if body is None else body,
        headers={"Authorization": f"Bearer {cred}"},
    )


def _hashes(token: str) -> list[str]:
    """库里这个明文对应的 hash 行数。测试直接查库，不走列表。"""
    import asyncio

    from sqlalchemy import select

    from app.core import db as db_mod
    from app.modules.bridge.model import BridgeSessionToken

    async def _query():
        async with db_mod.async_session() as db:
            rows = await db.execute(
                select(BridgeSessionToken).where(
                    BridgeSessionToken.token_hash == hashlib.sha256(token.encode()).hexdigest()
                )
            )
            return list(rows.scalars())

    return asyncio.run(_query())


# ---------------------------------------------------------------------------
# 鉴权 fail-closed
# ---------------------------------------------------------------------------
def test_unauthenticated_create_is_401(client):
    resp = client.post("/api/v1/ctl/bridge/sessions", json={})
    assert resp.status_code == 401
    assert resp.status_code != 201


def test_bad_bearer_is_401(client):
    resp = client.post(
        "/api/v1/ctl/bridge/sessions",
        json={},
        headers={"Authorization": "Bearer totally-not-a-credential"},
    )
    assert resp.status_code == 401


def test_upload_token_without_bearer_is_401(client):
    from app.core.config import settings

    resp = client.post(
        "/api/v1/ctl/bridge/sessions",
        json={},
        headers={"X-Upload-Token": settings.data_plane.UPLOAD_TOKEN},
    )
    assert resp.status_code == 401


def test_upload_token_cannot_list_sessions(client):
    from app.core.config import settings

    resp = client.get(
        "/api/v1/bridge/sessions",
        headers={"X-Upload-Token": settings.data_plane.UPLOAD_TOKEN},
    )
    assert resp.status_code == 401


def test_device_credential_cannot_list_sessions(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-1")
    # 发码时登过管理台，cookie 还在。这里要验证的是设备凭据本身不够，所以先清掉。
    client.cookies.clear()
    resp = client.get(
        "/api/v1/bridge/sessions",
        headers={"Authorization": f"Bearer {cred}"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 签发
# ---------------------------------------------------------------------------
def test_create_returns_token_once_and_stores_only_hash(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-1")
    resp = _create(client, cred, {"ver": "0.1.604", "cwd_basename": "sid-code"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["session_id"].startswith("br_")
    assert body["ws_url"] == "ws://127.0.0.1:8901/api/v1/bridge/ws"
    assert "session_token" in body

    rows = _hashes(body["session_token"])
    assert len(rows) == 1
    assert rows[0].role == "cli"
    assert rows[0].revoked_at is None
    # 明文不在库里。hash 列不等于明文。
    assert rows[0].token_hash != body["session_token"]


def test_absolute_cwd_is_stored_as_basename(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-1")
    resp = _create(client, cred, {"cwd_basename": "/Users/someone/Code/sid-code"})
    assert resp.status_code == 201, resp.text

    _login(client)
    listed = client.get("/api/v1/bridge/sessions")
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["cwd_basename"] == "sid-code"
    assert "session_token" not in item
    assert "token_hash" not in item


def test_body_org_does_not_override_credential(client):
    """body 里塞另一个组织也不进库。身份只来自凭据。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-1")
    resp = client.post(
        "/api/v1/ctl/bridge/sessions",
        json={"org_id": "attacker-org", "device_id": "attacker-device"},
        headers={"Authorization": f"Bearer {cred}"},
    )
    # extra=forbid：多出来的身份字段直接 422，而不是悄悄收下。
    assert resp.status_code == 422, resp.text


def test_whoami_returns_open_session_without_token(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-1")
    created = _create(client, cred).json()
    resp = client.get(
        "/api/v1/ctl/bridge/sessions/whoami",
        headers={"Authorization": f"Bearer {cred}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"] == created["session_id"]
    assert body["state"] == "waiting"
    assert "session_token" not in body


def test_org_cap_is_429_and_does_not_block_other_orgs(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-1")
    for _ in range(50):
        assert _create(client, cred).status_code == 201
    assert _create(client, cred).status_code == 429

    other_code = _issue_code(client, org_id="corp-beijing", team_id=None)
    other = _enroll(client, other_code, "dev-2", org_id="corp-beijing")
    assert _create(client, other).status_code == 201


def test_public_url_must_be_wss_or_loopback(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(
        settings.control_plane, "BRIDGE_WS_PUBLIC_URL", "ws://203.0.113.5/bridge/ws"
    )
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-1")
    resp = _create(client, cred)
    assert resp.status_code == 500, resp.text

    monkeypatch.setattr(
        settings.control_plane,
        "BRIDGE_WS_PUBLIC_URL",
        "wss://www.sid-code.cc/traj/api/v1/bridge/ws",
    )
    resp = _create(client, cred)
    assert resp.status_code == 201
    assert resp.json()["ws_url"].startswith("wss://")


# ---------------------------------------------------------------------------
# 管理台：控制端 token / 强制断开 / 列表
# ---------------------------------------------------------------------------
def test_controller_token_replaces_the_previous_one(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-1")
    session_id = _create(client, cred).json()["session_id"]

    _login(client)
    first = client.post(f"/api/v1/bridge/sessions/{session_id}/controller-token")
    assert first.status_code == 201, first.text
    second = client.post(f"/api/v1/bridge/sessions/{session_id}/controller-token")
    assert second.status_code == 201

    # 同一 (session, role) 只有一行。重签换的是 hash，旧明文不再能查到。
    assert _hashes(first.json()["session_token"]) == []
    new = _hashes(second.json()["session_token"])
    assert len(new) == 1 and new[0].revoked_at is None
    assert new[0].role == "controller"


def test_disconnect_requires_reason_and_is_audited(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-1")
    session_id = _create(client, cred).json()["session_id"]

    _login(client)
    missing = client.post(f"/api/v1/bridge/sessions/{session_id}/disconnect", json={})
    assert missing.status_code == 422

    blank = client.post(
        f"/api/v1/bridge/sessions/{session_id}/disconnect", json={"reason": "   "}
    )
    assert blank.status_code == 422

    ok = client.post(
        f"/api/v1/bridge/sessions/{session_id}/disconnect",
        json={"reason": "怀疑凭据泄漏"},
    )
    assert ok.status_code == 204, ok.text

    listed = client.get("/api/v1/bridge/sessions")
    item = next(i for i in listed.json()["items"] if i["id"] == session_id)
    assert item["state"] == "disconnected"

    # token 同时作废：强制断开后重连也进不来。
    import asyncio

    from sqlalchemy import select

    from app.core import db as db_mod
    from app.modules.bridge.model import BridgeAudit, BridgeSessionToken

    async def _query():
        async with db_mod.async_session() as db:
            tokens = await db.execute(
                select(BridgeSessionToken).where(BridgeSessionToken.session_id == session_id)
            )
            audits = await db.execute(
                select(BridgeAudit).where(BridgeAudit.session_id == session_id)
            )
            return list(tokens.scalars()), list(audits.scalars())

    tokens, audits = asyncio.run(_query())
    assert tokens and all(t.revoked_at is not None for t in tokens)
    actions = {a.action for a in audits}
    assert {"issue_cli", "disconnect"} <= actions
    disconnect = next(a for a in audits if a.action == "disconnect")
    assert disconnect.reason == "怀疑凭据泄漏"
    assert disconnect.actor.startswith("web:")


def test_controller_token_rejected_after_disconnect(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-1")
    session_id = _create(client, cred).json()["session_id"]
    _login(client)
    client.post(
        f"/api/v1/bridge/sessions/{session_id}/disconnect",
        json={"reason": "结束"},
    )
    again = client.post(f"/api/v1/bridge/sessions/{session_id}/controller-token")
    assert again.status_code == 409


def test_waiting_over_ten_minutes_is_marked_expired(client):
    """列表接口顺手标。不另起 cron，握手时仍按 expires_at 拒绝。"""
    from datetime import datetime, timezone

    from app.core.timeutil import utc_now

    code = _issue_code(client)
    cred = _enroll(client, code, "dev-1")
    session_id = _create(client, cred).json()["session_id"]

    import asyncio

    from sqlalchemy import select

    from app.core import db as db_mod
    from app.modules.bridge.model import BridgeSession

    stale = (utc_now() - timedelta(minutes=11)).isoformat()

    async def _backdate():
        async with db_mod.async_session() as db:
            row = await db.execute(select(BridgeSession).where(BridgeSession.id == session_id))
            row.scalar_one().created_at = stale
            await db.commit()

    asyncio.run(_backdate())

    _login(client)
    listed = client.get("/api/v1/bridge/sessions")
    item = next(i for i in listed.json()["items"] if i["id"] == session_id)
    assert item["state"] == "expired"
    # datetime 导入留着给断言一个可解析的时间，避免标错格式。
    datetime.fromisoformat(item["created_at"]).astimezone(timezone.utc)


def test_list_filters_narrow_items_but_not_counts(client):
    """筛选只收窄表。页首三个数字按全集算，否则选了 paired 就看不到 waiting。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-1")
    waiting_id = _create(client, cred, {"ver": "0.1.604", "cwd_basename": "sid-code"}).json()[
        "session_id"
    ]
    paired_id = _create(client, cred).json()["session_id"]

    other_code = _issue_code(client, org_id="corp-beijing", team_id=None)
    other = _enroll(client, other_code, "dev-2", org_id="corp-beijing")
    other_id = _create(client, other).json()["session_id"]

    import asyncio

    from sqlalchemy import select

    from app.core import db as db_mod
    from app.modules.bridge.model import BridgeSession

    async def _pair():
        async with db_mod.async_session() as db:
            row = await db.execute(select(BridgeSession).where(BridgeSession.id == paired_id))
            row.scalar_one().state = "paired"
            await db.commit()

    asyncio.run(_pair())

    _login(client)
    all_rows = client.get("/api/v1/bridge/sessions")
    assert all_rows.status_code == 200
    body = all_rows.json()
    assert body["total"] == 3
    assert body["counts"] == {"paired": 1, "waiting": 2, "disconnect_24h": 0}
    assert "session_token" not in body["items"][0]

    by_state = client.get("/api/v1/bridge/sessions", params={"state": "paired"})
    narrowed = by_state.json()
    assert [i["id"] for i in narrowed["items"]] == [paired_id]
    assert narrowed["total"] == 1
    # 选了 paired，waiting 不能跟着变成 0。
    assert narrowed["counts"]["waiting"] == 2
    assert narrowed["counts"]["paired"] == 1

    by_org = client.get("/api/v1/bridge/sessions", params={"org_id": "corp-beijing"})
    assert [i["id"] for i in by_org.json()["items"]] == [other_id]
    assert by_org.json()["counts"]["waiting"] == 2

    by_device = client.get("/api/v1/bridge/sessions", params={"device_id": "dev-1"})
    assert {i["id"] for i in by_device.json()["items"]} == {waiting_id, paired_id}

    gone = client.post(
        f"/api/v1/bridge/sessions/{paired_id}/disconnect",
        json={"reason": "验收踢人"},
    )
    assert gone.status_code == 204
    after = client.get("/api/v1/bridge/sessions").json()
    assert after["counts"]["paired"] == 0
    assert after["counts"]["disconnect_24h"] == 1

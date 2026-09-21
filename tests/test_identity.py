"""identity 模块：注册码 / 凭据签发 / require_device / 吊销。

规划 PR-1.3 验收：
- 注册码用第二次 → 401
- 吊销后原凭据立即失效
- DB 搜不到凭据明文
- 无凭据访问 /ctl/ → 401（不再是 501）
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from pathlib import Path

import pytest

# 必须在 import app 之前设好必填配置。CI 已经有 AUTH_*；本地跑也要能绿。
os.environ.setdefault("AUTH_PASSWORD", "ci-not-a-secret")
os.environ.setdefault("UPLOAD_TOKEN", "ci-not-a-secret")

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture
def client(tmp_path, monkeypatch):
    """每个用例独立 SQLite：改 settings 原地字段，不重建对象（各模块已持有引用）。"""
    db_path = tmp_path / "identity.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.core import db as db_mod
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", db_url)
    monkeypatch.setattr(settings.control_plane, "CTL_ENROLL_ENABLED", True)

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
        yield tc, db_path

    engine.sync_engine.dispose()


def _login(client) -> None:
    from app.core.config import settings

    resp = client.post(
        "/api/v1/auth/login",
        json={"username": settings.AUTH_USERNAME, "password": settings.AUTH_PASSWORD},
    )
    assert resp.status_code == 200, resp.text


def _issue_code(client, org_id: str = "corp-shanghai", team_id: str = "infra") -> str:
    _login(client)
    resp = client.post(
        "/api/v1/identity/enroll-codes",
        json={"org_id": org_id, "org_name": "上海", "team_id": team_id, "note": "test"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["code"]


def _enroll(client, code: str, device_id: str = "dev-1", **extra) -> dict:
    body = {
        "device_id": device_id,
        "user_id": "zhangsan@corp.com",
        "org_id": "corp-shanghai",
        "team_id": "infra",
        "platform": "darwin",
        "ver": "0.1.604",
    }
    body.update(extra)
    resp = client.post(
        "/api/v1/ctl/enroll",
        json=body,
        headers={"X-Enroll-Token": code},
    )
    data = resp.json() if resp.content else {}
    return {"status": resp.status_code, "body": data}


def test_unauthenticated_ctl_whoami_is_401_not_501(client):
    tc, _ = client
    resp = tc.get("/api/v1/ctl/whoami")
    assert resp.status_code == 401
    assert resp.status_code != 501


def test_enroll_disabled_returns_403(client, monkeypatch):
    tc, _ = client
    from app.core.config import settings

    monkeypatch.setattr(settings.control_plane, "CTL_ENROLL_ENABLED", False)
    result = _enroll(tc, "whatever")
    assert result["status"] == 403


def test_enroll_code_second_use_is_401(client):
    tc, _ = client
    code = _issue_code(tc)
    first = _enroll(tc, code, device_id="dev-a")
    assert first["status"] == 201, first
    assert first["body"]["credential"]
    assert first["body"]["org_id"] == "corp-shanghai"

    second = _enroll(tc, code, device_id="dev-b")
    assert second["status"] == 401


def test_plaintext_credential_not_in_db(client):
    tc, db_path = client
    code = _issue_code(tc)
    first = _enroll(tc, code)
    assert first["status"] == 201, first
    plaintext = first["body"]["credential"]
    expected_hash = hashlib.sha256(plaintext.encode()).hexdigest()

    conn = sqlite3.connect(db_path)
    try:
        hashes = [row[0] for row in conn.execute("SELECT token_hash FROM device_credentials")]
        assert expected_hash in hashes
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for name in tables:
            cols = [row[1] for row in conn.execute(f"PRAGMA table_info({name})")]
            if not cols:
                continue
            sel = ", ".join(cols)
            for row in conn.execute(f"SELECT {sel} FROM {name}"):
                blob = " ".join("" if v is None else str(v) for v in row)
                assert plaintext not in blob, f"明文凭据出现在表 {name}"
    finally:
        conn.close()


def test_whoami_with_bearer(client):
    tc, _ = client
    code = _issue_code(tc)
    enrolled = _enroll(tc, code, device_id="dev-who")
    cred = enrolled["body"]["credential"]
    resp = tc.get(
        "/api/v1/ctl/whoami",
        headers={"Authorization": f"Bearer {cred}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["device_id"] == "dev-who"
    assert data["org_id"] == "corp-shanghai"
    assert data["team_id"] == "infra"
    assert data["user_id"] == "zhangsan@corp.com"


def test_revoke_invalidates_credential(client):
    tc, _ = client
    code = _issue_code(tc)
    enrolled = _enroll(tc, code, device_id="dev-rev")
    cred = enrolled["body"]["credential"]

    resp = tc.post("/api/v1/identity/devices/dev-rev/revoke")
    assert resp.status_code == 200, resp.text
    assert resp.json()["revoked"] is True

    who = tc.get(
        "/api/v1/ctl/whoami",
        headers={"Authorization": f"Bearer {cred}"},
    )
    assert who.status_code == 401


def test_wrong_token_is_401(client):
    tc, _ = client
    resp = tc.get(
        "/api/v1/ctl/whoami",
        headers={"Authorization": "Bearer totally-not-a-credential"},
    )
    assert resp.status_code == 401


def test_admin_device_list_shows_last_seen(client):
    tc, _ = client
    code = _issue_code(tc)
    enrolled = _enroll(tc, code, device_id="dev-seen")
    cred = enrolled["body"]["credential"]
    seen = tc.get("/api/v1/ctl/whoami", headers={"Authorization": f"Bearer {cred}"})
    assert seen.status_code == 200, seen.text

    listed = tc.get("/api/v1/identity/devices")
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    match = next(i for i in items if i["device_id"] == "dev-seen")
    assert match["last_seen_at"]
    assert match["org_id"] == "corp-shanghai"

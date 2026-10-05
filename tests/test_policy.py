"""policy 模块：下发契约 / 求值 / 门禁 / 审计。

规划 §4 M3 验收：
- `GET /ctl/policy` 无凭据 401（不是 200/204）；有凭据按 device>team>org 返回子集
- ETag / 304；Cache-Control 含 private 且不含 public
- 管理台 reason 必填；未知字段 422；空策略 422；同层第二条 enabled 409
- 上传 token 写不进 /policies
- 审计在 delete 后仍在
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("AUTH_PASSWORD", "ci-not-a-secret")
os.environ.setdefault("UPLOAD_TOKEN", "ci-not-a-secret")

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


DENY_CURL = {"permissions": {"deny": ["Bash(curl *)"]}}
DENY_RM = {"permissions": {"deny": ["Bash(rm *)"]}}
LIMIT_MCP = {"policyLimits": {"mcp": {"allowed": False, "reason": "未审计的 MCP 不允许"}}}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """每个用例独立 SQLite：改 settings 原地字段，不重建对象（各模块已持有引用）。"""
    db_path = tmp_path / "policy.db"
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


def _enroll(client, code: str, device_id: str, org_id: str = "corp-shanghai", team_id: str | None = "infra") -> str:
    body = {
        "device_id": device_id,
        "user_id": "zhangsan@corp.com",
        "org_id": org_id,
        "platform": "darwin",
        "ver": "0.1.604",
    }
    if team_id:
        body["team_id"] = team_id
    resp = client.post(
        "/api/v1/ctl/enroll",
        json=body,
        headers={"X-Enroll-Token": code},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["credential"]


def _create_org(client, org_id: str) -> None:
    _login(client)
    resp = client.post("/api/v1/identity/organizations", json={"org_id": org_id, "name": org_id})
    # 409 = 签发码时已经建过
    assert resp.status_code in {201, 409}, resp.text


def _create_policy(client, *, scope_type: str, scope_id: str, org_id: str, settings: dict, reason: str = "test"):
    _login(client)
    return client.post(
        "/api/v1/policies",
        json={
            "scope_type": scope_type,
            "scope_id": scope_id,
            "org_id": org_id,
            "settings": settings,
            "reason": reason,
        },
    )


def _get_policy(client, cred: str, etag: str | None = None):
    headers = {"Authorization": f"Bearer {cred}"}
    if etag:
        headers["If-None-Match"] = etag
    return client.get("/api/v1/ctl/policy", headers=headers)


# ---------------------------------------------------------------------------
# 下发鉴权
# ---------------------------------------------------------------------------
def test_unauthenticated_get_is_401(client):
    resp = client.get("/api/v1/ctl/policy")
    assert resp.status_code == 401, resp.text
    assert resp.status_code not in {200, 204}


def test_bad_bearer_is_401(client):
    resp = client.get("/api/v1/ctl/policy", headers={"Authorization": "Bearer totally-not-a-credential"})
    assert resp.status_code == 401


def test_upload_token_without_bearer_is_401(client):
    from app.core.config import settings

    resp = client.get(
        "/api/v1/ctl/policy",
        headers={"X-Upload-Token": settings.data_plane.UPLOAD_TOKEN},
    )
    assert resp.status_code == 401


def test_empty_db_is_204(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-empty")
    resp = _get_policy(client, cred)
    assert resp.status_code == 204, resp.text
    assert not resp.content
    assert resp.headers.get("x-policy-generation") == '"none"'


# ---------------------------------------------------------------------------
# 求值：device > team > org，不合并
# ---------------------------------------------------------------------------
def test_org_policy_is_delivered_with_source_remote(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-org")
    created = _create_policy(
        client,
        scope_type="org",
        scope_id="corp-shanghai",
        org_id="corp-shanghai",
        settings=DENY_CURL,
    )
    assert created.status_code == 201, created.text

    resp = _get_policy(client, cred)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "remote"
    assert "scope_id" not in body
    assert "id" not in body
    assert body["permissions"]["deny"] == ["Bash(curl *)"]
    assert "private" in resp.headers.get("cache-control", "")
    assert "public" not in resp.headers.get("cache-control", "")
    assert resp.headers.get("etag", "").startswith('"')


def test_device_policy_wins_over_org_without_merge(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-win")
    assert _create_policy(
        client, scope_type="org", scope_id="corp-shanghai", org_id="corp-shanghai", settings=LIMIT_MCP
    ).status_code == 201
    assert _create_policy(
        client, scope_type="device", scope_id="dev-win", org_id="corp-shanghai", settings=DENY_CURL
    ).status_code == 201

    body = _get_policy(client, cred).json()
    assert body["permissions"]["deny"] == ["Bash(curl *)"]
    assert "policyLimits" not in body, "device 命中则不合并 org"


def test_sibling_device_still_gets_org(client):
    code = _issue_code(client)
    cred_a = _enroll(client, code, "dev-a")
    code_b = _issue_code(client)
    cred_b = _enroll(client, code_b, "dev-b")
    assert _create_policy(
        client, scope_type="org", scope_id="corp-shanghai", org_id="corp-shanghai", settings=LIMIT_MCP
    ).status_code == 201
    assert _create_policy(
        client, scope_type="device", scope_id="dev-a", org_id="corp-shanghai", settings=DENY_CURL
    ).status_code == 201

    a = _get_policy(client, cred_a).json()
    b = _get_policy(client, cred_b).json()
    assert a["permissions"]["deny"] == ["Bash(curl *)"]
    assert "policyLimits" not in a
    assert b["policyLimits"]["mcp"]["allowed"] is False
    assert "permissions" not in b


def test_team_skipped_when_device_has_no_team(client):
    code = _issue_code(client, team_id=None)
    cred = _enroll(client, code, "dev-noteam", team_id=None)
    _create_org(client, "corp-shanghai")
    # 先签发一个带 team 的码，把 infra 团队建出来，再给它配策略
    code_with_team = _issue_code(client, team_id="infra")
    _enroll(client, code_with_team, "dev-with-team")
    assert _create_policy(
        client, scope_type="team", scope_id="infra", org_id="corp-shanghai", settings=DENY_RM
    ).status_code == 201
    assert _create_policy(
        client, scope_type="org", scope_id="corp-shanghai", org_id="corp-shanghai", settings=DENY_CURL
    ).status_code == 201

    body = _get_policy(client, cred).json()
    assert body["permissions"]["deny"] == ["Bash(curl *)"], "无 team 应跳过 team 层用 org"


def test_team_org_mismatch_does_not_hit(client):
    """跨 org 同名 team 不得串台：北京设备不能拿到上海/infra 的策略。"""
    code_sh = _issue_code(client, org_id="corp-shanghai", team_id="infra")
    _enroll(client, code_sh, "dev-sh")
    code_bj = _issue_code(client, org_id="corp-beijing", team_id="infra")
    cred_bj = _enroll(client, code_bj, "dev-bj", org_id="corp-beijing", team_id="infra")

    created = _create_policy(
        client, scope_type="team", scope_id="infra", org_id="corp-shanghai", settings=DENY_CURL
    )
    assert created.status_code == 201, created.text

    resp = _get_policy(client, cred_bj)
    assert resp.status_code == 204, resp.text


def test_etag_304_and_reason_change_busts_etag(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-etag")
    created = _create_policy(
        client,
        scope_type="org",
        scope_id="corp-shanghai",
        org_id="corp-shanghai",
        settings=LIMIT_MCP,
    )
    assert created.status_code == 201, created.text
    policy_id = created.json()["id"]

    first = _get_policy(client, cred)
    assert first.status_code == 200
    etag = first.headers["etag"]
    second = _get_policy(client, cred, etag=etag)
    assert second.status_code == 304
    assert not second.content

    updated = client.put(
        f"/api/v1/policies/{policy_id}",
        json={
            "settings": {"policyLimits": {"mcp": {"allowed": False, "reason": "文案改了"}}},
            "reason": "改禁用理由",
        },
    )
    assert updated.status_code == 200, updated.text
    third = _get_policy(client, cred, etag=etag)
    assert third.status_code == 200, third.text
    assert third.headers["etag"] != etag
    assert third.json()["policyLimits"]["mcp"]["reason"] == "文案改了"


def test_disable_unique_policy_returns_204(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-off")
    created = _create_policy(
        client, scope_type="org", scope_id="corp-shanghai", org_id="corp-shanghai", settings=DENY_CURL
    )
    policy_id = created.json()["id"]
    first = _get_policy(client, cred)
    assert first.status_code == 200
    delivery_etag = first.headers["etag"]

    off = client.post(f"/api/v1/policies/{policy_id}/disable", params={"reason": "先停"})
    assert off.status_code == 200, off.text
    gone = _get_policy(client, cred, etag=delivery_etag)
    assert gone.status_code == 204, gone.text
    assert gone.headers.get("x-policy-generation") == '"none"'
    assert not gone.content

    on = client.post(f"/api/v1/policies/{policy_id}/enable", params={"reason": "再开"})
    assert on.status_code == 200, on.text
    restored = _get_policy(client, cred)
    assert restored.status_code == 200
    assert restored.headers["etag"] == delivery_etag, "settings 没变，下发 ETag 不应因停用周期而变"


def test_admin_disable_enable_changes_etag(client):
    """管理台 item.etag 必须吃 disabled_at：disable / enable 各变一次。

    下发 ETag 只哈希 settings，停用后走 204，不走 304。不要把两套 etag 混成一个。
    """
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-admin-etag")
    created = _create_policy(
        client, scope_type="org", scope_id="corp-shanghai", org_id="corp-shanghai", settings=DENY_CURL
    )
    assert created.status_code == 201, created.text
    policy_id = created.json()["id"]
    etag_on = created.json()["etag"]
    assert etag_on.startswith('"'), etag_on

    delivered = _get_policy(client, cred)
    assert delivered.status_code == 200
    delivery_etag = delivered.headers["etag"]
    assert delivered.headers.get("x-policy-generation") == delivery_etag

    off = client.post(f"/api/v1/policies/{policy_id}/disable", params={"reason": "先停"})
    assert off.status_code == 200, off.text
    etag_off = off.json()["etag"]
    assert off.json()["disabled"] is True
    assert etag_off != etag_on, (etag_on, etag_off)

    listed = client.get("/api/v1/policies")
    assert listed.status_code == 200, listed.text
    item = next(p for p in listed.json()["items"] if p["id"] == policy_id)
    assert item["etag"] == etag_off

    got = client.get(f"/api/v1/policies/{policy_id}")
    assert got.status_code == 200, got.text
    assert got.json()["etag"] == etag_off

    on = client.post(f"/api/v1/policies/{policy_id}/enable", params={"reason": "再开"})
    assert on.status_code == 200, on.text
    etag_on_again = on.json()["etag"]
    assert on.json()["disabled"] is False
    assert etag_on_again != etag_off, (etag_off, etag_on_again)
    assert etag_on_again == etag_on, "settings 没变、重新启用后应回到启用态 etag"


def test_bool_is_json_literal_not_string(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-bool")
    created = _create_policy(
        client,
        scope_type="org",
        scope_id="corp-shanghai",
        org_id="corp-shanghai",
        settings={"disableAllHooks": True},
    )
    assert created.status_code == 201, created.text
    body = _get_policy(client, cred).text.replace(" ", "")
    assert '"disableAllHooks":true' in body, body
    assert '"disableAllHooks":"true"' not in body


# ---------------------------------------------------------------------------
# 管理台 fail-closed
# ---------------------------------------------------------------------------
def test_admin_writes_require_session(client):
    body = {
        "scope_type": "org",
        "scope_id": "corp-shanghai",
        "org_id": "corp-shanghai",
        "settings": DENY_CURL,
        "reason": "x",
    }
    for method, path in [
        ("post", "/api/v1/policies"),
        ("put", "/api/v1/policies/1"),
        ("delete", "/api/v1/policies/1"),
        ("post", "/api/v1/policies/1/disable"),
        ("post", "/api/v1/policies/1/enable"),
        ("get", "/api/v1/policies"),
        ("get", "/api/v1/policies/audit"),
    ]:
        resp = client.request(method.upper(), path, json=body if method in {"post", "put"} else None)
        assert resp.status_code == 401, f"{method.upper()} {path} → {resp.status_code}"


def test_missing_reason_is_422(client):
    _issue_code(client)  # 建 org
    _login(client)
    resp = client.post(
        "/api/v1/policies",
        json={
            "scope_type": "org",
            "scope_id": "corp-shanghai",
            "org_id": "corp-shanghai",
            "settings": DENY_CURL,
            "reason": "",
        },
    )
    assert resp.status_code == 422, resp.text


def test_settings_source_or_endpoint_is_422(client):
    _issue_code(client)
    for extra in ({"source": "remote"}, {"policyEndpoint": "https://evil.example/policy"}):
        resp = _create_policy(
            client,
            scope_type="org",
            scope_id="corp-shanghai",
            org_id="corp-shanghai",
            settings={**DENY_CURL, **extra},
        )
        assert resp.status_code == 422, resp.text


def test_network_access_limit_is_422(client):
    _issue_code(client)
    resp = _create_policy(
        client,
        scope_type="org",
        scope_id="corp-shanghai",
        org_id="corp-shanghai",
        settings={"policyLimits": {"network_access": {"allowed": False, "reason": "断网"}}},
    )
    assert resp.status_code == 422, resp.text
    assert "gate" in resp.json()["detail"] or "咽喉" in resp.json()["detail"]


def test_empty_settings_is_422(client):
    _issue_code(client)
    resp = _create_policy(
        client,
        scope_type="org",
        scope_id="corp-shanghai",
        org_id="corp-shanghai",
        settings={},
    )
    assert resp.status_code == 422, resp.text


def test_duplicate_enabled_is_409(client):
    _issue_code(client)
    first = _create_policy(
        client, scope_type="org", scope_id="corp-shanghai", org_id="corp-shanghai", settings=DENY_CURL
    )
    assert first.status_code == 201, first.text
    second = _create_policy(
        client, scope_type="org", scope_id="corp-shanghai", org_id="corp-shanghai", settings=DENY_RM
    )
    assert second.status_code == 409, second.text


def test_enable_second_enabled_is_409(client):
    _issue_code(client)
    a = _create_policy(
        client, scope_type="org", scope_id="corp-shanghai", org_id="corp-shanghai", settings=DENY_CURL
    )
    assert a.status_code == 201
    off = client.post(f"/api/v1/policies/{a.json()['id']}/disable", params={"reason": "停"})
    assert off.status_code == 200
    b = _create_policy(
        client, scope_type="org", scope_id="corp-shanghai", org_id="corp-shanghai", settings=DENY_RM
    )
    assert b.status_code == 201, b.text
    again = client.post(f"/api/v1/policies/{a.json()['id']}/enable", params={"reason": "再开"})
    assert again.status_code == 409, again.text


def test_upload_token_cannot_write_policies(client):
    from app.core.config import settings

    resp = client.post(
        "/api/v1/policies",
        json={
            "scope_type": "org",
            "scope_id": "corp-shanghai",
            "org_id": "corp-shanghai",
            "settings": DENY_CURL,
            "reason": "x",
        },
        headers={"X-Upload-Token": settings.data_plane.UPLOAD_TOKEN},
    )
    assert resp.status_code == 401


def test_audit_survives_deletion(client):
    _issue_code(client)
    created = _create_policy(
        client,
        scope_type="org",
        scope_id="corp-shanghai",
        org_id="corp-shanghai",
        settings=DENY_CURL,
        reason="上线",
    )
    policy_id = created.json()["id"]
    deleted = client.delete(f"/api/v1/policies/{policy_id}", params={"reason": "下线"})
    assert deleted.status_code == 200, deleted.text

    items = client.get("/api/v1/policies/audit").json()["items"]
    actions = [a["action"] for a in items]
    assert "delete" in actions and "create" in actions, actions
    deleted_row = next(a for a in items if a["action"] == "delete")
    assert deleted_row["reason"] == "下线"
    assert deleted_row["scope_type"] == "org"
    assert deleted_row["scope_id"] == "corp-shanghai"


def test_admin_evaluate_shows_winning_layer(client):
    """管理台预览走 cookie，不是客户端下发；device 层盖过 org。"""
    code = _issue_code(client)
    _enroll(client, code, "dev-eval")
    assert _create_policy(
        client, scope_type="org", scope_id="corp-shanghai", org_id="corp-shanghai", settings=LIMIT_MCP
    ).status_code == 201
    assert _create_policy(
        client, scope_type="device", scope_id="dev-eval", org_id="corp-shanghai", settings=DENY_CURL
    ).status_code == 201
    _login(client)
    resp = client.get("/api/v1/policies/evaluate", params={"device_id": "dev-eval"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["layer"] == "device"
    assert body["scope_id"] == "dev-eval"
    assert "policyLimits" not in body["settings"]
    assert body["settings"]["permissions"]["deny"] == ["Bash(curl *)"]


def test_device_org_id_overridden_by_real_org(client):
    """客户端乱填 org_id 不能把设备策略写到别人名下。"""
    code = _issue_code(client)
    _enroll(client, code, "dev-real")
    _create_org(client, "corp-other")
    created = _create_policy(
        client,
        scope_type="device",
        scope_id="dev-real",
        org_id="corp-other",
        settings=DENY_CURL,
    )
    assert created.status_code == 201, created.text
    assert created.json()["org_id"] == "corp-shanghai"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ---------------------------------------------------------------------------
# bridgeEnabled：省略 = 不关；false 是一项约束；true 写入时丢掉
# ---------------------------------------------------------------------------
def test_bridge_disabled_alone_is_a_constraint_and_is_delivered(client):
    """只关遥控也不是空策略。空 remote 会盖掉本地 managed，所以这一项必须算数。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-bridge")
    created = _create_policy(
        client,
        scope_type="org",
        scope_id="corp-shanghai",
        org_id="corp-shanghai",
        settings={"bridgeEnabled": False},
    )
    assert created.status_code == 201, created.text

    resp = _get_policy(client, cred)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"source": "remote", "bridgeEnabled": False}


def test_bridge_enabled_true_is_not_a_constraint(client):
    """true 与省略同义。单独写它等于空策略，必须 422。"""
    _issue_code(client)
    resp = _create_policy(
        client,
        scope_type="org",
        scope_id="corp-shanghai",
        org_id="corp-shanghai",
        settings={"bridgeEnabled": True},
    )
    assert resp.status_code == 422, resp.text


def test_bridge_enabled_true_is_dropped_when_other_constraints_exist(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-bridge-true")
    created = _create_policy(
        client,
        scope_type="org",
        scope_id="corp-shanghai",
        org_id="corp-shanghai",
        settings={"bridgeEnabled": True, "permissions": {"deny": ["Bash(curl *)"]}},
    )
    assert created.status_code == 201, created.text
    assert "bridgeEnabled" not in created.json()["settings"]

    resp = _get_policy(client, cred)
    assert resp.status_code == 200, resp.text
    assert "bridgeEnabled" not in resp.json()
    assert resp.json()["permissions"]["deny"] == ["Bash(curl *)"]


def test_bridge_enabled_must_be_bool(client):
    _issue_code(client)
    resp = _create_policy(
        client,
        scope_type="org",
        scope_id="corp-shanghai",
        org_id="corp-shanghai",
        settings={"bridgeEnabled": "false"},
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# strictKnownMarketplaces（P5）：市场 index URL 白名单，客户端执行
# ---------------------------------------------------------------------------
def _org_policy(client, settings: dict):
    _issue_code(client)
    return _create_policy(
        client,
        scope_type="org",
        scope_id="corp-shanghai",
        org_id="corp-shanghai",
        settings=settings,
    )


def _mk(*urls: str) -> dict:
    return {"strictKnownMarketplaces": [{"source": "url", "url": u} for u in urls]}


def test_known_marketplaces_delivered_verbatim_and_normalized(client):
    """合法 URL 去尾斜杠、去重后入库，并原样出现在 /ctl/policy 响应里。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-market")
    created = _create_policy(
        client,
        scope_type="org",
        scope_id="corp-shanghai",
        org_id="corp-shanghai",
        settings=_mk(
            "https://market.corp.example/index.json",
            "https://market.corp.example/v2/",
            "https://market.corp.example/v2",  # 去尾斜杠后与上一项重复
            "http://127.0.0.1:8765/index.json",
            "http://localhost:9000",
            "http://[::1]:9001/",
        ),
    )
    assert created.status_code == 201, created.text
    expected = [
        {"source": "url", "url": "https://market.corp.example/index.json"},
        {"source": "url", "url": "https://market.corp.example/v2"},
        {"source": "url", "url": "http://127.0.0.1:8765/index.json"},
        {"source": "url", "url": "http://localhost:9000"},
        {"source": "url", "url": "http://[::1]:9001"},
    ]
    assert created.json()["settings"]["strictKnownMarketplaces"] == expected

    resp = _get_policy(client, cred)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"source": "remote", "strictKnownMarketplaces": expected}


def test_known_marketplaces_empty_array_is_a_constraint(client):
    """空数组 = 除内置外禁一切插件。它是最严的一档，不能被当成空策略拒掉，也不能被丢掉。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-market-empty")
    created = _create_policy(
        client,
        scope_type="org",
        scope_id="corp-shanghai",
        org_id="corp-shanghai",
        settings={"strictKnownMarketplaces": []},
    )
    assert created.status_code == 201, created.text
    resp = _get_policy(client, cred)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"source": "remote", "strictKnownMarketplaces": []}


def test_known_marketplaces_omitted_means_not_delivered(client):
    """省略 = 不限制：下发里不出现这个键（出现空数组就是禁一切，语义完全相反）。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-market-omit")
    assert _create_policy(
        client, scope_type="org", scope_id="corp-shanghai", org_id="corp-shanghai", settings=DENY_CURL
    ).status_code == 201
    resp = _get_policy(client, cred)
    assert "strictKnownMarketplaces" not in resp.json()


@pytest.mark.parametrize(
    "url",
    [
        "http://market.corp.example/index.json",  # 非回环 http
        "http://10.0.0.1/index.json",
        "ftp://market.corp.example/",
        "HTTPS://market.corp.example/",  # 大写 scheme：客户端字符串比较会当另一个市场
        "https://user:pw@market.corp.example/",  # userinfo
        "https://token@market.corp.example/",
        "https://market.corp.example/index.json?ref=main",  # query
        "https://market.corp.example/index.json?",  # 空 query
        "https://market.corp.example/index.json#top",  # fragment
        "https:///index.json",  # 无主机
        "https://market.corp.example/a b",  # 空白
        "https://market.corp.example:99999/",  # 非法端口
        "market.corp.example/index.json",  # 无 scheme
        "",
    ],
)
def test_known_marketplaces_bad_url_is_422(client, url):
    resp = _org_policy(client, _mk(url))
    assert resp.status_code == 422, (url, resp.text)


@pytest.mark.parametrize(
    "item",
    [
        {"source": "url", "url": "https://m.example", "name": "x"},  # extra 字段
        {"source": "git", "url": "https://m.example"},  # source 闭集
        {"url": "https://m.example"},  # 缺 source
        {"source": "url"},  # 缺 url
        {"source": "url", "url": 123},
        "https://m.example",  # 不是对象
    ],
)
def test_known_marketplaces_bad_item_shape_is_422(client, item):
    resp = _org_policy(client, {"strictKnownMarketplaces": [item]})
    assert resp.status_code == 422, (item, resp.text)


def test_known_marketplaces_not_array_is_422(client):
    resp = _org_policy(client, {"strictKnownMarketplaces": {"source": "url", "url": "https://m.example"}})
    assert resp.status_code == 422, resp.text


def test_known_marketplaces_limit_32_after_dedup(client):
    ok = _org_policy(client, _mk(*[f"https://m{i}.example" for i in range(32)]))
    assert ok.status_code == 201, ok.text
    # 33 个不同的 → 422；但 32 个不同 + 重复项不该被拒（上限按去重后计）
    dup = client.put(
        f"/api/v1/policies/{ok.json()['id']}",
        json={
            "settings": _mk(*[f"https://m{i}.example" for i in range(32)], "https://m0.example/"),
            "reason": "dup",
        },
    )
    assert dup.status_code == 200, dup.text
    assert len(dup.json()["settings"]["strictKnownMarketplaces"]) == 32
    too_many = client.put(
        f"/api/v1/policies/{ok.json()['id']}",
        json={"settings": _mk(*[f"https://m{i}.example" for i in range(33)]), "reason": "33"},
    )
    assert too_many.status_code == 422, too_many.text

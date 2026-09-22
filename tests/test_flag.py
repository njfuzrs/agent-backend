"""flag 模块：下发契约 / 门禁 / 审计 / 全量替换语义。

规划 §4 M2 验收：
- `GET /ctl/flags` 无认证可读，返回**扁平** JSON，类型是原生 JSON（true 不是 "true"）
- 客户端能感知删除：停用 / 删除后该 key 从下发里消失
- key 门禁：非 snake_case → 422；命中 bypass/disable_sandbox 等词 → 422
- 管理台写口要 cookie 会话，无会话 → 401
- 每次变更都有审计行，且真删后审计仍在
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# 必须在 import app 之前设好必填配置（同 test_identity）。
os.environ.setdefault("AUTH_PASSWORD", "ci-not-a-secret")
os.environ.setdefault("UPLOAD_TOKEN", "ci-not-a-secret")

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture
def client(tmp_path, monkeypatch):
    """每个用例独立 SQLite：改 settings 原地字段，不重建对象（各模块已持有引用）。"""
    db_path = tmp_path / "flag.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.core import db as db_mod
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", db_url)

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


def _create(client, key: str, value, description: str = "", reason: str = "test"):
    return client.post(
        "/api/v1/flags",
        json={"key": key, "value": value, "description": description, "reason": reason},
    )


# ---------------------------------------------------------------------------
# 下发契约（客户端 feature-flags.ts）
# ---------------------------------------------------------------------------
def test_serve_flags_is_public_and_empty_by_default(client):
    """无认证可读；空库返回 `{}` 而不是 401/404 —— 客户端非 2xx 会直接 return。"""
    resp = client.get("/api/v1/ctl/flags")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {}


def test_serve_flags_sets_cache_control(client):
    """与 PR-2.1 nginx 静态文件口径一致，挡住客户端集体冷启动打库。"""
    resp = client.get("/api/v1/ctl/flags")
    assert "max-age=300" in resp.headers.get("cache-control", "")


def test_serve_flags_is_flat_with_native_json_types(client):
    """扁平 JSON + 原生类型。

    契约两条同时验：
    - 顶层就是 flag 字典，不能包在 `{"flags": ...}` 里（客户端直接 Object.entries）
    - `true` 必须是 JSON bool，不能是 `"true"` / `1` —— 客户端 `=== true` 会判错
    """
    _login(client)
    assert _create(client, "content_tracing", True).status_code == 201
    assert _create(client, "max_turns_limit", 40).status_code == 201
    assert _create(client, "release_channel", "stable").status_code == 201
    assert _create(client, "event_sampling_config", {"tool_use": 0.5}).status_code == 201

    payload = client.get("/api/v1/ctl/flags").json()
    assert set(payload) == {
        "content_tracing",
        "max_turns_limit",
        "release_channel",
        "event_sampling_config",
    }, "响应必须扁平，不得包一层"
    assert payload["content_tracing"] is True
    # 不能退化成 bool：Python 里 isinstance(True, int) 也成立，所以显式排除
    assert payload["max_turns_limit"] == 40
    assert not isinstance(payload["max_turns_limit"], bool)
    assert payload["release_channel"] == "stable"
    assert payload["event_sampling_config"] == {"tool_use": 0.5}


def test_false_value_survives_roundtrip(client):
    """`false` 不能在存取之间变成 `0` 或消失 —— killswitch 类 flag 全靠它。"""
    _login(client)
    assert _create(client, "sink_killswitch", False).status_code == 201
    payload = client.get("/api/v1/ctl/flags").json()
    assert payload["sink_killswitch"] is False


def test_wire_format_is_json_literals_not_strings(client):
    """看原始响应体，不看 .json() —— 客户端读的是线上的字节。

    `.json()` 会把 `"true"` 和 `true` 都解析成可比较的值，容易漏掉
    「bool 被当字符串下发」这类错误。这里直接断言文本里出现 JSON 字面量。
    """
    _login(client)
    _create(client, "content_tracing", True)
    _create(client, "max_turns_limit", 40)
    body = client.get("/api/v1/ctl/flags").text
    assert '"content_tracing":true' in body.replace(" ", ""), body
    assert '"max_turns_limit":40' in body.replace(" ", ""), body


def test_disable_removes_key_from_delivery(client):
    """停用 → key 从下发消失 → 客户端 remoteValues.clear() 后不再有它 → 回落默认值。

    这是「必须能感知删除」这条契约的一半：不是发 `null`，是**不发这个 key**。
    """
    _login(client)
    _create(client, "content_tracing", False)
    assert "content_tracing" in client.get("/api/v1/ctl/flags").json()

    resp = client.post("/api/v1/flags/content_tracing/disable", params={"reason": "灰度结束"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["disabled"] is True

    assert "content_tracing" not in client.get("/api/v1/ctl/flags").json()

    # 管理台仍看得见（关掉但没删）
    keys = {f["key"] for f in client.get("/api/v1/flags").json()["items"]}
    assert "content_tracing" in keys

    # 重新启用后又出现
    assert client.post("/api/v1/flags/content_tracing/enable").status_code == 200
    assert "content_tracing" in client.get("/api/v1/ctl/flags").json()


def test_delete_removes_key_from_delivery(client):
    _login(client)
    _create(client, "content_tracing", True)
    resp = client.delete("/api/v1/flags/content_tracing", params={"reason": "不要了"})
    assert resp.status_code == 200, resp.text
    assert client.get("/api/v1/ctl/flags").json() == {}


def test_update_changes_delivered_value(client):
    _login(client)
    _create(client, "max_turns_limit", 20)
    resp = client.put(
        "/api/v1/flags/max_turns_limit",
        json={"value": 5, "description": "收紧", "reason": "事故复盘"},
    )
    assert resp.status_code == 200, resp.text
    assert client.get("/api/v1/ctl/flags").json()["max_turns_limit"] == 5


# ---------------------------------------------------------------------------
# 门禁（规划 PR-2.3）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "key",
    ["Has-Dash", "has.dot", "9leading", "has space", "UPPER_CASE", "trailing-"],
)
def test_non_snake_case_key_is_422(client, key):
    """非法标识符会让客户端 SID_CODE_FLAG_<KEY> 覆盖路径静默失效。"""
    _login(client)
    assert _create(client, key, True).status_code == 422


@pytest.mark.parametrize(
    "key",
    [
        "disable_sandbox",
        "bypass_permissions",
        "allow_bypass_mode",
        "disable_all_hooks",
        "dangerously_skip_permissions",
        "unsafe_exec",
        "no_sandbox",
        "god_mode",
    ],
)
def test_permission_widening_key_is_422(client, key):
    """无认证下发通道只接受「施加约束」类 flag。放宽类走 M3 policy。"""
    _login(client)
    resp = _create(client, key, True)
    assert resp.status_code == 422, resp.text


def test_permission_widening_description_is_422(client):
    _login(client)
    resp = _create(client, "feature_x", True, description="临时 bypass 权限检查")
    assert resp.status_code == 422, resp.text


def test_rejected_flag_is_not_persisted(client):
    """被门禁拒的 flag 不能留在库里 —— 否则下次改下发逻辑就漏出去了。"""
    _login(client)
    _create(client, "disable_sandbox", True)
    assert client.get("/api/v1/ctl/flags").json() == {}
    assert client.get("/api/v1/flags").json()["items"] == []


def test_update_path_key_also_goes_through_guard(client):
    """PUT 的 key 来自 URL，同样过门禁 —— 否则能绕过创建时的校验去改违规 flag。"""
    _login(client)
    resp = client.put(
        "/api/v1/flags/disable_sandbox",
        json={"value": True, "reason": "试图绕过"},
    )
    assert resp.status_code == 422, resp.text


def test_duplicate_key_is_409(client):
    _login(client)
    assert _create(client, "content_tracing", True).status_code == 201
    assert _create(client, "content_tracing", False).status_code == 409


def test_update_missing_flag_is_404(client):
    _login(client)
    resp = client.put("/api/v1/flags/never_created", json={"value": True})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 鉴权分链
# ---------------------------------------------------------------------------
def test_admin_writes_require_session(client):
    """管理台写口无 cookie 会话 → 401。下发端点无认证是有意的，写口不是。"""
    body = {"key": "content_tracing", "value": True}
    for method, path in [
        ("post", "/api/v1/flags"),
        ("put", "/api/v1/flags/content_tracing"),
        ("delete", "/api/v1/flags/content_tracing"),
        ("post", "/api/v1/flags/content_tracing/disable"),
        ("post", "/api/v1/flags/content_tracing/enable"),
    ]:
        # httpx 的 delete() 不收 json 参数，统一走 client.request
        resp = client.request(method.upper(), path, json=None if method == "delete" else body)
        assert resp.status_code == 401, f"{method.upper()} {path} → {resp.status_code}"


def test_admin_reads_require_session(client):
    for path in ["/api/v1/flags", "/api/v1/flags/audit"]:
        assert client.get(path).status_code == 401, path


def test_upload_token_cannot_write_flags(client):
    """纪律一：控制面绝不复用 X-Upload-Token。带上传 token 也进不了 flag 写口。"""
    from app.core.config import settings

    resp = client.post(
        "/api/v1/flags",
        json={"key": "content_tracing", "value": True},
        headers={"X-Upload-Token": settings.data_plane.UPLOAD_TOKEN},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------
def test_audit_records_every_change(client):
    _login(client)
    _create(client, "max_turns_limit", 20, reason="初始值")
    client.put("/api/v1/flags/max_turns_limit", json={"value": 5, "reason": "收紧"})
    client.post("/api/v1/flags/max_turns_limit/disable", params={"reason": "先停"})

    items = client.get("/api/v1/flags/audit", params={"key": "max_turns_limit"}).json()["items"]
    actions = [a["action"] for a in items]
    assert actions == ["disable", "update", "create"], actions  # 倒序

    update = next(a for a in items if a["action"] == "update")
    assert update["old_value"] == 20 and update["new_value"] == 5
    assert update["reason"] == "收紧"

    from app.core.config import settings

    assert all(a["actor"] == settings.AUTH_USERNAME for a in items)


def test_audit_survives_flag_deletion(client):
    """真删 flag 后审计行必须还在 —— 否则「谁删的」跟着被删的行消失。"""
    _login(client)
    _create(client, "content_tracing", True, reason="上线")
    client.delete("/api/v1/flags/content_tracing", params={"reason": "下线"})

    items = client.get("/api/v1/flags/audit", params={"key": "content_tracing"}).json()["items"]
    actions = [a["action"] for a in items]
    assert "delete" in actions and "create" in actions, actions
    deleted = next(a for a in items if a["action"] == "delete")
    assert deleted["old_value"] is True
    assert deleted["reason"] == "下线"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

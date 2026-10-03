"""P1 管理台飞书登录：方案 §7 P1 行的全部反向用例。

飞书三个端点都打桩（替换 service/feishu.py 的两个函数），不连真实租户。
- 未登录访问管理端点 401；member 访问 403
- state 重放 / 过期被拒；state 与浏览器 nonce 不匹配被拒
- 用户拒绝授权（error=access_denied）有明确提示
- 其他租户被拒
- 吊销后已有会话下一次请求即 401；被吊销用户重新登录被拒
- 关闭口令登录后口令登录 404，且已发出的口令会话失效
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

os.environ.setdefault("AUTH_PASSWORD", "ci-not-a-secret")
os.environ.setdefault("UPLOAD_TOKEN", "ci-not-a-secret")

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

TENANT = "tenant-test"
ADMIN_UNION = "on_admin"
MEMBER_UNION = "on_member"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "auth.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.core import db as db_mod
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", db_url)
    login = settings.login
    monkeypatch.setattr(login, "PUBLIC_BASE_URL", "https://example.test/traj")
    monkeypatch.setattr(login, "ADMIN_UI_BASE_URL", "")
    monkeypatch.setattr(login, "FEISHU_APP_ID", "cli_test")
    monkeypatch.setattr(login, "FEISHU_APP_SECRET", "test-secret")
    monkeypatch.setattr(login, "FEISHU_TENANT_KEY", TENANT)
    monkeypatch.setattr(login, "ADMIN_BOOTSTRAP_UNION_IDS", ADMIN_UNION)
    monkeypatch.setattr(login, "AUTH_PASSWORD_LOGIN_ENABLED", True)

    engine = create_async_engine(db_url, echo=False)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "async_session", session_factory)

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(cfg, "head")

    # 飞书打桩：code 形如 "<union_id>@<tenant>"，换出来的 token 原样带着身份
    from app.modules.auth.service import feishu

    calls = {"exchange": []}

    async def fake_exchange(code: str, verifier: str) -> str:
        calls["exchange"].append((code, verifier))
        if code == "bad":
            raise feishu.FeishuError("token", "20003")
        return "tok:" + code

    async def fake_user_info(token: str) -> feishu.FeishuUser:
        union_id, tenant = token[4:].split("@")
        return feishu.FeishuUser(tenant_key=tenant, union_id=union_id, open_id="ou_" + union_id,
                                 name=union_id, email="")

    monkeypatch.setattr(feishu, "exchange_code", fake_exchange)
    monkeypatch.setattr(feishu, "fetch_user_info", fake_user_info)

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, raise_server_exceptions=False, base_url="https://example.test") as tc:
        tc.calls = calls
        tc.db_path = db_path
        yield tc

    engine.sync_engine.dispose()


def _start(client, redirect="/devices") -> str:
    """走 start，返回 state。nonce cookie 留在 client 的 cookie jar 里。"""
    resp = client.get("/api/v1/auth/feishu/start", params={"redirect": redirect}, follow_redirects=False)
    assert resp.status_code == 302, resp.text
    loc = urlparse(resp.headers["location"])
    assert loc.netloc == "accounts.feishu.cn"
    q = parse_qs(loc.query)
    assert q["client_id"] == ["cli_test"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == ["https://example.test/traj/api/v1/auth/feishu/callback"]
    # app_secret 绝不出现在跳转地址里
    assert "test-secret" not in resp.headers["location"]
    return q["state"][0]


def _callback(client, **params):
    return client.get("/api/v1/auth/feishu/callback", params=params, follow_redirects=False)


def _feishu_login(client, union_id: str, tenant: str = TENANT, redirect="/devices"):
    state = _start(client, redirect)
    return _callback(client, code=f"{union_id}@{tenant}", state=state)


def _error_of(resp) -> str:
    assert resp.status_code == 302, resp.text
    loc = urlparse(resp.headers["location"])
    assert loc.path == "/traj/login"
    return parse_qs(loc.query)["error"][0]


def _db(client):
    return sqlite3.connect(client.db_path)


# ---------------------------------------------------------------------------
# 主路径
# ---------------------------------------------------------------------------
def test_bootstrap_admin_logs_in_and_reaches_admin_api(client):
    resp = _feishu_login(client, ADMIN_UNION)
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://example.test/traj/devices"
    me = client.get("/api/v1/auth/me").json()
    assert me["kind"] == "user" and me["role"] == "admin" and me["is_admin"] is True
    assert me["username"] == f"user:{ADMIN_UNION}"
    assert client.get("/api/v1/identity/organizations").status_code == 200
    # 轨迹接口（数据面 verify_basic_auth 的 cookie 分支）同样放行 admin
    assert client.get("/api/v1/trajectories").status_code == 200
    # PKCE：换 token 时带的 verifier 与 start 时的 challenge 对得上
    import base64
    import hashlib

    _code, verifier = client.calls["exchange"][-1]
    assert len(verifier) >= 43
    assert base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")


def test_login_writes_audit_and_does_not_store_token(client):
    _feishu_login(client, ADMIN_UNION)
    with _db(client) as conn:
        events = [r[0] for r in conn.execute("SELECT event FROM auth_audit")]
        dump = "\n".join(str(r) for r in conn.iterdump())
    assert "login" in events
    # P1 不保存飞书 token；state 只存 hash
    assert "tok:" not in dump


def test_unauthenticated_admin_endpoint_is_401(client):
    assert client.get("/api/v1/identity/organizations").status_code == 401
    assert client.get("/api/v1/users").status_code == 401


def test_member_gets_403_everywhere_but_me(client):
    _feishu_login(client, MEMBER_UNION)
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["is_admin"] is False
    assert client.get("/api/v1/identity/organizations").status_code == 403
    assert client.get("/api/v1/trajectories").status_code == 403
    assert client.get("/api/v1/users").status_code == 403
    # member 不能把自己改成 admin
    with _db(client) as conn:
        (uid,) = conn.execute("SELECT id FROM users WHERE union_id=?", (MEMBER_UNION,)).fetchone()
    assert client.patch(f"/api/v1/users/{uid}/role", json={"role": "admin"}).status_code == 403


def test_redirect_param_cannot_leave_site(client):
    for evil in ("//evil.example", "https://evil.example", "/\\evil.example"):
        client.cookies.clear()
        resp = _feishu_login(client, ADMIN_UNION, redirect=evil)
        assert resp.headers["location"] == "https://example.test/traj/"


# ---------------------------------------------------------------------------
# state / nonce
# ---------------------------------------------------------------------------
def test_state_replay_is_rejected(client):
    state = _start(client)
    nonce = client.cookies.get("traj_login_nonce")
    first = _callback(client, code=f"{ADMIN_UNION}@{TENANT}", state=state)
    assert first.headers["location"].endswith("/devices")
    client.cookies.clear()
    client.cookies.set("traj_login_nonce", nonce, domain="example.test")
    assert _error_of(_callback(client, code=f"{ADMIN_UNION}@{TENANT}", state=state)) == "invalid_state"


def test_expired_state_is_rejected(client):
    state = _start(client)
    with _db(client) as conn:
        conn.execute("UPDATE auth_states SET expires_at='2000-01-01T00:00:00+00:00'")
    assert _error_of(_callback(client, code=f"{ADMIN_UNION}@{TENANT}", state=state)) == "invalid_state"
    assert client.calls["exchange"] == []


def test_state_from_another_browser_is_rejected(client):
    """登录 CSRF：攻击者拿自己的 state + code 让管理员的浏览器回调。管理员浏览器没有匹配的 nonce。"""
    state = _start(client)
    # 先清空：start 下发的正确 nonce 还在 jar 里（domain=example.test），直接 set 会多出一个
    # 同名 cookie，发哪个取决于 cookiejar 实现（3.10 与 3.13 不同），测试就成了碰运气。
    client.cookies.clear()
    client.cookies.set("traj_login_nonce", "attacker-browser-nonce", domain="example.test")
    assert _error_of(_callback(client, code=f"{MEMBER_UNION}@{TENANT}", state=state)) == "invalid_state"
    client.cookies.clear()
    assert _error_of(_callback(client, code=f"{MEMBER_UNION}@{TENANT}", state=state)) == "invalid_state"
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.calls["exchange"] == []


def test_start_on_other_host_bounces_to_canonical_host(client):
    """从 sid-code.cc 发起、回调落在 www.sid-code.cc：nonce cookie 按主机隔离，回调必然带不上。
    start 必须先把浏览器送到 PUBLIC_BASE_URL 的主机上，再建 state、下发 nonce。"""
    resp = client.get(
        "https://other.example.test/api/v1/auth/feishu/start",
        params={"redirect": "/devices"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://example.test/traj/api/v1/auth/feishu/start?redirect=%2Fdevices"
    assert "traj_login_nonce" not in resp.headers.get("set-cookie", "")
    with _db(client) as conn:
        assert conn.execute("SELECT count(*) FROM auth_states").fetchone()[0] == 0


def test_unknown_state_is_rejected(client):
    _start(client)
    assert _error_of(_callback(client, code=f"{ADMIN_UNION}@{TENANT}", state="forged")) == "invalid_state"


# ---------------------------------------------------------------------------
# 飞书侧失败
# ---------------------------------------------------------------------------
def test_user_denied_authorization(client):
    state = _start(client)
    assert _error_of(_callback(client, error="access_denied", state=state)) == "access_denied"
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.calls["exchange"] == []


def test_feishu_exchange_failure(client):
    state = _start(client)
    assert _error_of(_callback(client, code="bad", state=state)) == "feishu_failed"
    assert client.get("/api/v1/auth/me").status_code == 401


def test_other_tenant_is_rejected(client):
    assert _error_of(_feishu_login(client, ADMIN_UNION, tenant="other-tenant")) == "tenant_mismatch"
    assert client.get("/api/v1/auth/me").status_code == 401
    with _db(client) as conn:
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 0


def test_feishu_disabled_returns_503(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings.login, "FEISHU_APP_SECRET", "")
    assert client.get("/api/v1/auth/feishu/start", follow_redirects=False).status_code == 503
    assert client.get("/api/v1/auth/options").json() == {"feishu_enabled": False, "password_enabled": True}


# ---------------------------------------------------------------------------
# 吊销 / 角色
# ---------------------------------------------------------------------------
def _uid(client, union_id):
    with _db(client) as conn:
        return conn.execute("SELECT id FROM users WHERE union_id=?", (union_id,)).fetchone()[0]


def test_revoked_user_session_dies_on_next_request(client):
    from fastapi.testclient import TestClient

    from app.main import app

    _feishu_login(client, ADMIN_UNION)
    with TestClient(app, base_url="https://example.test") as other:
        other.calls = client.calls
        other.db_path = client.db_path
        _feishu_login(other, MEMBER_UNION)
        member_id = _uid(client, MEMBER_UNION)
        # 先提权成 admin，确认能用
        assert client.patch(f"/api/v1/users/{member_id}/role", json={"role": "admin"}).status_code == 200
        assert other.get("/api/v1/identity/organizations").status_code == 200
        # 吊销：已有会话下一次请求即 401
        assert client.post(f"/api/v1/users/{member_id}/revoke").json()["status"] == "revoked"
        assert other.get("/api/v1/identity/organizations").status_code == 401
        # 重新登录被拒
        other.cookies.clear()
        assert _error_of(_feishu_login(other, MEMBER_UNION)) == "revoked"
        # 恢复后能再登录
        client.post(f"/api/v1/users/{member_id}/restore")
        other.cookies.clear()
        assert _feishu_login(other, MEMBER_UNION).headers["location"].endswith("/devices")

    with _db(client) as conn:
        events = [r[0] for r in conn.execute("SELECT event FROM auth_audit ORDER BY id")]
        actors = {r[0] for r in conn.execute("SELECT actor FROM auth_audit WHERE event='revoke'")}
    for e in ("role_change", "revoke", "login_rejected", "restore"):
        assert e in events
    assert actors == {f"user:{ADMIN_UNION}"}


def test_admin_cannot_demote_or_revoke_self(client):
    _feishu_login(client, ADMIN_UNION)
    me = _uid(client, ADMIN_UNION)
    assert client.patch(f"/api/v1/users/{me}/role", json={"role": "member"}).status_code == 409
    assert client.post(f"/api/v1/users/{me}/revoke").status_code == 409


def test_bootstrap_restores_admin_on_login(client):
    _feishu_login(client, ADMIN_UNION)
    with _db(client) as conn:
        conn.execute("UPDATE users SET role='member'")
    client.cookies.clear()
    _feishu_login(client, ADMIN_UNION)
    assert client.get("/api/v1/auth/me").json()["role"] == "admin"


def test_new_user_outside_bootstrap_is_member(client):
    _feishu_login(client, MEMBER_UNION)
    with _db(client) as conn:
        assert conn.execute("SELECT role FROM users").fetchone()[0] == "member"


# ---------------------------------------------------------------------------
# 口令应急入口
# ---------------------------------------------------------------------------
def _password_login(client):
    from app.core.config import settings

    return client.post(
        "/api/v1/auth/login",
        json={"username": settings.AUTH_USERNAME, "password": settings.AUTH_PASSWORD},
    )


def test_password_login_writes_break_glass_audit(client):
    assert _password_login(client).status_code == 200
    assert client.get("/api/v1/identity/organizations").status_code == 200
    with _db(client) as conn:
        assert [r[0] for r in conn.execute("SELECT event FROM auth_audit")] == ["break_glass"]


def test_password_login_disabled_is_404_and_kills_sessions(client, monkeypatch):
    from app.core.config import settings

    assert _password_login(client).status_code == 200
    monkeypatch.setattr(settings.login, "AUTH_PASSWORD_LOGIN_ENABLED", False)
    assert client.get("/api/v1/identity/organizations").status_code == 401
    assert _password_login(client).status_code == 404
    assert client.get("/api/v1/auth/options").json()["password_enabled"] is False


def test_basic_auth_for_scripts_survives_password_page_off(client, monkeypatch):
    """关掉的是浏览器登录页，脚本 / curl 的 HTTP Basic 照常可用（运维脚本依赖它）。"""
    from app.core.config import settings

    monkeypatch.setattr(settings.login, "AUTH_PASSWORD_LOGIN_ENABLED", False)
    resp = client.get("/api/v1/trajectories", auth=(settings.AUTH_USERNAME, settings.AUTH_PASSWORD))
    assert resp.status_code == 200


def test_legacy_cookie_without_prefix_is_invalid(client):
    """P1 之前签发的 cookie 形状是 <username>.<expiry>.<sig>，升级后一律当无效。"""
    import hashlib
    import hmac
    import time

    from app.core.auth.session import signing_key

    payload = f"admin.{int(time.time()) + 3600}"
    sig = hmac.new(signing_key(), payload.encode(), hashlib.sha256).hexdigest()
    client.cookies.set("traj_session", f"{payload}.{sig}")
    assert client.get("/api/v1/auth/me").status_code == 401

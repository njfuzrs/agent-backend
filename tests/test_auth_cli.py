"""P2 CLI 飞书登录：start → callback(kind=cli) → exchange，及方案 §5.2 的反向用例。

飞书打桩沿用 test_auth_login 的 client fixture（code 形如 "<union_id>@<tenant>"）。
- 回跳只去 127.0.0.1:<port>，参数不合格 400
- 登录码 60 秒、一次性；verifier / device_id 不对被拒且不消费
- 设备已绑给另一个在职用户 → 409，不吊销对方凭据；登出解绑后可换人
- 吊销用户连带吊销其设备凭据，CLI 下一次请求 401
- 注册码不能覆盖已绑人的设备
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

from test_auth_login import ADMIN_UNION, MEMBER_UNION, TENANT, _db, _error_of, _feishu_login, _uid, client  # noqa: F401

PORT = 43123


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _cli_start(client, device_id="dev-cli", challenge=None, cli_state="cli-state-0123456789", port=PORT):
    params = {"port": port, "challenge": challenge or _pkce()[1], "cli_state": cli_state, "device_id": device_id}
    return client.get("/api/v1/auth/feishu/cli/start", params=params, follow_redirects=False)


def _cli_login(client, union_id, device_id="dev-cli", tenant=TENANT):
    """走完 start + callback，返回 (回跳 CLI 的 query, verifier)。"""
    verifier, challenge = _pkce()
    resp = _cli_start(client, device_id=device_id, challenge=challenge)
    assert resp.status_code == 302, resp.text
    state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]
    cb = client.get(
        "/api/v1/auth/feishu/callback", params={"code": f"{union_id}@{tenant}", "state": state},
        follow_redirects=False,
    )
    assert cb.status_code == 302, cb.text
    loc = urlparse(cb.headers["location"])
    assert (loc.scheme, loc.netloc, loc.path) == ("http", f"127.0.0.1:{PORT}", "/callback")
    q = {k: v[0] for k, v in parse_qs(loc.query).items()}
    assert q["state"] == "cli-state-0123456789"
    return q, verifier


def _exchange(client, code, verifier, device_id="dev-cli"):
    return client.post(
        "/api/v1/auth/cli/exchange",
        json={"code": code, "verifier": verifier, "device_id": device_id, "platform": "darwin", "ver": "1.0"},
    )


def _login_device(client, union_id, device_id="dev-cli") -> dict:
    q, verifier = _cli_login(client, union_id, device_id)
    resp = _exchange(client, q["code"], verifier, device_id)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _whoami(client, cred):
    return client.get("/api/v1/ctl/whoami", headers={"Authorization": f"Bearer {cred}"})


# ---------------------------------------------------------------------------
# 主路径
# ---------------------------------------------------------------------------
def test_cli_login_end_to_end_binds_device_to_user(client):
    body = _login_device(client, MEMBER_UNION)
    assert body["user"]["union_id"] == MEMBER_UNION
    assert body["device_id"] == "dev-cli" and body["org_id"] == "default"
    who = _whoami(client, body["credential"])
    assert who.status_code == 200
    assert who.json()["user_ref"] == _uid(client, MEMBER_UNION)
    with _db(client) as conn:
        events = [r[0] for r in conn.execute("SELECT event FROM auth_audit ORDER BY id")]
        dump = "\n".join(str(r) for r in conn.iterdump())
    assert events == ["cli_login", "cli_exchange"]
    # 凭据、登录码、飞书 token 都不落明文
    assert body["credential"] not in dump
    assert "tok:" not in dump
    # CLI 流程不签管理台会话
    assert client.get("/api/v1/auth/me").status_code == 401


def test_cli_callback_uses_feishu_pkce(client):
    _cli_login(client, MEMBER_UNION)
    _code, verifier = client.calls["exchange"][-1]
    assert len(verifier) >= 43


def test_user_list_shows_device_count(client):
    _login_device(client, MEMBER_UNION)
    client.cookies.clear()
    _feishu_login(client, ADMIN_UNION)
    items = {u["union_id"]: u for u in client.get("/api/v1/users").json()["items"]}
    assert items[MEMBER_UNION]["device_count"] == 1
    assert items[ADMIN_UNION]["device_count"] == 0
    devices = client.get("/api/v1/identity/devices").json()["items"]
    assert devices[0]["user_ref"] == _uid(client, MEMBER_UNION)


# ---------------------------------------------------------------------------
# start 参数 / 回跳地址
# ---------------------------------------------------------------------------
def test_cli_start_rejects_bad_params(client):
    for kw in (
        {"port": 80},
        {"port": 70000},
        {"challenge": "short"},
        {"cli_state": "x"},
        {"device_id": "dev/../evil"},
    ):
        assert _cli_start(client, **kw).status_code == 400, kw
    resp = client.get("/api/v1/auth/feishu/cli/start", params={"port": "evil.example", "challenge": "a" * 43,
                                                               "cli_state": "s" * 16, "device_id": "d"},
                      follow_redirects=False)
    assert resp.status_code == 422


def test_cli_start_bounces_to_canonical_host(client):
    resp = client.get(
        "https://other.test/api/v1/auth/feishu/cli/start",
        params={"port": PORT, "challenge": _pkce()[1], "cli_state": "cli-state-0123456789", "device_id": "dev-cli"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    loc = urlparse(resp.headers["location"])
    assert loc.netloc == "example.test" and loc.path == "/traj/api/v1/auth/feishu/cli/start"


def test_cli_callback_failures_return_to_cli(client):
    verifier, challenge = _pkce()
    state = parse_qs(urlparse(_cli_start(client, challenge=challenge).headers["location"]).query)["state"][0]
    cb = client.get("/api/v1/auth/feishu/callback", params={"error": "access_denied", "state": state},
                    follow_redirects=False)
    loc = urlparse(cb.headers["location"])
    assert loc.netloc == f"127.0.0.1:{PORT}"
    q = parse_qs(loc.query)
    assert q["error"] == ["access_denied"] and "code" not in q

    # 其他租户
    state = parse_qs(urlparse(_cli_start(client, challenge=challenge).headers["location"]).query)["state"][0]
    cb = client.get("/api/v1/auth/feishu/callback", params={"code": f"{MEMBER_UNION}@other", "state": state},
                    follow_redirects=False)
    assert parse_qs(urlparse(cb.headers["location"]).query)["error"] == ["tenant_mismatch"]


def test_cli_state_needs_browser_nonce(client):
    resp = _cli_start(client)
    state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]
    client.cookies.clear()
    cb = client.get("/api/v1/auth/feishu/callback", params={"code": f"{MEMBER_UNION}@{TENANT}", "state": state},
                    follow_redirects=False)
    # state 不可信时连端口都不可信：回管理台登录页，不回 127.0.0.1
    assert _error_of(cb) == "invalid_state"


# ---------------------------------------------------------------------------
# exchange
# ---------------------------------------------------------------------------
def test_login_code_is_single_use(client):
    q, verifier = _cli_login(client, MEMBER_UNION)
    assert _exchange(client, q["code"], verifier).status_code == 200
    assert _exchange(client, q["code"], verifier).status_code == 401


def test_wrong_verifier_or_device_is_rejected_without_burning_code(client):
    q, verifier = _cli_login(client, MEMBER_UNION)
    other_verifier, _ = _pkce()
    assert _exchange(client, q["code"], other_verifier).status_code == 401
    assert _exchange(client, q["code"], verifier, device_id="dev-other").status_code == 401
    # 截到码的人没换成，真正的 CLI 仍能兑换
    assert _exchange(client, q["code"], verifier).status_code == 200


def test_expired_login_code_is_rejected(client):
    q, verifier = _cli_login(client, MEMBER_UNION)
    with _db(client) as conn:
        conn.execute("UPDATE login_codes SET expires_at='2000-01-01T00:00:00+00:00'")
    assert _exchange(client, q["code"], verifier).status_code == 401


def test_user_revoked_between_callback_and_exchange(client):
    q, verifier = _cli_login(client, MEMBER_UNION)
    with _db(client) as conn:
        conn.execute("UPDATE users SET status='revoked' WHERE union_id=?", (MEMBER_UNION,))
    assert _exchange(client, q["code"], verifier).status_code == 401


def test_relogin_same_user_rotates_credential(client):
    first = _login_device(client, MEMBER_UNION)
    second = _login_device(client, MEMBER_UNION)
    assert _whoami(client, first["credential"]).status_code == 401
    assert _whoami(client, second["credential"]).status_code == 200


def test_device_bound_to_other_active_user_is_409(client):
    owner = _login_device(client, MEMBER_UNION)
    q, verifier = _cli_login(client, "on_intruder")
    assert _exchange(client, q["code"], verifier).status_code == 409
    # 不重绑、不吊销原主人的凭据
    who = _whoami(client, owner["credential"])
    assert who.status_code == 200 and who.json()["user_ref"] == _uid(client, MEMBER_UNION)
    with _db(client) as conn:
        assert "cli_conflict" in [r[0] for r in conn.execute("SELECT event FROM auth_audit")]


def test_logout_unbinds_so_another_user_can_login(client):
    owner = _login_device(client, MEMBER_UNION)
    resp = client.post("/api/v1/auth/cli/logout", headers={"Authorization": f"Bearer {owner['credential']}"})
    assert resp.status_code == 200
    assert _whoami(client, owner["credential"]).status_code == 401
    assert client.post("/api/v1/auth/cli/logout").status_code == 401
    other = _login_device(client, "on_newcomer")
    assert _whoami(client, other["credential"]).json()["user_ref"] == _uid(client, "on_newcomer")


def test_revoking_user_revokes_device_credentials(client):
    from fastapi.testclient import TestClient

    from app.main import app

    member = _login_device(client, MEMBER_UNION, device_id="dev-a")
    member_b = _login_device(client, MEMBER_UNION, device_id="dev-b")
    with TestClient(app, base_url="https://example.test") as admin:
        admin.calls = client.calls
        _feishu_login(admin, ADMIN_UNION)
        uid = _uid(client, MEMBER_UNION)
        assert admin.post(f"/api/v1/users/{uid}/revoke").status_code == 200
        assert _whoami(client, member["credential"]).status_code == 401
        assert _whoami(client, member_b["credential"]).status_code == 401
        # 被吊销的人不算在职：别人可以接手这台设备
        taken = _login_device(client, "on_newcomer", device_id="dev-a")
        assert _whoami(client, taken["credential"]).status_code == 200
        # 恢复不恢复旧凭据
        admin.post(f"/api/v1/users/{uid}/restore")
        assert _whoami(client, member_b["credential"]).status_code == 401
    with _db(client) as conn:
        (detail,) = conn.execute("SELECT detail_json FROM auth_audit WHERE event='revoke'").fetchone()
    assert '"credentials_revoked": 2' in detail


def test_enroll_code_cannot_take_over_bound_device(client, monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.config import settings
    from app.main import app

    monkeypatch.setattr(settings.control_plane, "CTL_ENROLL_ENABLED", True)
    owner = _login_device(client, MEMBER_UNION)
    with TestClient(app, base_url="https://example.test") as admin:
        admin.calls = client.calls
        _feishu_login(admin, ADMIN_UNION)
        code = admin.post("/api/v1/identity/enroll-codes", json={"org_id": "default"}).json()["code"]
        resp = admin.post("/api/v1/ctl/enroll", headers={"X-Enroll-Token": code}, json={"device_id": "dev-cli"})
    assert resp.status_code == 409
    assert _whoami(client, owner["credential"]).status_code == 200


def test_feishu_disabled_cli_endpoints_503(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings.login, "FEISHU_APP_ID", "")
    assert _cli_start(client).status_code == 503
    assert _exchange(client, "x", "v" * 43).status_code == 503

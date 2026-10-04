"""P3 身份落账：事件 / 账本的 user_ref 从设备凭据取，管理台能按人看会话与花费。

方案 §5.6 / §7 P3 行：
- CLI 登录过的设备上报，events / usage_ledger 带上 user_ref
- 客户端在 body 里伪造 user_ref / user_id **不影响**入库结果
- 注册码设备（未登录）user_ref 为 NULL，按人聚合里合成「未登录」一行，总数对得上
- 设备换人后，历史行仍记在当时的人头上（不回填、不跟着 devices.user_ref 走）
- 飞书会话的管理写操作，审计 actor 是 user:<union_id>
"""

from __future__ import annotations

from datetime import datetime, timezone

from test_auth_cli import _login_device
from test_auth_login import ADMIN_UNION, MEMBER_UNION, _db, _feishu_login, _uid, client  # noqa: F401

_NOW = datetime.now(timezone.utc)
TS = int(_NOW.replace(hour=12, minute=0, second=0, microsecond=0).timestamp())
TODAY = _NOW.strftime("%Y-%m-%d")


def _bearer(cred: str) -> dict:
    return {"Authorization": f"Bearer {cred}"}


def _event(session_id="s-1", ts=1_780_557_354_650, **meta) -> dict:
    return {
        "eventName": "tool_call",
        "timestamp": ts,
        "metadata": {"_ctx_session_id": session_id, "tool_name": "Bash", **meta},
    }


def _ledger(session_id="s-1", cost=0.5, **over) -> dict:
    body = {
        "ts": TS, "sessionId": session_id, "model": "m", "provider": "openai",
        "promptTotal": 100, "cacheHit": 0, "cacheWrite": 0, "uncachedInput": 100, "output": 10,
        "costUSD": cost, "savingsUSD": 0, "durationMs": 1000,
    }
    body.update(over)
    return body


def _post_events(client, cred, events):
    resp = client.post("/api/v1/events", json={"events": events}, headers=_bearer(cred))
    assert resp.status_code == 202, resp.text
    return resp.json()


def _post_ledger(client, cred, body):
    resp = client.post("/api/v1/usage/ledger", json=body, headers=_bearer(cred))
    assert resp.status_code == 200, resp.text


def _admin(client):
    _feishu_login(client, ADMIN_UNION)


# ---------------------------------------------------------------------------
# 入库
# ---------------------------------------------------------------------------
def test_logged_in_device_writes_user_ref(client):
    cred = _login_device(client, MEMBER_UNION)["credential"]
    uid = _uid(client, MEMBER_UNION)
    _post_events(client, cred, [_event()])
    _post_ledger(client, cred, _ledger())

    _admin(client)
    events = client.get("/api/v1/events").json()["items"]
    assert [e["user_ref"] for e in events] == [uid]
    ledger = client.get("/api/v1/usage/ledger").json()["items"]
    assert [r["user_ref"] for r in ledger] == [uid]


def test_body_user_fields_are_ignored(client):
    """伪造 user_ref / user_id / userRef 不影响入库：只认凭据。"""
    cred = _login_device(client, MEMBER_UNION)["credential"]
    uid = _uid(client, MEMBER_UNION)
    _admin(client)  # 让 admin 有 users.id，作为伪造目标
    admin_id = _uid(client, ADMIN_UNION)

    events = [{**_event(), "user_ref": admin_id, "userRef": admin_id, "user_id": "boss"}]
    _post_events(client, cred, events)
    _post_ledger(client, cred, _ledger(user_ref=admin_id, userRef=admin_id, userId="boss"))

    with _db(client) as conn:
        assert conn.execute("SELECT user_ref FROM events").fetchall() == [(uid,)]
        assert conn.execute("SELECT user_ref FROM usage_ledger").fetchall() == [(uid,)]


def test_enroll_code_device_has_null_user_ref(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings.control_plane, "CTL_ENROLL_ENABLED", True)
    _admin(client)
    code = client.post("/api/v1/identity/enroll-codes", json={"org_id": "default"}).json()["code"]
    resp = client.post(
        "/api/v1/ctl/enroll", headers={"X-Enroll-Token": code}, json={"device_id": "dev-anon", "user_id": "x"}
    )
    assert resp.status_code == 201, resp.text
    cred = resp.json()["credential"]
    _post_events(client, cred, [_event()])
    _post_ledger(client, cred, _ledger())
    with _db(client) as conn:
        assert conn.execute("SELECT user_ref FROM events").fetchall() == [(None,)]
        assert conn.execute("SELECT user_ref FROM usage_ledger").fetchall() == [(None,)]


def test_history_stays_with_previous_owner_after_device_changes_hands(client):
    member = _login_device(client, MEMBER_UNION, device_id="dev-shared")
    member_id = _uid(client, MEMBER_UNION)
    _post_events(client, member["credential"], [_event(session_id="s-old")])
    _post_ledger(client, member["credential"], _ledger(session_id="s-old"))
    assert client.post("/api/v1/auth/cli/logout", headers=_bearer(member["credential"])).status_code == 200

    other = _login_device(client, "on_newcomer", device_id="dev-shared")
    other_id = _uid(client, "on_newcomer")
    _post_events(client, other["credential"], [_event(session_id="s-new")])
    _post_ledger(client, other["credential"], _ledger(session_id="s-new"))

    with _db(client) as conn:
        ev = dict(conn.execute("SELECT session_id, user_ref FROM events").fetchall())
        led = dict(conn.execute("SELECT session_id, user_ref FROM usage_ledger").fetchall())
    assert ev == {"s-old": member_id, "s-new": other_id}
    assert led == {"s-old": member_id, "s-new": other_id}


# ---------------------------------------------------------------------------
# 管理台按人看
# ---------------------------------------------------------------------------
def test_filters_by_user_ref(client):
    a = _login_device(client, MEMBER_UNION, device_id="dev-a")["credential"]
    b = _login_device(client, "on_b", device_id="dev-b")["credential"]
    _post_events(client, a, [_event(session_id="sa")])
    _post_events(client, b, [_event(session_id="sb")])
    _post_ledger(client, a, _ledger(session_id="sa"))
    _post_ledger(client, b, _ledger(session_id="sb"))

    _admin(client)
    uid = _uid(client, MEMBER_UNION)
    events = client.get("/api/v1/events", params={"user_ref": uid}).json()
    assert events["total"] == 1 and events["items"][0]["session_id"] == "sa"
    ledger = client.get("/api/v1/usage/ledger", params={"user_ref": uid}).json()
    assert ledger["total"] == 1 and ledger["items"][0]["session_id"] == "sa"


def test_by_user_reports_sessions_and_cost_today(client, monkeypatch):
    from app.core.config import settings

    a1 = _login_device(client, MEMBER_UNION, device_id="dev-a1")["credential"]
    a2 = _login_device(client, MEMBER_UNION, device_id="dev-a2")["credential"]
    b = _login_device(client, "on_b", device_id="dev-b")["credential"]
    _post_ledger(client, a1, _ledger(session_id="s1", cost=1.25))
    _post_ledger(client, a1, _ledger(session_id="s2", cost=0.25))
    _post_ledger(client, a2, _ledger(session_id="s3", cost=0.5))
    _post_ledger(client, b, _ledger(session_id="s4", cost=0.1))
    # 同一会话再报一次：latest-wins，不重复计数
    _post_ledger(client, a1, _ledger(session_id="s1", cost=1.5))
    # 昨天之前的会话不进今天
    _post_ledger(client, b, _ledger(session_id="s-old", cost=9.0, ts=TS - 3 * 86400))

    monkeypatch.setattr(settings.control_plane, "CTL_ENROLL_ENABLED", True)
    _admin(client)
    code = client.post("/api/v1/identity/enroll-codes", json={"org_id": "default"}).json()["code"]
    anon = client.post(
        "/api/v1/ctl/enroll", headers={"X-Enroll-Token": code}, json={"device_id": "dev-anon"}
    ).json()["credential"]
    _post_ledger(client, anon, _ledger(session_id="s5", cost=0.05))

    resp = client.get("/api/v1/usage/ledger/stats/by-user")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["period"] == "daily" and body["period_key"] == TODAY
    rows = {r["union_id"] or None: r for r in body["items"]}
    assert set(rows) == {MEMBER_UNION, "on_b", None}

    a = rows[MEMBER_UNION]
    assert (a["user_ref"], a["name"], a["devices"], a["sessions"]) == (_uid(client, MEMBER_UNION), MEMBER_UNION, 2, 3)
    assert abs(a["cost_usd"] - 2.25) < 1e-9
    assert rows["on_b"]["sessions"] == 1 and abs(rows["on_b"]["cost_usd"] - 0.1) < 1e-9
    assert rows[None]["user_ref"] is None and rows[None]["sessions"] == 1
    # 按花费倒序
    assert body["items"][0]["union_id"] == MEMBER_UNION

    # 各行加起来 = 同周期 by-scope 合计
    scope = client.get("/api/v1/usage/ledger/stats/by-scope", params={"period": "daily"}).json()
    assert abs(sum(r["cost_usd"] for r in body["items"]) - sum(r["cost_usd"] for r in scope["items"])) < 1e-9


def test_by_user_rejects_session_period(client):
    _admin(client)
    assert client.get("/api/v1/usage/ledger/stats/by-user", params={"period": "session"}).status_code == 400
    assert client.get("/api/v1/usage/ledger/stats/by-user", params={"period": "yearly"}).status_code == 422


def test_by_user_requires_admin_session(client):
    assert client.get("/api/v1/usage/ledger/stats/by-user").status_code == 401
    _feishu_login(client, MEMBER_UNION)
    assert client.get("/api/v1/usage/ledger/stats/by-user").status_code == 403


# ---------------------------------------------------------------------------
# 审计 actor
# ---------------------------------------------------------------------------
def test_feishu_session_admin_write_audits_union_id(client):
    _login_device(client, MEMBER_UNION)  # 顺带建出 default 组织
    _admin(client)
    resp = client.post(
        "/api/v1/policies",
        json={"scope_type": "org", "scope_id": "default", "org_id": "default",
              "settings": {"disableAllHooks": True}, "reason": "p3"},
    )
    assert resp.status_code == 201, resp.text
    with _db(client) as conn:
        actors = {r[0] for r in conn.execute("SELECT actor FROM policy_audit")}
    assert actors == {f"user:{ADMIN_UNION}"}

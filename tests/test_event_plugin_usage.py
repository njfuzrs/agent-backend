"""P5 按插件统计调用次数：tool_invoked 事件入库校验 + GET /events/stats/by-plugin。

- tool_invoked 在白名单里；plugin_name 非 slug / plugin_component 不在闭集 → 逐条 rejected，不退整批
- 聚合按 plugin_name × user_ref，user_ref 来自凭据（body / metadata 伪造无效）
- 未登录设备合成 user_ref=null 一行，不计入去重人数
- 只统计 tool_invoked：同库里别的事件带同样 metadata 也不算
- 管理端点挂管理台会话：未登录 401、member 403
"""

from __future__ import annotations

from test_auth_cli import _login_device
from test_auth_login import ADMIN_UNION, MEMBER_UNION, _db, _feishu_login, _uid, client  # noqa: F401

_TS = 1_780_557_354_650


def _bearer(cred: str) -> dict:
    return {"Authorization": f"Bearer {cred}"}


def _invoked(i: int, plugin="jira-helper", component="mcp", tool="search_issues", **meta) -> dict:
    return {
        "eventName": "tool_invoked",
        "timestamp": _TS + i,
        "metadata": {
            "_ctx_session_id": "s-1",
            "plugin_name": plugin,
            "plugin_marketplace": "company",
            "plugin_component": component,
            "plugin_tool": tool,
            "tool_name": "mcp_tool" if component == "mcp" else "Skill",
            **meta,
        },
    }


def _post(client, cred, events):
    resp = client.post("/api/v1/events", json={"events": events}, headers=_bearer(cred))
    assert resp.status_code == 202, resp.text
    return resp.json()


def _anon_device(client, monkeypatch, device_id="dev-anon") -> str:
    from app.core.config import settings

    monkeypatch.setattr(settings.control_plane, "CTL_ENROLL_ENABLED", True)
    _feishu_login(client, ADMIN_UNION)
    code = client.post("/api/v1/identity/enroll-codes", json={"org_id": "default"}).json()["code"]
    resp = client.post(
        "/api/v1/ctl/enroll", headers={"X-Enroll-Token": code}, json={"device_id": device_id}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["credential"]


def _stats(client, **params):
    _feishu_login(client, ADMIN_UNION)
    resp = client.get("/api/v1/events/stats/by-plugin", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 入库
# ---------------------------------------------------------------------------
def test_tool_invoked_is_whitelisted(client):
    cred = _login_device(client, MEMBER_UNION)["credential"]
    body = _post(client, cred, [_invoked(0), _invoked(1, plugin="doc_writer", component="skill", tool="summarize")])
    assert body == {"accepted": 2, "deduped": 0, "rejected": 0}


def test_bad_plugin_fields_rejected_per_event_and_counted(client):
    """坏字段逐条拒、计入 event_rejects，好的照常入库（不退整批）。"""
    cred = _login_device(client, MEMBER_UNION)["credential"]
    bad = [
        _invoked(10, plugin="Jira Helper"),  # 大写 + 空格
        _invoked(11, plugin="-leading-dash"),
        _invoked(12, plugin="a" * 65),  # 超长
        _invoked(13, plugin="jira\n"),  # 结尾换行（re.match + $ 会放行）
        _invoked(14, plugin=""),
        _invoked(15, component="hook"),  # 组件不在闭集
        _invoked(16, component="MCP"),
    ]
    missing_name = _invoked(17)
    del missing_name["metadata"]["plugin_name"]
    missing_comp = _invoked(18)
    del missing_comp["metadata"]["plugin_component"]
    numeric_name = _invoked(19, plugin=123)
    events = [_invoked(0), *bad, missing_name, missing_comp, numeric_name, _invoked(1, plugin="a" * 64)]

    body = _post(client, cred, events)
    assert body == {"accepted": 2, "deduped": 0, "rejected": 10}
    with _db(client) as conn:
        assert conn.execute("SELECT COUNT(*) FROM events WHERE event_name='tool_invoked'").fetchone() == (2,)
        assert conn.execute(
            "SELECT event_name, reason, count FROM event_rejects"
        ).fetchall() == [("tool_invoked", "bad_plugin_fields", 10)]


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------
def test_by_plugin_aggregates_plugin_by_user_with_anonymous_row(client, monkeypatch):
    a1 = _login_device(client, MEMBER_UNION, device_id="dev-a1")["credential"]
    a2 = _login_device(client, MEMBER_UNION, device_id="dev-a2")["credential"]
    b = _login_device(client, "on_b", device_id="dev-b")["credential"]
    # A 两台设备共 3 次 jira（同一个人）、1 次 doc-writer
    _post(client, a1, [_invoked(0), _invoked(1)])
    _post(client, a2, [_invoked(2), _invoked(3, plugin="doc-writer", component="skill", tool="summarize")])
    # B 1 次 jira
    _post(client, b, [_invoked(4)])
    # 未登录设备 2 次 jira（两台不同的匿名设备合成一行）
    anon1 = _anon_device(client, monkeypatch, "dev-anon-1")
    anon2 = _anon_device(client, monkeypatch, "dev-anon-2")
    _post(client, anon1, [_invoked(5)])
    _post(client, anon2, [_invoked(6)])
    # 别的事件名带同样 metadata：不算（变异自证锚点：去掉 event_name 过滤会让 jira 变 8）
    _post(client, a1, [{**_invoked(7), "eventName": "tool_call"}])

    body = _stats(client)
    assert body["days"] == 30 and body["truncated"] is False and body["scanned"] == 7
    items = {i["plugin_name"]: i for i in body["items"]}
    assert set(items) == {"jira-helper", "doc-writer"}
    # 按调用数倒序
    assert [i["plugin_name"] for i in body["items"]] == ["jira-helper", "doc-writer"]

    jira = items["jira-helper"]
    assert (jira["calls"], jira["mcp_calls"], jira["skill_calls"]) == (6, 6, 0)
    assert jira["users"] == 2 and jira["has_anonymous"] is True
    assert jira["marketplaces"] == ["company"]
    uid_a, uid_b = _uid(client, MEMBER_UNION), _uid(client, "on_b")
    rows = {r["user_ref"]: r for r in jira["by_user"]}
    assert set(rows) == {uid_a, uid_b, None}
    assert (rows[uid_a]["calls"], rows[uid_a]["union_id"], rows[uid_a]["name"]) == (3, MEMBER_UNION, MEMBER_UNION)
    assert rows[uid_b]["calls"] == 1
    assert rows[None]["calls"] == 2 and rows[None]["union_id"] == ""
    # 各行加起来 = 插件总数
    assert sum(r["calls"] for r in jira["by_user"]) == jira["calls"]

    doc = items["doc-writer"]
    assert (doc["calls"], doc["skill_calls"], doc["users"], doc["has_anonymous"]) == (1, 1, 1, False)
    assert [r["user_ref"] for r in doc["by_user"]] == [uid_a]


def test_user_ref_comes_from_credential_not_body(client):
    """body 顶层与 metadata 里伪造 user_ref / user_id 都不影响归属。"""
    cred = _login_device(client, MEMBER_UNION)["credential"]
    _feishu_login(client, ADMIN_UNION)
    admin_id = _uid(client, ADMIN_UNION)
    forged = {**_invoked(0, user_ref=admin_id, user_id="boss"), "user_ref": admin_id, "userRef": admin_id}
    _post(client, cred, [forged])

    body = _stats(client)
    by_user = body["items"][0]["by_user"]
    assert [r["user_ref"] for r in by_user] == [_uid(client, MEMBER_UNION)]


def test_by_plugin_window_and_org_filter(client):
    cred = _login_device(client, MEMBER_UNION)["credential"]
    _post(client, cred, [_invoked(0), _invoked(1)])
    # 把一条挪到 40 天前：30 天窗口外、90 天窗口内
    with _db(client) as conn:
        conn.execute(
            "UPDATE events SET received_at='2000-01-01T00:00:00+00:00' WHERE client_ts=?", (_TS + 1,)
        )
        conn.commit()
    assert _stats(client)["items"][0]["calls"] == 1
    # days 上限 90，越界 422
    _feishu_login(client, ADMIN_UNION)
    assert client.get("/api/v1/events/stats/by-plugin", params={"days": 91}).status_code == 422
    assert client.get("/api/v1/events/stats/by-plugin", params={"days": 0}).status_code == 422
    # org 过滤
    assert _stats(client, org_id="default")["items"][0]["calls"] == 1
    assert _stats(client, org_id="other-org")["items"] == []


def test_by_plugin_truncated_flag(client, monkeypatch):
    from app.modules.event.service import queries

    monkeypatch.setattr(queries, "PLUGIN_USAGE_MAX_ROWS", 2)
    cred = _login_device(client, MEMBER_UNION)["credential"]
    _post(client, cred, [_invoked(i) for i in range(3)])
    body = _stats(client)
    assert body["truncated"] is True and body["scanned"] == 2
    assert body["items"][0]["calls"] == 2


def test_by_plugin_skips_dirty_rows(client):
    """脚本直接写库的脏行（坏 JSON / 缺字段）跳过，不让聚合 500。"""
    cred = _login_device(client, MEMBER_UNION)["credential"]
    _post(client, cred, [_invoked(0)])
    with _db(client) as conn:
        conn.execute(
            "INSERT INTO events (fingerprint, event_name, device_id, org_id, team_id, client_ts, received_at, metadata_json)"
            " VALUES ('x1', 'tool_invoked', 'd', 'default', '', 1, '2999-01-01', 'not-json'),"
            "        ('x2', 'tool_invoked', 'd', 'default', '', 2, '2999-01-01', '{\"plugin_component\":\"mcp\"}')"
        )
        conn.commit()
    body = _stats(client)
    assert [(i["plugin_name"], i["calls"]) for i in body["items"]] == [("jira-helper", 1)]


def test_by_plugin_requires_admin_session(client):
    assert client.get("/api/v1/events/stats/by-plugin").status_code == 401
    _feishu_login(client, MEMBER_UNION)
    assert client.get("/api/v1/events/stats/by-plugin").status_code == 403
    # 设备凭据也读不了
    cred = _login_device(client, MEMBER_UNION, device_id="dev-x")["credential"]
    client.cookies.clear()
    assert client.get("/api/v1/events/stats/by-plugin", headers=_bearer(cred)).status_code == 401

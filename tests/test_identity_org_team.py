"""D1：组织 / 团队管理入口 + 设备改归属（飞书登录的设备也能进团队）。

验收路径：飞书登录的设备被分到团队 T → 给 T 配一份策略 →
`GET /policies/evaluate?device_id=` 命中 team 层 → 设备下一次控制面请求就拿到 T 的策略。
另外锁住：
- 每个写操作都进 identity_audit（同事务、带理由 / actor / request_id）
- 团队只能建在已存在的组织下；跨组织的团队不能挂给设备（404）
- 再次 `sid-code login` 不会把管理员设的归属冲掉
- member 角色访问这些接口 403
"""

from __future__ import annotations

from test_auth_cli import _login_device, _whoami
from test_auth_login import ADMIN_UNION, MEMBER_UNION, _feishu_login, client  # noqa: F401


def _as_admin(client) -> None:
    client.cookies.clear()
    _feishu_login(client, ADMIN_UNION)


def _create_team(client, org_id="default", team_id="infra", name="基础设施"):
    return client.post(
        "/api/v1/identity/teams",
        json={"org_id": org_id, "team_id": team_id, "name": name, "reason": "新团队"},
    )


def _move(client, device_id="dev-cli", org_id="default", team_id="infra", reason="分到基础设施"):
    return client.post(
        f"/api/v1/identity/devices/{device_id}/move",
        json={"org_id": org_id, "team_id": team_id, "reason": reason},
    )


def test_feishu_login_device_moved_into_team_hits_team_policy(client):
    body = _login_device(client, MEMBER_UNION)
    cred = body["credential"]
    assert body["org_id"] == "default" and body["team_id"] == ""

    _as_admin(client)
    assert _create_team(client).status_code == 201

    moved = _move(client)
    assert moved.status_code == 200, moved.text
    assert moved.json() == {"device_id": "dev-cli", "org_id": "default", "team_id": "infra", "changed": True}

    resp = client.post(
        "/api/v1/policies",
        json={
            "scope_type": "team",
            "scope_id": "infra",
            "org_id": "default",
            "settings": {"permissions": {"deny": ["Bash(curl *)"]}},
            "reason": "团队策略",
        },
    )
    assert resp.status_code == 201, resp.text

    ev = client.get("/api/v1/policies/evaluate", params={"device_id": "dev-cli"})
    assert ev.status_code == 200, ev.text
    assert ev.json()["layer"] == "team"

    # 凭据不用重签：下一次控制面请求的 DeviceContext 就是新归属
    who = _whoami(client, cred)
    assert who.status_code == 200
    assert who.json()["team_id"] == "infra"
    served = client.get("/api/v1/ctl/policy", headers={"Authorization": f"Bearer {cred}"})
    assert served.status_code == 200, served.text
    assert served.json()["permissions"]["deny"] == ["Bash(curl *)"]


def test_relogin_keeps_admin_assigned_team(client):
    _login_device(client, MEMBER_UNION)
    _as_admin(client)
    _create_team(client)
    assert _move(client).status_code == 200

    client.cookies.clear()
    again = _login_device(client, MEMBER_UNION)
    assert again["team_id"] == "infra"


def test_org_and_team_crud_writes_audit(client):
    _as_admin(client)
    org = client.post(
        "/api/v1/identity/organizations",
        json={"org_id": "corp-shanghai", "name": "上海", "reason": "新分公司"},
        headers={"X-Request-ID": "org-create-000001"},
    )
    assert org.status_code == 201, org.text
    renamed = client.patch("/api/v1/identity/organizations/corp-shanghai", json={"name": "上海分公司", "reason": "改名"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "上海分公司"

    assert _create_team(client, org_id="corp-shanghai").status_code == 201
    assert _create_team(client, org_id="corp-shanghai").status_code == 409
    team = client.patch(
        "/api/v1/identity/organizations/corp-shanghai/teams/infra", json={"name": "平台组", "reason": "改名"}
    )
    assert team.status_code == 200 and team.json()["name"] == "平台组"

    teams = client.get("/api/v1/identity/teams", params={"org_id": "corp-shanghai"}).json()["items"]
    assert [(t["team_id"], t["name"], t["device_count"]) for t in teams] == [("infra", "平台组", 0)]

    audit = client.get("/api/v1/identity/audit").json()["items"]
    assert [a["action"] for a in audit] == ["team_rename", "team_create", "org_rename", "org_create"]
    assert audit[0]["detail"] == {"old_name": "基础设施", "new_name": "平台组"}
    assert audit[0]["reason"] == "改名" and audit[0]["actor"]
    assert audit[-1]["request_id"] == "org-create-000001"
    assert audit[-1]["reason"] == "新分公司"


def test_move_audit_records_before_and_after(client):
    _login_device(client, MEMBER_UNION)
    _as_admin(client)
    _create_team(client)
    _move(client)
    # 同值再提交：不写审计
    again = _move(client)
    assert again.json()["changed"] is False
    # 移出团队（只挂组织）
    out = client.post(
        "/api/v1/identity/devices/dev-cli/move", json={"org_id": "default", "team_id": None, "reason": "离组"}
    )
    assert out.status_code == 200 and out.json()["team_id"] == ""

    audit = client.get("/api/v1/identity/audit", params={"target_type": "device", "target_id": "dev-cli"}).json()
    moves = audit["items"]
    assert [m["reason"] for m in moves] == ["离组", "分到基础设施"]
    assert moves[1]["detail"] == {
        "from": {"org_id": "default", "team_id": ""},
        "to": {"org_id": "default", "team_id": "infra"},
    }


def test_team_must_belong_to_target_org(client):
    _login_device(client, MEMBER_UNION)
    _as_admin(client)
    client.post("/api/v1/identity/organizations", json={"org_id": "corp-beijing", "name": "北京"})
    _create_team(client, org_id="corp-beijing")
    # infra 只在北京存在，不能挂在 default 下
    assert _move(client, org_id="default", team_id="infra").status_code == 404
    # 不存在的组织
    assert _move(client, org_id="nope", team_id=None).status_code == 404
    assert _create_team(client, org_id="nope").status_code == 404
    # 不存在的设备
    assert _move(client, device_id="ghost", org_id="corp-beijing").status_code == 404
    # 跨组织整体搬家可以
    assert _move(client, org_id="corp-beijing", team_id="infra").status_code == 200


def test_writes_require_reason_and_valid_slug(client):
    _login_device(client, MEMBER_UNION)
    _as_admin(client)
    _create_team(client)
    assert _move(client, reason="").status_code == 422
    bad = client.post("/api/v1/identity/teams", json={"org_id": "default", "team_id": "a b", "reason": "x"})
    assert bad.status_code == 422
    no_reason = client.post("/api/v1/identity/teams", json={"org_id": "default", "team_id": "ops"})
    assert no_reason.status_code == 422


def test_member_cannot_manage_org_team(client):
    _login_device(client, MEMBER_UNION)
    client.cookies.clear()
    _feishu_login(client, MEMBER_UNION)
    assert client.get("/api/v1/identity/teams").status_code == 403
    assert _create_team(client).status_code == 403
    assert _move(client).status_code == 403
    assert client.get("/api/v1/identity/audit").status_code == 403

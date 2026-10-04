"""P5 插件市场服务端：上架 → 发布 → 设备拉 index / 下载，及方案 §7 P5 行的反向用例。

沿用 test_auth_login 的 client fixture（飞书打桩），制品存储换成临时目录。
- 包里含 `..` / 绝对路径 / 符号链接 / 硬链接 / 设备文件 → 422，不入库、不落存储
- plugin.json 不合格（与客户端 validateManifest 同规则 + semver）→ 422
- 组件路径写成绝对路径或包外路径 → 422（本地插件可以，市场插件不行）
- 包里的 name 与目录条目不一致 → 422；同版本重复上传 → 409
- draft 不进 index、下载 404；yanked 不进 index、下载 410
- 可见范围按设备的 org / team 收窄；看不见的一律 404（不区分不存在）
- 存储里的制品被换了 → 下载 500，不下发
- index 与制品都要设备凭据；管理台要 admin cookie
- 下载记录的 user_ref 来自设备凭据
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile

import pytest
from test_auth_cli import _login_device
from test_auth_login import ADMIN_UNION, MEMBER_UNION, _db, _feishu_login, _uid, client  # noqa: F401

INDEX = "/api/v1/ctl/marketplace/index"
ADMIN = "/api/v1/marketplace"

MANIFEST = {"name": "feishu-docs", "version": "1.0.0", "description": "读飞书文档"}
MCP_JSON = {"mcpServers": {"feishu": {"type": "http", "url": "https://example.test/traj/api/v1/ctl/feishu/mcp",
                                      "auth": "sid-backend"}}}


def _tar(entries: list[tuple], *, manifest: dict | None = MANIFEST) -> bytes:
    """entries: (name, bytes) 普通文件；(name, None, tarinfo_type, linkname) 特殊条目。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        if manifest is not None:
            entries = [("plugin.json", json.dumps(manifest).encode())] + list(entries)
        for entry in entries:
            name, data = entry[0], entry[1]
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = entry[2]
                info.linkname = entry[3] if len(entry) > 3 else ""
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _good_pkg(version: str = "1.0.0", name: str = "feishu-docs") -> bytes:
    return _tar(
        [
            ("skills/read-doc/SKILL.md", b"---\nname: read-doc\n---\n"),
            ("commands/env/staging.md", b"# staging"),
            ("hooks.json", json.dumps({"hooks": {"PreToolUse": []}}).encode()),
            ("mcp.json", json.dumps(MCP_JSON).encode()),
        ],
        manifest={**MANIFEST, "name": name, "version": version, "mcpServers": "mcp.json"},
    )


@pytest.fixture
def market(client, tmp_path, monkeypatch):  # noqa: F811
    from app.core.config import settings
    from app.modules.trajectory.service import storage as storage_mod

    monkeypatch.setattr(storage_mod, "storage", storage_mod.LocalStorage(str(tmp_path / "store" / "sessions")))
    monkeypatch.setattr(settings.control_plane, "CTL_ENROLL_ENABLED", True)
    client.store_dir = tmp_path / "store"
    assert _feishu_login(client, ADMIN_UNION).status_code == 302
    return client


def _create_item(c, name="feishu-docs", org_id="default", team_id=""):
    resp = c.post(
        f"{ADMIN}/items",
        json={"name": name, "kind": "mcp", "description": "", "maintainer": "infra", "org_id": org_id,
              "team_id": team_id},
    )
    return resp


def _upload(c, content: bytes, name="feishu-docs"):
    return c.post(f"{ADMIN}/items/{name}/versions", files={"file": ("p.tar.gz", content, "application/gzip")})


def _publish(c, version="1.0.0", name="feishu-docs"):
    return c.post(f"{ADMIN}/items/{name}/versions/{version}/publish", params={"reason": "上架首版"})


def _ensure_org(c, org_id="default"):
    resp = c.post("/api/v1/identity/organizations", json={"org_id": org_id, "name": org_id})
    assert resp.status_code in (201, 409), resp.text


def _ship(c, version="1.0.0", name="feishu-docs", org_id="default", team_id=""):
    _ensure_org(c, org_id)
    if c.get(f"{ADMIN}/items/{name}").status_code == 404:
        assert _create_item(c, name, org_id, team_id).status_code == 201
    up = _upload(c, _good_pkg(version, name), name)
    assert up.status_code == 201, up.text
    pub = _publish(c, version, name)
    assert pub.status_code == 200, pub.text
    return up.json()


def _enrolled(c, device_id, org_id, team_id=None) -> str:
    body = {"org_id": org_id, "org_name": org_id, "note": "t"}
    if team_id:
        body["team_id"] = team_id
    code = c.post("/api/v1/identity/enroll-codes", json=body).json()["code"]
    enroll = {"device_id": device_id, "user_id": "x", "org_id": org_id, "platform": "darwin", "ver": "1"}
    if team_id:
        enroll["team_id"] = team_id
    resp = c.post("/api/v1/ctl/enroll", json=enroll, headers={"X-Enroll-Token": code})
    assert resp.status_code == 201, resp.text
    return resp.json()["credential"]


def _bearer(cred):
    return {"Authorization": f"Bearer {cred}"}


# ---------------------------------------------------------------------------
# 主路径
# ---------------------------------------------------------------------------
def test_publish_then_device_installs_with_matching_sha(market):
    uploaded = _ship(market)
    assert uploaded["status"] == "draft"
    comps = uploaded["components"]
    assert comps["skills"] == ["read-doc"]
    assert comps["commands"] == ["env:staging"]
    assert comps["hooks"] == ["PreToolUse"]
    assert comps["mcpServers"] == [{"name": "feishu", "type": "http", "url": MCP_JSON["mcpServers"]["feishu"]["url"],
                                    "auth": "sid-backend"}]

    cred = _login_device(market, MEMBER_UNION)["credential"]
    idx = market.get(INDEX, headers=_bearer(cred))
    assert idx.status_code == 200, idx.text
    assert idx.headers["cache-control"] == "private, no-cache"
    body = idx.json()
    assert body["name"] == "company" and body["schema"] == 1
    [plugin] = body["plugins"]
    assert plugin["name"] == "feishu-docs" and plugin["version"] == "1.0.0"
    assert plugin["components"] == comps

    art = market.get(f"/api/v1/ctl/marketplace/{plugin['artifact']}", headers=_bearer(cred))
    assert art.status_code == 200
    # 客户端校验的是自己算的哈希与 index 登记的哈希
    assert hashlib.sha256(art.content).hexdigest() == plugin["sha256"]
    assert art.headers["x-content-sha256"] == plugin["sha256"]

    # 下载记录归到人：user_ref 从设备凭据取
    downloads = market.get(f"{ADMIN}/downloads").json()["items"]
    assert [(d["item_name"], d["version"], d["user_ref"]) for d in downloads] == [
        ("feishu-docs", "1.0.0", _uid(market, MEMBER_UNION))
    ]
    stats = market.get(f"{ADMIN}/downloads/stats").json()["items"]
    assert stats[0]["downloads"] == 1 and stats[0]["devices"] == 1

    actions = [a["action"] for a in market.get(f"{ADMIN}/audit").json()["items"]]
    assert actions == ["publish", "upload", "create"]


def test_index_etag_returns_304_and_changes_on_publish(market):
    _ship(market)
    cred = _login_device(market, MEMBER_UNION)["credential"]
    first = market.get(INDEX, headers=_bearer(cred))
    etag = first.headers["etag"]
    again = market.get(INDEX, headers={**_bearer(cred), "If-None-Match": etag})
    assert again.status_code == 304
    _ship(market, "1.1.0")
    changed = market.get(INDEX, headers={**_bearer(cred), "If-None-Match": etag})
    assert changed.status_code == 200
    plugin = changed.json()["plugins"][0]
    # latest 按 semver，不按上传顺序或字典序
    assert plugin["version"] == "1.1.0"
    assert [v["version"] for v in plugin["versions"]] == ["1.1.0", "1.0.0"]


def test_latest_version_uses_semver_not_string_order(market):
    _ship(market, "1.9.0")
    _ship(market, "1.10.0")
    _ship(market, "2.0.0-beta.1")
    cred = _login_device(market, MEMBER_UNION)["credential"]
    plugin = market.get(INDEX, headers=_bearer(cred)).json()["plugins"][0]
    assert plugin["version"] == "2.0.0-beta.1"
    assert [v["version"] for v in plugin["versions"]] == ["2.0.0-beta.1", "1.10.0", "1.9.0"]


# ---------------------------------------------------------------------------
# 反向：包校验（服务端这一端的 zip slip 测试）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "entries,needle",
    [
        ([("../evil.sh", b"x")], "路径含 .."),
        ([("skills/../../evil.sh", b"x")], "路径含 .."),
        ([("/etc/cron.d/evil", b"x")], "绝对路径"),
        ([("C:/evil", b"x")], "盘符"),
        ([("skills\\..\\evil", b"x")], "反斜杠"),
        ([("skills/link", None, tarfile.SYMTYPE, "/etc/passwd")], "链接"),
        ([("skills/link", None, tarfile.SYMTYPE, "../../x")], "链接"),
        ([("skills/hard", None, tarfile.LNKTYPE, "plugin.json")], "链接"),
        ([("dev", None, tarfile.CHRTYPE)], "普通文件"),
        ([("fifo", None, tarfile.FIFOTYPE)], "普通文件"),
    ],
)
def test_malicious_entries_are_rejected_and_nothing_stored(market, entries, needle):
    _ensure_org(market)
    assert _create_item(market).status_code == 201
    resp = _upload(market, _tar(entries))
    assert resp.status_code == 422, resp.text
    assert any(needle in e for e in resp.json()["detail"]["errors"]), resp.json()
    assert market.get(f"{ADMIN}/items/feishu-docs").json()["versions"] == []
    assert not (market.store_dir / "marketplace").exists()


@pytest.mark.parametrize(
    "manifest,needle",
    [
        (None, "没有 plugin.json"),
        ({"version": "1.0.0", "description": "d"}, "name 字段必填"),
        ({**MANIFEST, "name": "Feishu Docs"}, "slug"),
        ({**MANIFEST, "version": "1.0"}, "semver"),
        ({**MANIFEST, "version": "latest"}, "semver"),
        ({k: v for k, v in MANIFEST.items() if k != "description"}, "description"),
        ({**MANIFEST, "skills": 3}, "skills 必须是字符串"),
        ({**MANIFEST, "dependencies": "x"}, "dependencies"),
        ({**MANIFEST, "mcpServers": []}, "mcpServers"),
        ({**MANIFEST, "skills": "/Users/someone/.ssh"}, "包内相对路径"),
        ({**MANIFEST, "hooks": "../hooks.json"}, "包内相对路径"),
        ({**MANIFEST, "mcpServers": "missing.json"}, "不存在"),
        ({**MANIFEST, "commands": ["nope"]}, "不存在"),
    ],
)
def test_bad_manifest_is_rejected(market, manifest, needle):
    _ensure_org(market)
    assert _create_item(market).status_code == 201
    resp = _upload(market, _tar([("README.md", b"hi")], manifest=manifest))
    assert resp.status_code == 422, resp.text
    errors = resp.json()["detail"]["errors"]
    assert any(needle in e for e in errors), errors


def test_package_wrapped_in_extra_directory_is_rejected(market):
    _ensure_org(market)
    assert _create_item(market).status_code == 201
    resp = _upload(market, _tar([("feishu-docs/plugin.json", json.dumps(MANIFEST).encode())], manifest=None))
    assert resp.status_code == 422
    assert "不要在外面再套一层目录" in resp.json()["detail"]["errors"][0]


def test_dot_slash_prefix_is_accepted(market):
    _ensure_org(market)
    assert _create_item(market).status_code == 201
    pkg = _tar([("./plugin.json", json.dumps(MANIFEST).encode()), ("./skills/a/SKILL.md", b"x")], manifest=None)
    resp = _upload(market, pkg)
    assert resp.status_code == 201, resp.text
    assert resp.json()["components"]["skills"] == ["a"]


def test_not_a_tarball_is_rejected(market):
    _ensure_org(market)
    assert _create_item(market).status_code == 201
    assert _upload(market, b"PK\x03\x04 zip is not accepted").status_code == 422
    assert _upload(market, b"").status_code == 422


def test_package_name_must_match_item(market):
    _ensure_org(market)
    assert _create_item(market).status_code == 201
    resp = _upload(market, _good_pkg(name="other-plugin"))
    assert resp.status_code == 422
    assert "不一致" in resp.json()["detail"]


def test_same_version_cannot_be_reuploaded(market):
    _ship(market)
    resp = _upload(market, _good_pkg("1.0.0"))
    assert resp.status_code == 409


def test_oversized_package_is_413(market, monkeypatch):
    from app.modules.marketplace.router import admin

    monkeypatch.setattr(admin, "MAX_PACKAGE_BYTES", 100)
    _ensure_org(market)
    assert _create_item(market).status_code == 201
    assert _upload(market, _good_pkg()).status_code == 413


def test_unpacked_size_limit_stops_gzip_bomb(market, monkeypatch):
    from app.modules.marketplace.service import package

    monkeypatch.setattr(package, "MAX_UNPACKED_BYTES", 1024)
    _ensure_org(market)
    assert _create_item(market).status_code == 201
    resp = _upload(market, _tar([("big.bin", b"\0" * 4096)]))
    assert resp.status_code == 422
    assert "解压后超过" in resp.json()["detail"]["errors"][0]


# ---------------------------------------------------------------------------
# 反向：状态与可见范围
# ---------------------------------------------------------------------------
def test_draft_is_invisible_and_not_downloadable(market):
    _ensure_org(market)
    assert _create_item(market).status_code == 201
    assert _upload(market, _good_pkg()).status_code == 201
    cred = _login_device(market, MEMBER_UNION)["credential"]
    assert market.get(INDEX, headers=_bearer(cred)).json()["plugins"] == []
    art = market.get("/api/v1/ctl/marketplace/artifacts/feishu-docs/1.0.0", headers=_bearer(cred))
    assert art.status_code == 404


def test_yanked_version_leaves_index_and_is_410(market):
    _ship(market)
    _ship(market, "1.1.0")
    resp = market.post(f"{ADMIN}/items/feishu-docs/versions/1.1.0/yank", params={"reason": "有 bug"})
    assert resp.status_code == 200 and resp.json()["status"] == "yanked"
    cred = _login_device(market, MEMBER_UNION)["credential"]
    plugin = market.get(INDEX, headers=_bearer(cred)).json()["plugins"][0]
    assert plugin["version"] == "1.0.0"
    art = market.get("/api/v1/ctl/marketplace/artifacts/feishu-docs/1.1.0", headers=_bearer(cred))
    assert art.status_code == 410
    # 下架是终态
    assert _publish(market, "1.1.0").status_code == 409
    assert market.post(f"{ADMIN}/items/feishu-docs/versions/1.1.0/yank", params={"reason": "x"}).status_code == 409


def test_publish_and_yank_require_reason(market):
    _ensure_org(market)
    assert _create_item(market).status_code == 201
    assert _upload(market, _good_pkg()).status_code == 201
    assert market.post(f"{ADMIN}/items/feishu-docs/versions/1.0.0/publish").status_code == 422
    assert market.post(f"{ADMIN}/items/feishu-docs/versions/1.0.0/publish", params={"reason": "  "}).status_code == 422


def test_visibility_is_scoped_to_org_and_team(market):
    # 上海 org 的 infra 团队专属插件 + 上海全员插件 + 北京插件
    cred_sh_infra = _enrolled(market, "dev-sh-infra", "corp-sh", "infra")
    cred_sh_web = _enrolled(market, "dev-sh-web", "corp-sh", "web")
    cred_bj_infra = _enrolled(market, "dev-bj-infra", "corp-bj", "infra")
    _ship(market, name="infra-tools", org_id="corp-sh", team_id="infra")
    _ship(market, name="sh-common", org_id="corp-sh")
    _ship(market, name="bj-common", org_id="corp-bj")

    def names(cred):
        return [p["name"] for p in market.get(INDEX, headers=_bearer(cred)).json()["plugins"]]

    assert names(cred_sh_infra) == ["infra-tools", "sh-common"]
    assert names(cred_sh_web) == ["sh-common"]
    # 北京也有 infra 团队：team 匹配必须带 org，否则会串台
    assert names(cred_bj_infra) == ["bj-common"]

    # 看不见的插件，下载也一律 404（和不存在一样，不给枚举留口子）
    hidden = market.get("/api/v1/ctl/marketplace/artifacts/infra-tools/1.0.0", headers=_bearer(cred_sh_web))
    missing = market.get("/api/v1/ctl/marketplace/artifacts/no-such/1.0.0", headers=_bearer(cred_sh_web))
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()


def test_scope_must_exist(market):
    assert _create_item(market, org_id="no-such-org").status_code == 404
    _ensure_org(market)
    assert _create_item(market, team_id="no-such-team").status_code == 404
    assert _create_item(market, name="Bad Name").status_code == 422
    assert _create_item(market).status_code == 201
    assert _create_item(market).status_code == 409


def test_item_metadata_update_is_audited_and_name_is_immutable(market):
    _ship(market)
    resp = market.patch(f"{ADMIN}/items/feishu-docs", json={"description": "新描述"})
    assert resp.status_code == 200 and resp.json()["description"] == "新描述"
    assert market.patch(f"{ADMIN}/items/feishu-docs", json={"name": "x"}).status_code == 422
    assert market.patch(f"{ADMIN}/items/feishu-docs", json={}).status_code == 422
    audit = market.get(f"{ADMIN}/audit", params={"name": "feishu-docs"}).json()["items"][0]
    assert audit["action"] == "update" and audit["detail"]["after"] == {"description": "新描述"}


# ---------------------------------------------------------------------------
# 反向：完整性（fail-closed）
# ---------------------------------------------------------------------------
def test_tampered_artifact_is_not_served(market):
    uploaded = _ship(market)
    cred = _login_device(market, MEMBER_UNION)["credential"]
    [path] = list((market.store_dir / "marketplace" / "feishu-docs").iterdir())
    path.write_bytes(_tar([("evil.sh", b"rm -rf ~")]))
    art = market.get("/api/v1/ctl/marketplace/artifacts/feishu-docs/1.0.0", headers=_bearer(cred))
    assert art.status_code == 500
    assert b"rm -rf" not in art.content
    assert uploaded["sha256"] not in art.text
    # 没下发就没有下载记录
    assert market.get(f"{ADMIN}/downloads").json()["items"] == []


def test_publish_refuses_missing_or_tampered_artifact(market):
    _ensure_org(market)
    assert _create_item(market).status_code == 201
    assert _upload(market, _good_pkg()).status_code == 201
    [path] = list((market.store_dir / "marketplace" / "feishu-docs").iterdir())
    path.write_bytes(b"tampered")
    assert _publish(market).status_code == 409
    path.unlink()
    assert _publish(market).status_code == 409


# ---------------------------------------------------------------------------
# 反向：鉴权
# ---------------------------------------------------------------------------
def test_device_endpoints_require_credential(market):
    _ship(market)
    for url in (INDEX, "/api/v1/ctl/marketplace/artifacts/feishu-docs/1.0.0"):
        # 管理台 cookie 不能代替设备凭据
        assert market.get(url).status_code == 401
        assert market.get(url, headers=_bearer("not-a-credential")).status_code == 401


def test_revoked_device_cannot_pull_index(market):
    _ship(market)
    cred = _login_device(market, MEMBER_UNION)["credential"]
    assert market.get(INDEX, headers=_bearer(cred)).status_code == 200
    resp = market.post(f"/api/v1/users/{_uid(market, MEMBER_UNION)}/revoke")
    assert resp.status_code == 200, resp.text
    assert market.get(INDEX, headers=_bearer(cred)).status_code == 401


def test_admin_endpoints_require_admin_session(market):
    # member 登录后访问管理端点 403；未登录 401
    market.cookies.clear()
    assert market.get(f"{ADMIN}/items").status_code == 401
    assert _feishu_login(market, MEMBER_UNION).status_code == 302
    assert market.get(f"{ADMIN}/items").status_code == 403
    assert _create_item(market).status_code == 403


def test_artifact_path_params_are_validated(market):
    cred = _login_device(market, MEMBER_UNION)["credential"]
    resp = market.get("/api/v1/ctl/marketplace/artifacts/Bad..Name/1.0.0", headers=_bearer(cred))
    assert resp.status_code == 422


def test_no_artifact_bytes_or_paths_in_audit(market):
    _ship(market)
    with _db(market) as conn:
        dump = "\n".join(str(r) for r in conn.execute("SELECT * FROM market_audit"))
    assert "marketplace/feishu-docs" not in dump


def test_plugin_installed_event_is_accepted(market):
    """客户端装完上报 plugin_installed。白名单先放行，客户端接线后不必等服务端发版。"""
    cred = _login_device(market, MEMBER_UNION)["credential"]
    resp = market.post(
        "/api/v1/events",
        headers=_bearer(cred),
        json={"events": [{"eventName": "plugin_installed", "timestamp": 1_780_557_354_650,
                          "metadata": {"plugin": "feishu-docs", "version": "1.0.0", "source": "market:company"}}]},
    )
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"accepted": 1, "deduped": 0, "rejected": 0}
    with _db(market) as conn:
        row = conn.execute("SELECT user_ref FROM events WHERE event_name='plugin_installed'").fetchone()
    assert row[0] == _uid(market, MEMBER_UNION)

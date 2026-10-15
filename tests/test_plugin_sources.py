"""仓内企业插件源码（plugins/*）的门禁：能被服务端收下，且装上之后真的连得到远程 MCP。

线上 feishu-docs 1.0.0 就是反例：包本身合格、上架成功、技能能调，但 mcpServers 为空，
装了也调不到 feishu_doc_read —— 而当时没有任何一处会红。这里把「该带的东西带了」写成断言。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

from app.modules.feishu.service.tools import TOOL_NAMES
from app.modules.marketplace.service.package import inspect_package

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGINS = sorted(p.parent.name for p in (REPO_ROOT / "plugins").glob("*/plugin.json"))
# 设备凭据只会发往 backend.url 同 origin（sid-code backend-auth.ts），生产 backend.url 是这个
PROD_BACKEND_ORIGIN = "https://www.sid-code.cc"
FEISHU_MCP_URL = f"{PROD_BACKEND_ORIGIN}/traj/api/v1/ctl/feishu/mcp"

_spec = importlib.util.spec_from_file_location("plugin_publish", REPO_ROOT / "scripts" / "plugin_publish.py")
plugin_publish = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plugin_publish)


def test_has_plugins():
    assert "feishu-docs" in PLUGINS


@pytest.mark.parametrize("name", PLUGINS)
def test_package_accepted_by_server(name):
    """打出来的包必须过服务端同一份校验（否则上传时才 422）。"""
    pkg = inspect_package(plugin_publish.pack(name))
    manifest = json.loads((REPO_ROOT / "plugins" / name / "plugin.json").read_text(encoding="utf-8"))
    assert pkg.name == name
    assert pkg.version == manifest["version"]
    assert pkg.components["skills"], "插件至少要有一个技能"


@pytest.mark.parametrize("name", PLUGINS)
def test_pack_is_deterministic(name):
    """同一份源码 → 同一个 sha256，版本不可覆盖时才能判断线上那份是不是这份源码。"""
    a = hashlib.sha256(plugin_publish.pack(name)).hexdigest()
    b = hashlib.sha256(plugin_publish.pack(name)).hexdigest()
    assert a == b


def test_feishu_docs_ships_remote_mcp():
    pkg = inspect_package(plugin_publish.pack("feishu-docs"))
    servers = pkg.components["mcpServers"]
    assert len(servers) == 1, f"feishu-docs 必须带远程 MCP：{servers}"
    s = servers[0]
    assert s["url"] == FEISHU_MCP_URL
    assert s.get("auth") == "sid-backend"

    # 客户端连接只认 transport 字段（MCPServerConfig.transport），type 不会被读取；
    # 缺了 transport 时 manager 走不到 http 分支，组件清单却照样显示 [http]。
    raw = pkg.manifest["mcpServers"]
    assert isinstance(raw, dict)
    (cfg,) = raw.values()
    assert cfg.get("transport") == "http"


def test_feishu_docs_skill_names_real_tools():
    """技能里点名的工具必须是远程 MCP 真提供的，否则模型会去找一个不存在的工具。"""
    text = (REPO_ROOT / "plugins" / "feishu-docs" / "skills" / "read-doc" / "SKILL.md").read_text(encoding="utf-8")
    named = set(re.findall(r"`(feishu_[a-z_]+)`", text))
    assert named, "技能里应点名要用的工具"
    assert named <= TOOL_NAMES, f"技能点名了不存在的工具：{named - TOOL_NAMES}"
    # 插件 MCP 在客户端的工具名是 mcp__plugin_<插件>_<server>__<tool>
    assert "mcp__plugin_feishu-docs_feishu__" in text

"""插件包校验：tar.gz → manifest + 组件清单 + sha256（方案 §5.5）。

只在内存里读，**从不解包落盘**：服务端要的只是 plugin.json、hooks.json、MCP 配置三个小文件
和条目名清单。不落盘就没有「解包时写到哪」的问题，路径穿越在这一层只剩「拒绝入库」一件事。
客户端解包时还要再拒一次（两端各一条测试），服务端放过的包不等于客户端可以不查。

包结构：plugin.json 必须在包**根目录**（允许 `./` 前缀），客户端解包到
`~/.sid-code/plugins/<name>/`。不接受「外面再套一层目录」：那会让两端对根目录的猜测不一致。

manifest 规则与 sid-code `packages/cli/src/plugin/validate.ts::validateManifest` 同一份，
另外加两条市场专有的收紧：
- version 必须是 semver（客户端 `/plugin update` 要按它比大小）；
- 组件路径（commands / skills / agents / hooks / mcpServers 文件）必须是包内相对路径，
  且确实存在。客户端 manifest.ts 允许绝对路径 —— 本地插件这样写没问题，市场插件这样写
  就能让一个上架包去加载员工机器上任意位置的文件。

失败语义 fail-closed：任何一条不过都是 422，不入库。
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
import zlib
from dataclasses import dataclass, field
from typing import Any

# --- 上限。写死在常量里，理由同 event/service/guard.py：改它要一起改文档 -----------
MAX_PACKAGE_BYTES = 20 * 1024 * 1024  # 压缩后。nginx 的 client_max_body_size 是 100M
MAX_UNPACKED_BYTES = 100 * 1024 * 1024  # 解压后合计。防 gzip 炸弹
MAX_ENTRIES = 2000
MAX_SMALL_FILE_BYTES = 1024 * 1024  # plugin.json / hooks.json / MCP 配置单个上限

MANIFEST_FILE = "plugin.json"
DEFAULT_COMMANDS_DIR = "commands"
DEFAULT_SKILLS_DIR = "skills"
DEFAULT_AGENTS_DIR = "agents"
DEFAULT_HOOKS_FILE = "hooks.json"

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-_]*$")
NAME_MAX = 64
# semver 2.0 的常用子集：主.次.修订，可带 -预发布 与 +构建元数据
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-.]+)?$"
)


class PackageError(Exception):
    """包不合格。errors 原样进 422 的 detail，给上架的人看。"""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class ValidatedPackage:
    name: str
    version: str
    description: str
    manifest: dict[str, Any]
    components: dict[str, Any]
    sha256: str
    size_bytes: int
    entries: list[str] = field(default_factory=list)


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def is_valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name)) and len(name) <= NAME_MAX


def is_valid_version(version: str) -> bool:
    return bool(SEMVER_RE.match(version))


def version_key(version: str) -> tuple:
    """semver 排序键。预发布版本排在同号正式版之前；不合法的排最前（不会出现，上传已拦）。"""
    m = SEMVER_RE.match(version)
    if not m:
        return (-1,)
    major, minor, patch, pre = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
    if pre is None:
        return (major, minor, patch, 1, ())
    parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    return (major, minor, patch, 0, parts)


def normalize_member_path(raw: str) -> str:
    """把 tar 条目名规范成包内相对路径。不合格抛 ValueError（原因是给人看的短句）。

    拒绝：绝对路径、Windows 盘符 / 反斜杠、任何 `..` 段、空名、NUL。
    `./a/b`、`a//b`、结尾 `/` 规范成 `a/b`。
    """
    if not raw or "\x00" in raw:
        raise ValueError("条目名为空或含 NUL")
    if "\\" in raw:
        raise ValueError("条目名含反斜杠")
    if raw.startswith("/"):
        raise ValueError("绝对路径")
    if re.match(r"^[A-Za-z]:", raw):
        raise ValueError("带盘符的路径")
    segments = [s for s in raw.split("/") if s not in ("", ".")]
    if any(s == ".." for s in segments):
        raise ValueError("路径含 ..")
    if not segments:
        raise ValueError("条目名为空")
    return "/".join(segments)


def normalize_declared_path(raw: str, field_name: str) -> str:
    """manifest 里声明的组件路径。规则同条目名：只认包内相对路径。"""
    try:
        return normalize_member_path(raw)
    except ValueError as exc:
        raise PackageError([f"{field_name} 必须是包内相对路径（{exc}）: {raw!r}"]) from None


def validate_manifest(m: Any) -> list[str]:
    """与客户端 validateManifest 同一份规则，外加 semver。返回错误列表，空 = 通过。"""
    if not isinstance(m, dict):
        return ["plugin.json 必须是 JSON 对象"]
    errors: list[str] = []

    name = m.get("name")
    if not name or not isinstance(name, str):
        errors.append("name 字段必填且必须是字符串")
    else:
        if not NAME_RE.match(name):
            errors.append("name 必须是 slug 格式（小写字母、数字、-、_，且以字母或数字开头）")
        if len(name) > NAME_MAX:
            errors.append("name 不能超过 64 个字符")

    version = m.get("version")
    if not version or not isinstance(version, str):
        errors.append("version 字段必填且必须是字符串")
    elif not is_valid_version(version):
        errors.append("version 必须是 semver（如 1.2.0）：客户端按它判断是否有更新")

    if not m.get("description") or not isinstance(m.get("description"), str):
        errors.append("description 字段必填且必须是字符串")

    for key in ("author", "license"):
        if key in m and not isinstance(m[key], str):
            errors.append(f"{key} 必须是字符串")

    for key in ("commands", "skills", "agents"):
        if key in m:
            val = m[key]
            if isinstance(val, str):
                continue
            if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
                errors.append(f"{key} 必须是字符串或字符串数组")

    if "hooks" in m and not isinstance(m["hooks"], str):
        errors.append("hooks 必须是字符串（hooks.json 的路径）")

    if "dependencies" in m:
        deps = m["dependencies"]
        if not isinstance(deps, list):
            errors.append("dependencies 必须是字符串数组")
        else:
            for dep in deps:
                if not isinstance(dep, str):
                    errors.append(f"dependencies 中的每个元素必须是字符串，发现: {type(dep).__name__}")

    if "mcpServers" in m and not isinstance(m["mcpServers"], (str, dict)):
        errors.append("mcpServers 必须是字符串（文件路径）或对象")

    return errors


def inspect_package(content: bytes) -> ValidatedPackage:
    """校验一个插件包。通过返回 ValidatedPackage，否则抛 PackageError。"""
    if len(content) > MAX_PACKAGE_BYTES:
        raise PackageError([f"包超过 {MAX_PACKAGE_BYTES // (1024 * 1024)} MiB"])
    if not content:
        raise PackageError(["包是空的"])

    files: dict[str, tarfile.TarInfo] = {}
    dirs: set[str] = set()
    small: dict[str, bytes] = {}
    errors: list[str] = []

    try:
        tar = tarfile.open(fileobj=io.BytesIO(content), mode="r:gz")
    except (tarfile.TarError, OSError, EOFError):
        raise PackageError(["不是合法的 tar.gz"]) from None

    with tar:
        total = 0
        count = 0
        try:
            members = iter(tar)
            for member in members:
                count += 1
                if count > MAX_ENTRIES:
                    raise PackageError([f"条目超过 {MAX_ENTRIES} 个"])
                try:
                    path = normalize_member_path(member.name)
                except ValueError as exc:
                    errors.append(f"非法条目 {member.name!r}：{exc}")
                    continue
                if member.issym() or member.islnk():
                    # 符号链接 / 硬链接一律拒：链接目标可以指向包外，客户端解包时跟过去就是穿越
                    errors.append(f"不允许链接条目: {path!r}")
                    continue
                if member.isdir():
                    dirs.add(path)
                    continue
                if not member.isreg():
                    errors.append(f"只允许普通文件和目录: {path!r}")
                    continue
                if path in files:
                    errors.append(f"重复条目: {path!r}")
                    continue
                total += member.size
                if total > MAX_UNPACKED_BYTES:
                    raise PackageError([f"解压后超过 {MAX_UNPACKED_BYTES // (1024 * 1024)} MiB"])
                files[path] = member
                # 把父目录也记上：很多打包工具不写目录条目
                parts = path.split("/")
                for i in range(1, len(parts)):
                    dirs.add("/".join(parts[:i]))
            if errors:
                raise PackageError(errors)

            def read_small(path: str) -> bytes:
                if path in small:
                    return small[path]
                member = files[path]
                if member.size > MAX_SMALL_FILE_BYTES:
                    raise PackageError([f"{path} 超过 1 MiB"])
                fobj = tar.extractfile(member)
                data = fobj.read() if fobj is not None else b""
                small[path] = data
                return data

            if MANIFEST_FILE not in files:
                raise PackageError(["包根目录没有 plugin.json（不要在外面再套一层目录）"])
            try:
                manifest = json.loads(read_small(MANIFEST_FILE).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PackageError([f"plugin.json 解析失败: {exc}"]) from None
            m_errors = validate_manifest(manifest)
            if m_errors:
                raise PackageError(m_errors)

            components = _components(manifest, files, dirs, read_small)
        except (tarfile.TarError, OSError, EOFError, zlib.error):
            raise PackageError(["tar.gz 内容损坏"]) from None

    return ValidatedPackage(
        name=manifest["name"],
        version=manifest["version"],
        description=manifest["description"],
        manifest=manifest,
        components=components,
        sha256=sha256_hex(content),
        size_bytes=len(content),
        entries=sorted(files),
    )


def _as_list(decl: Any) -> list[str]:
    return decl if isinstance(decl, list) else [decl]


def _component_roots(manifest: dict, key: str, default: str, dirs: set[str], files: dict) -> list[str]:
    """组件根路径。未声明 → 默认目录（存在才算）；声明了 → 必须存在。"""
    if key not in manifest:
        return [default] if default in dirs else []
    roots = []
    for raw in _as_list(manifest[key]):
        path = normalize_declared_path(raw, key)
        if path not in dirs and path not in files:
            raise PackageError([f"{key} 声明的路径在包里不存在: {raw!r}"])
        roots.append(path)
    return roots


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def _components(manifest: dict, files: dict, dirs: set[str], read_small) -> dict[str, Any]:
    """组件清单：客户端安装前原样展示给员工（方案 §5.5「安装前展示组件清单」）。"""
    out: dict[str, Any] = {"skills": [], "commands": [], "agents": [], "hooks": [], "mcpServers": []}

    # skills/<name>/SKILL.md → <name>
    for root in _component_roots(manifest, "skills", DEFAULT_SKILLS_DIR, dirs, files):
        for path in files:
            if _under(path, root) and path.endswith("/SKILL.md"):
                rel = path[len(root) + 1 :]
                out["skills"].append(rel[: -len("/SKILL.md")] or root.split("/")[-1])
    # commands/env/staging.md → env:staging（与 loadPluginCommands 的命名一致）
    for key in ("commands", "agents"):
        default = DEFAULT_COMMANDS_DIR if key == "commands" else DEFAULT_AGENTS_DIR
        for root in _component_roots(manifest, key, default, dirs, files):
            if root in files:
                # 声明的是单个文件
                if root.endswith(".md"):
                    out[key].append(root.split("/")[-1][:-3])
                continue
            for path in files:
                if _under(path, root) and path.endswith(".md"):
                    out[key].append(path[len(root) + 1 : -3].replace("/", ":"))

    hooks_decl = manifest.get("hooks")
    hooks_path = normalize_declared_path(hooks_decl, "hooks") if hooks_decl else DEFAULT_HOOKS_FILE
    if hooks_path in files:
        out["hooks"] = _hook_events(read_small(hooks_path), hooks_path)
    elif hooks_decl:
        raise PackageError([f"hooks 声明的文件在包里不存在: {hooks_decl!r}"])

    mcp_decl = manifest.get("mcpServers")
    servers: Any = None
    if isinstance(mcp_decl, str):
        mcp_path = normalize_declared_path(mcp_decl, "mcpServers")
        if mcp_path not in files:
            raise PackageError([f"mcpServers 声明的文件在包里不存在: {mcp_decl!r}"])
        try:
            parsed = json.loads(read_small(mcp_path).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PackageError([f"{mcp_path} 解析失败: {exc}"]) from None
        servers = (parsed.get("mcpServers") or parsed.get("mcp_servers") or parsed) if isinstance(parsed, dict) else None
        if not isinstance(servers, dict):
            raise PackageError([f"{mcp_path} 必须是对象"])
    elif isinstance(mcp_decl, dict):
        servers = mcp_decl
    if servers:
        for name, cfg in sorted(servers.items()):
            if not isinstance(cfg, dict):
                raise PackageError([f"MCP 服务器 {name!r} 的配置必须是对象"])
            # 展示「连哪里 / 跑什么」，让员工和管理员看得见远程地址与本地命令
            entry = {"name": name, "type": str(cfg.get("type") or ("http" if cfg.get("url") else "stdio"))}
            if cfg.get("url"):
                entry["url"] = str(cfg["url"])
            if cfg.get("command"):
                entry["command"] = str(cfg["command"])
            if cfg.get("auth"):
                entry["auth"] = str(cfg["auth"])
            out["mcpServers"].append(entry)

    for key in ("skills", "commands", "agents"):
        out[key] = sorted(set(out[key]))
    return out


def _hook_events(raw: bytes, path: str) -> list[str]:
    """hooks.json 里挂了哪些事件。hooks 会在员工机器上执行命令，必须让人看见。"""
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError([f"{path} 解析失败: {exc}"]) from None
    hooks = parsed.get("hooks") if isinstance(parsed, dict) and isinstance(parsed.get("hooks"), dict) else parsed
    if not isinstance(hooks, dict):
        raise PackageError([f"{path} 必须是对象"])
    return sorted(str(k) for k in hooks)

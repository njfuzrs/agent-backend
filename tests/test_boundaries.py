#!/usr/bin/env python3
"""边界测试 —— 把规划 §0.3 的三条纪律机械化。

这些纪律的共同特征是：**代码 review 看不出来**。每个文件单独看都正常，
违反只体现在「文件之间的关系」上。所以必须靠测试拦，不能靠人眼。

用法：
    cd backend && ../backend/venv/bin/python -m pytest ../tests/test_boundaries.py -v
    # 或从仓库根：backend/venv/bin/python -m pytest tests/test_boundaries.py -v

测试：
    ① 控制面禁止复用数据面鉴权（静态扫描）
    ② 每个 /ctl/ 端点必须挂 require_device（反射检查；enroll / GET flags 豁免）
    ③ 跨模块禁止直接查表（静态扫描）
    ④ 冻结区路径不得变更（快照测试）
    ⑤ enroll 不得走数据面凭据
    ⑥ 无认证豁免名单是白名单，且每个豁免项的代价可机械检查
"""

import ast
import sys
from pathlib import Path

import pytest

# 让 `import app.*` 可用：把 backend/ 加进 sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

APP_DIR = BACKEND_DIR / "app"

# 数据面鉴权符号 —— 控制面出现这些即为纪律违反
DATA_PLANE_AUTH_SYMBOLS = {"verify_upload_token", "verify_basic_auth"}

# 控制面模块（规划 §2.1）。M1-M5 逐个补齐，目录不存在时跳过。
CONTROL_PLANE_MODULES = ["identity", "flag", "policy", "cost"]


def _iter_py_files(root: Path):
    """遍历 .py 文件，跳过缓存与虚拟环境。"""
    if not root.exists():
        return
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts or "venv" in p.parts:
            continue
        yield p


def _imported_names(path: Path) -> set[str]:
    """解析文件里所有 import 进来的名字（含 from X import a, b）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as e:  # pragma: no cover
        pytest.fail(f"{path} 语法错误: {e}")
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[-1])
    return names


# ---------------------------------------------------------------------------
# ① 控制面禁止复用数据面鉴权
# ---------------------------------------------------------------------------
def test_control_plane_never_imports_data_plane_auth():
    """扫 app/modules/{identity,flag,policy,cost}/ 与 core/auth/control_plane.py，
    出现 verify_upload_token / verify_basic_auth 即失败。

    理由见规划 §2.1：控制面被打穿 = 全公司客户端护栏被关。
    一个能下发 disableAllHooks 的端点挂在共享 token 后面，
    等于「谁拿到上传 token 谁能关掉全公司客户端的护栏」。
    """
    targets = [APP_DIR / "core" / "auth" / "control_plane.py"]
    for mod in CONTROL_PLANE_MODULES:
        targets.extend(_iter_py_files(APP_DIR / "modules" / mod))

    violations = []
    for path in targets:
        if not path.exists():
            continue
        leaked = _imported_names(path) & DATA_PLANE_AUTH_SYMBOLS
        if leaked:
            rel = path.relative_to(BACKEND_DIR)
            violations.append(f"{rel} import 了数据面鉴权符号: {sorted(leaked)}")

    assert not violations, (
        "控制面复用了数据面鉴权（规划 §2.1 禁止）:\n  " + "\n  ".join(violations)
    )


def test_control_plane_module_does_not_import_data_plane_module():
    """更强的一条：control_plane.py 整个模块都不得 import data_plane，
    连 `from app.core.auth import data_plane` 这种形式也算违反。
    """
    path = APP_DIR / "core" / "auth" / "control_plane.py"
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "data_plane" not in node.module, (
                f"control_plane.py 不得 import data_plane（第 {node.lineno} 行）"
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "data_plane" not in alias.name, (
                    f"control_plane.py 不得 import data_plane（第 {node.lineno} 行）"
                )


# ---------------------------------------------------------------------------
# ② 每个 /ctl/ 端点必须挂 require_device
# ---------------------------------------------------------------------------
# 无 require_device 的 /ctl/ 端点白名单。加一条就要在这里写「为什么必须豁免」，
# 否则 review 里「忘了挂鉴权」和「有意豁免」长得一模一样。
# 形状是 (METHOD, path)，方法也锁 —— 否则给 GET /ctl/flags 开的豁免会顺手
# 放过将来某个 POST /ctl/flags。
CTL_AUTH_EXEMPTIONS = {
    # 签发入口，鸡生蛋：拿不到凭据的设备才来 enroll。鉴权走一次性码 X-Enroll-Token。
    ("POST", "/api/v1/ctl/enroll"),
    # 客户端 feature-flags.ts::refreshFromRemote 发裸 fetch，没有 Authorization 头。
    # 挂 require_device 会让它拿 401 后 catch {} 静默吞掉 —— 表现是「flag 功能全在、
    # 真实会话零生效」。代价用两道锁补：本端点只读（写在 /api/v1/flags/**，cookie 会话），
    # 且写入侧门禁禁掉放宽安全类 flag（flag/service/guard.py），见测试 ⑥。
    ("GET", "/api/v1/ctl/flags"),
}


def test_all_control_endpoints_require_device():
    """遍历已挂载路由，路径含 /ctl/ 且依赖链里没有 require_device 的，列出并失败。

    这条防的是「新加端点忘了挂鉴权」，是最容易发生的一类事故 ——
    特征是代码 review 看不出来（每个文件单独看都正常）。

    豁免见 CTL_AUTH_EXEMPTIONS（含豁免理由）；其余 /ctl/ 端点一律要挂。
    """
    from app.core.auth.control_plane import require_device
    from app.main import app

    offenders = []
    for path, methods, dependant in _iter_app_routes(app):
        if "/ctl/" not in path:
            continue
        deps = getattr(dependant, "dependencies", []) or []
        found = any(getattr(d, "call", None) is require_device for d in _flatten_deps(deps))
        if found:
            continue
        normalized = path.rstrip("/") or path
        # HEAD 是 FastAPI 给 GET 自动加的，跟着 GET 的豁免走
        unexempt = sorted(
            m
            for m in methods
            if (m, normalized) not in CTL_AUTH_EXEMPTIONS
            and not (m == "HEAD" and ("GET", normalized) in CTL_AUTH_EXEMPTIONS)
        )
        if unexempt:
            offenders.append(f"{','.join(unexempt)} {path}")

    assert not offenders, (
        "以下控制面端点没有挂 require_device（规划 §2.1）。若确属有意豁免，"
        "把它加进 CTL_AUTH_EXEMPTIONS 并写清理由与补偿措施:\n  "
        + "\n  ".join(offenders)
    )


def test_ctl_exemptions_all_exist():
    """白名单里的每一项都必须对应真实端点。

    防的是「端点改了名/挪走了，豁免还留着」—— 那条豁免会静静地等着
    下一个同名端点，把它也放过去。
    """
    from app.main import app

    actual = set()
    for path, methods, _dependant in _iter_app_routes(app):
        normalized = path.rstrip("/") or path
        for m in methods:
            actual.add((m, normalized))

    stale = sorted(CTL_AUTH_EXEMPTIONS - actual)
    assert not stale, f"CTL_AUTH_EXEMPTIONS 里有已不存在的端点，请删除: {stale}"


def _flatten_deps(deps):
    """递归展开依赖树 —— require_device 可能挂在嵌套依赖里。"""
    for d in deps:
        yield d
        yield from _flatten_deps(getattr(d, "dependencies", []) or [])


def _iter_app_routes(app):
    """产出 (path, methods, dependant)。

    FastAPI ≤0.140 把 include_router 摊平到 app.routes（APIRoute 带 path）。
    0.141+（Starlette 1.6）改成 _IncludedRouter 延迟展开，顶层没有 path，
    必须走 effective_candidates() 才能看到真实 URL。门禁必须两种都认，
    否则 CI 装到新 FastAPI 会把冻结区误报成「端点消失」。
    """
    for node in app.routes:
        yield from _iter_route_node(node)


def _iter_route_node(node):
    """递归展开一层路由节点（含 0.141 的嵌套 include）。"""
    effective = getattr(node, "effective_candidates", None)
    if callable(effective):
        for child in effective():
            yield from _iter_route_node(child)
        return
    path = getattr(node, "path", None)
    if not path:
        return
    methods = getattr(node, "methods", None) or []
    yield path, methods, getattr(node, "dependant", None)


# ---------------------------------------------------------------------------
# ③ 跨模块禁止直接查表
# ---------------------------------------------------------------------------
def test_no_cross_module_model_import():
    """modules/A 不得 import modules/B 的 model，只能走 service 层函数调用。

    规划 §2.3 的切分原则：一个模块 = 一组内聚的表 + 一个路由前缀 + 一条鉴权链。
    直接跨模块查表会让「改一张表」的影响面无法界定。
    """
    modules_dir = APP_DIR / "modules"
    module_names = [
        p.name for p in modules_dir.iterdir()
        if p.is_dir() and p.name != "__pycache__"
    ]

    violations = []
    for owner in module_names:
        for path in _iter_py_files(modules_dir / owner):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mod = None
                if isinstance(node, ast.ImportFrom) and node.module:
                    mod = node.module
                elif isinstance(node, ast.Import):
                    mod = next(
                        (a.name for a in node.names if a.name.startswith("app.modules.")),
                        None,
                    )
                if not mod or not mod.startswith("app.modules."):
                    continue
                parts = mod.split(".")
                if len(parts) < 3:
                    continue
                other = parts[2]
                if other == owner:
                    continue  # 同模块内部，允许
                if len(parts) >= 4 and parts[3] == "model":
                    rel = path.relative_to(BACKEND_DIR)
                    violations.append(
                        f"{rel}:{node.lineno} 模块 {owner} 直接 import 了 {other} 的 model"
                    )

    assert not violations, (
        "跨模块直接查表（规划 §2.3 禁止，请改走 service 函数）:\n  "
        + "\n  ".join(violations)
    )


# ---------------------------------------------------------------------------
# ④ 冻结区路径不得变更
# ---------------------------------------------------------------------------
# 规划 §1.2 冻结区：这些 URL 是 sid-code 线上在用的，改了就断采集。
#   /api/v1/upload/session-file ← packages/core/src/trace/uploader.ts:272
#   /api/v1/health              ← packages/core/src/trace/uploader.ts:169（心跳，60s）
FROZEN_ROUTES = [
    ("/api/v1/upload/session-file", "POST"),
    ("/api/v1/health", "GET"),
    ("/api/v1/upload/traj", "POST"),
    ("/api/v1/upload/batch", "POST"),
    ("/api/v1/upload/reindex", "POST"),
    ("/api/v1/trajectories", "GET"),
    ("/api/v1/trajectories/{session_id}", "GET"),
    ("/api/v1/trajectories/{session_id}", "PATCH"),
    ("/api/v1/trajectories/{session_id}", "DELETE"),
    ("/api/v1/trajectories/batch", "PATCH"),
    ("/api/v1/trajectories/{session_id}/detail/trajectory", "GET"),
    ("/api/v1/trajectories/{session_id}/detail/history", "GET"),
    ("/api/v1/trajectories/{session_id}/detail/info", "GET"),
    ("/api/v1/trajectories/{session_id}/detail/raw", "GET"),
    ("/api/v1/trajectories/{session_id}/detail/raw-data", "GET"),
    ("/api/v1/trajectories/{session_id}/detail/events", "GET"),
    ("/api/v1/stats/overview", "GET"),
    ("/api/v1/stats/trends", "GET"),
    ("/api/v1/stats/tools", "GET"),
    ("/api/v1/stats/models", "GET"),
    ("/api/v1/stats/cost", "GET"),
    ("/api/v1/export/trajectories", "POST"),
    ("/api/v1/export/sft", "POST"),
]


def test_frozen_routes_exist():
    """§1.2 冻结区：这些 URL 是 sid-code 线上在用的，改了就断采集。

    这条防的是重构中无意改动线上契约。M0 的模块化重构只允许移动文件，不允许改 URL。
    """
    from app.main import app

    actual = set()
    for path, methods, _ in _iter_app_routes(app):
        for m in methods:
            actual.add((path, m))

    missing = [f"{m} {p}" for p, m in FROZEN_ROUTES if (p, m) not in actual]
    assert not missing, (
        "冻结区 URL 消失了（规划 §1.2：改了就断 sid-code 线上数据采集）:\n  "
        + "\n  ".join(missing)
    )


def test_upload_session_file_uses_upload_token():
    """冻结区的鉴权方式也不能变：session-file 必须仍挂 verify_upload_token。

    仅锁路径不够 —— 路径还在但鉴权换了，客户端一样会 401。
    """
    from app.core.auth.data_plane import verify_upload_token
    from app.main import app

    for path, _methods, dependant in _iter_app_routes(app):
        if path != "/api/v1/upload/session-file":
            continue
        deps = getattr(dependant, "dependencies", []) or []
        if any(getattr(d, "call", None) is verify_upload_token for d in _flatten_deps(deps)):
            return
        pytest.fail("/api/v1/upload/session-file 不再使用 verify_upload_token 鉴权")
    pytest.fail("/api/v1/upload/session-file 端点不存在")


# 签发入口不能挂 require_device（鸡生蛋），但绝不能退回数据面凭据。
ENROLL_PATH = "/api/v1/ctl/enroll"


def test_enroll_exists_and_does_not_use_data_plane_auth():
    """POST /ctl/enroll 必须存在，且依赖链里没有 verify_upload_token / verify_basic_auth。"""
    from app.core.auth.data_plane import verify_basic_auth, verify_upload_token
    from app.main import app

    found = False
    for path, methods, dependant in _iter_app_routes(app):
        if path.rstrip("/") != ENROLL_PATH:
            continue
        found = True
        assert "POST" in methods, "enroll 必须是 POST"
        deps = list(_flatten_deps(getattr(dependant, "dependencies", []) or []))
        leaked = [
            name
            for name, fn in (
                ("verify_upload_token", verify_upload_token),
                ("verify_basic_auth", verify_basic_auth),
            )
            if any(getattr(d, "call", None) is fn for d in deps)
        ]
        assert not leaked, f"{ENROLL_PATH} 复用了数据面鉴权: {leaked}"
    assert found, f"{ENROLL_PATH} 端点不存在"


# ---------------------------------------------------------------------------
# 附加：schema 演进只有一条路
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# ⑥ 无认证豁免的代价可机械检查
# ---------------------------------------------------------------------------
def test_ctl_flags_route_is_read_only():
    """`/ctl/flags` 的无认证豁免只覆盖读。同路径上不得出现写方法。

    豁免的第一道补偿锁。写口在 `/api/v1/flags/**`（cookie 会话）。
    这条防的是「给下发端点顺手加个 POST 方便脚本改 flag」——
    那等于把「谁都能改全公司客户端行为」做成了功能。
    """
    from app.main import app

    write_methods = set()
    for path, methods, _dependant in _iter_app_routes(app):
        if (path.rstrip("/") or path) != "/api/v1/ctl/flags":
            continue
        write_methods |= {m for m in methods if m in {"POST", "PUT", "PATCH", "DELETE"}}

    assert not write_methods, (
        f"/ctl/flags 是无认证端点，不得有写方法: {sorted(write_methods)}。"
        "写口在 /api/v1/flags/**（require_web_session）"
    )


def test_flag_admin_writes_require_web_session():
    """flag 管理台的写端点必须挂 require_web_session，且不在 /ctl/ 下。

    豁免的第二道补偿锁的前半：写口有鉴权。规划 §3 已纠正 2.2 原文 ——
    管理台不能挂 require_device（浏览器没有设备凭据），所以写口用 cookie 会话，
    并且必须与无认证的 /ctl/ 下发路径分开，否则鉴权链分不开。
    """
    from app.core.auth.session import require_web_session
    from app.main import app

    checked = 0
    offenders = []
    for path, methods, dependant in _iter_app_routes(app):
        if not path.startswith("/api/v1/flags"):
            continue
        assert "/ctl/" not in path, f"管理台 flag 端点不得挂在 /ctl/ 下: {path}"
        deps = list(_flatten_deps(getattr(dependant, "dependencies", []) or []))
        if not any(getattr(d, "call", None) is require_web_session for d in deps):
            offenders.append(f"{','.join(sorted(methods))} {path}")
        checked += 1

    assert checked > 0, "没找到 /api/v1/flags 管理端点 —— flag 模块是否没注册？"
    assert not offenders, (
        "以下 flag 管理端点没挂 require_web_session:\n  " + "\n  ".join(offenders)
    )


def test_flag_guard_rejects_permission_widening_keys():
    """写入侧门禁必须拦掉放宽安全限制的 key/description。

    豁免的第二道补偿锁的后半，也是规划 §4 M2「硬约束」的机械化：
    `GET /ctl/flags` 无认证，所以它只能承载「施加约束」类开关。
    一个叫 disable_sandbox 的 flag 挂在无认证端点后面，等于谁能写这张表
    （或谁能中间人这条 HTTP）就能关掉全公司的沙箱。放宽类走 M3 policy。

    这里直接调 guard，不起 HTTP —— 拦的是「函数被改松」，不是「路由忘了调」；
    后者由上面两条覆盖。
    """
    from fastapi import HTTPException

    from app.modules.flag.service.guard import validate_description, validate_key

    must_reject = [
        "disable_sandbox",
        "bypass_permissions",
        "allow_bypass",
        "disable_all_hooks",
        "dangerously_skip_permissions",
        "skip_permission_check",
        "unsafe_mode",
        "disable_sandbox_v2",  # 子串匹配，加后缀绕不过
        "no_sandbox",
    ]
    for key in must_reject:
        with pytest.raises(HTTPException) as exc:
            validate_key(key)
        assert exc.value.status_code == 422, key

    # 非法标识符：客户端 SID_CODE_FLAG_<KEY> 环境变量覆盖会静默失效
    for key in ["Has-Dash", "has.dot", "9leading_digit", "has space", "UPPER", ""]:
        with pytest.raises(HTTPException):
            validate_key(key)

    # 描述也要过词表，否则 feature_x + 描述「关掉沙箱 bypass」能绕过 key 检查
    with pytest.raises(HTTPException):
        validate_description("临时 bypass 权限检查")

    # 正常的「施加约束」类 flag 必须放行
    for key in ["content_tracing", "sink_killswitch", "event_sampling_config", "max_turns_limit"]:
        assert validate_key(key) == key


def test_no_create_all_in_runtime_code():
    """运行时代码不得再调用 create_all / 手写 ALTER TABLE。

    规划 §1.3 ①：两条路（create_all + _migrate_sqlite_columns）里有一条在生产不生效，
    这正是那个 bug 的成因。现在只允许 alembic 一条路。
    """
    banned = ["metadata.create_all", "_migrate_sqlite_columns", "ADD COLUMN"]
    violations = []
    for path in _iter_py_files(APP_DIR):
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            # 跳过注释与文档字符串里的提及（本文件与 db.py 的历史说明会命中）
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("- `"):
                continue
            for b in banned:
                if b in line and "`" not in line:
                    violations.append(f"{path.relative_to(BACKEND_DIR)}:{i} 出现 {b!r}")
    assert not violations, (
        "运行时代码里出现了建表/加列（schema 演进只能走 alembic）:\n  "
        + "\n  ".join(violations)
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

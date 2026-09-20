#!/usr/bin/env python3
"""边界测试 —— 把规划 §0.3 的三条纪律机械化。

这些纪律的共同特征是：**代码 review 看不出来**。每个文件单独看都正常，
违反只体现在「文件之间的关系」上。所以必须靠测试拦，不能靠人眼。

用法：
    cd backend && ../backend/venv/bin/python -m pytest ../tests/test_boundaries.py -v
    # 或从仓库根：backend/venv/bin/python -m pytest tests/test_boundaries.py -v

四条测试：
    ① 控制面禁止复用数据面鉴权（静态扫描）
    ② 每个 /ctl/ 端点必须挂 require_device（反射检查）
    ③ 跨模块禁止直接查表（静态扫描）
    ④ 冻结区路径不得变更（快照测试）
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
def test_all_control_endpoints_require_device():
    """遍历已挂载路由，路径含 /ctl/ 且依赖链里没有 require_device 的，列出并失败。

    这条防的是「新加端点忘了挂鉴权」，是最容易发生的一类事故 ——
    特征是代码 review 看不出来（每个文件单独看都正常）。

    M0 阶段还没有 /ctl/ 端点，此时测试自然通过；
    但它必须**现在就存在**，这样 M1 加第一个控制面端点时门禁已经在位。
    """
    from app.core.auth.control_plane import require_device
    from app.main import app

    offenders = []
    for path, methods, dependant in _iter_app_routes(app):
        if "/ctl/" not in path:
            continue
        deps = getattr(dependant, "dependencies", []) or []
        found = any(getattr(d, "call", None) is require_device for d in _flatten_deps(deps))
        if not found:
            offenders.append(f"{','.join(sorted(methods))} {path}")

    assert not offenders, (
        "以下控制面端点没有挂 require_device（规划 §2.1，无例外）:\n  "
        + "\n  ".join(offenders)
    )


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
    ("/api/v1/compare/groups", "GET"),
    ("/api/v1/compare/groups", "POST"),
    ("/api/v1/compare/groups/{group_id}", "GET"),
    ("/api/v1/compare/groups/{group_id}", "DELETE"),
    ("/api/v1/compare/groups/{group_id}/items", "POST"),
    ("/api/v1/compare/groups/{group_id}/items/{trajectory_id}", "DELETE"),
    ("/api/v1/compare/groups/{group_id}/radar", "GET"),
    ("/api/v1/export/trajectories", "POST"),
    ("/api/v1/export/sft", "POST"),
    ("/api/v1/scoring/stats", "GET"),
    ("/api/v1/scoring/batch", "POST"),
    ("/api/v1/scoring/rescore-all", "POST"),
    ("/api/v1/scoring/score/{session_id}", "POST"),
    ("/api/v1/scoring/{session_id}", "GET"),
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


# ---------------------------------------------------------------------------
# 附加：schema 演进只有一条路
# ---------------------------------------------------------------------------
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

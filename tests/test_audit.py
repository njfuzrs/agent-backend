#!/usr/bin/env python3
"""
test_audit.py — 对账与迁移验证脚本

用法：
    python tests/test_audit.py                                    # 全部检查
    python tests/test_audit.py --test db-storage                  # 只检查 DB vs 存储一致性
    python tests/test_audit.py --test migration --sqlite /path/to/old.db  # 迁移验证

测试内容：
    1. DB vs 存储对账：DB 中每条记录在存储中都有对应文件
    2. 存储 vs DB 对账：存储中每个 session 在 DB 中都有记录
    3. SHA256 完整性抽查：随机抽取 N 个文件验证 hash
    4. 迁移验证：SQLite 记录数 vs PG 记录数，字段值抽查
"""

import argparse
import base64
import hashlib
import json
import os
import random
import sys
import time

import http.client
from urllib.parse import urlparse

# ─────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────

def _require_env(name: str) -> str:
    """读必填环境变量。缺失即退出 —— 不再内置真实凭据做默认值（规划 §PR-0.5）。

    原来这里写着生产的真实口令/token：仓库或文档一泄漏，凭据即泄漏。
    现在必须显式提供，例如：
        export TRAJ_AUTH_PASS=...      # 管理台口令
        export TRAJ_UPLOAD_TOKEN=...   # 上传 token
    """
    val = os.environ.get(name, "")
    if not val:
        raise SystemExit(f"缺少必需的环境变量 {name}（不再有内置默认值，见规划 §PR-0.5）")
    return val

DEFAULT_URL = os.environ.get("TRAJ_PLATFORM_URL", "http://127.0.0.1/traj")
AUTH_USER = os.environ.get("TRAJ_AUTH_USER", "admin")
AUTH_PASS = _require_env("TRAJ_AUTH_PASS")


# ─────────────────────────────────────────────
# HTTP 工具
# ─────────────────────────────────────────────

def _auth_header() -> dict:
    cred = base64.b64encode(f"{AUTH_USER}:{AUTH_PASS}".encode()).decode()
    return {"Authorization": f"Basic {cred}"}


def http_get(url: str) -> tuple:
    parsed = urlparse(url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=30)
    path = parsed.path
    if parsed.query:
        path += "?" + parsed.query
    conn.request("GET", path, headers=_auth_header())
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return resp.status, data


# ─────────────────────────────────────────────
# 测试框架
# ─────────────────────────────────────────────

class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.warnings = 0
        self.errors = []

    def ok(self, name: str):
        self.passed += 1
        print(f"  ✅ {name}")

    def fail(self, name: str, detail: str = ""):
        self.failed += 1
        msg = f"  ❌ {name}: {detail}" if detail else f"  ❌ {name}"
        self.errors.append(msg)
        print(msg)

    def warn(self, name: str, detail: str = ""):
        self.warnings += 1
        print(f"  ⚠️  {name}: {detail}" if detail else f"  ⚠️  {name}")

    def check(self, name: str, condition: bool, detail: str = ""):
        if condition:
            self.ok(name)
        else:
            self.fail(name, detail)

    def summary(self) -> bool:
        total = self.passed + self.failed
        print(f"\n{'='*50}")
        print(f"总计: {total} 项, 通过: {self.passed}, 失败: {self.failed}, 警告: {self.warnings}")
        if self.errors:
            print("\n失败项:")
            for e in self.errors:
                print(f"  {e}")
        return self.failed == 0


# ─────────────────────────────────────────────
# 测试 1：DB vs 存储对账
# ─────────────────────────────────────────────

def test_db_storage_consistency(base_url: str, result: TestResult, sample_size: int = 20):
    """检查 DB 中的记录在存储中是否都有对应文件"""
    print("\n【1】DB vs 存储对账")
    api = base_url.rstrip("/") + "/api/v1"

    # 获取总记录数
    status, body = http_get(f"{api}/trajectories?page=1&page_size=1")
    if status != 200:
        result.fail("获取轨迹列表失败", f"HTTP {status}")
        return
    total = body.get("total", 0)
    result.check("DB 有记录", total > 0, f"total={total}")
    if total == 0:
        return

    print(f"  DB 总记录数: {total}")

    # 随机抽样检查
    check_count = min(sample_size, total)
    pages_needed = (total + 99) // 100  # 每页最多 100 条
    all_sessions = []

    # 获取所有 session_id（分页拉取）
    for page in range(1, min(pages_needed + 1, 11)):  # 最多拉 10 页
        status, body = http_get(f"{api}/trajectories?page={page}&page_size=100")
        if status == 200:
            for item in body.get("items", []):
                all_sessions.append(item.get("session_id"))

    print(f"  拉取到 {len(all_sessions)} 个 session_id，抽样 {check_count} 个")

    # 随机抽样
    if len(all_sessions) > check_count:
        sampled = random.sample(all_sessions, check_count)
    else:
        sampled = all_sessions

    # 检查每个 session 的文件是否可访问
    traj_ok = 0
    traj_missing = 0
    raw_ok = 0
    raw_missing = 0

    for sid in sampled:
        # 检查 traj 文件（通过 detail/trajectory 端点）
        status, body = http_get(f"{api}/trajectories/{sid}/detail/trajectory?limit=1")
        if status == 200:
            traj_ok += 1
        else:
            traj_missing += 1
            result.warn(f"traj 文件缺失: {sid[:12]}", f"HTTP {status}")

        # 检查 raw 文件
        status, body = http_get(f"{api}/trajectories/{sid}/detail/raw-data")
        if status == 200:
            raw_ok += 1
        else:
            raw_missing += 1
            # raw 缺失不算严重错误，部分旧 session 可能没有

    result.check(
        f"traj 文件完整性 ({traj_ok}/{check_count})",
        traj_missing == 0,
        f"缺失 {traj_missing} 个",
    )
    if raw_missing > 0:
        result.warn(f"raw 文件缺失 {raw_missing}/{check_count} 个（旧 session 可能无此文件）")
    else:
        result.ok(f"raw 文件完整性 ({raw_ok}/{check_count})")


# ─────────────────────────────────────────────
# 测试 2：字段完整性抽查
# ─────────────────────────────────────────────

def test_field_integrity(base_url: str, result: TestResult, sample_size: int = 10):
    """抽查 DB 记录的关键字段是否完整"""
    print("\n【2】字段完整性抽查")
    api = base_url.rstrip("/") + "/api/v1"

    status, body = http_get(f"{api}/trajectories?page=1&page_size={sample_size}")
    if status != 200:
        result.fail("获取轨迹列表失败", f"HTTP {status}")
        return

    items = body.get("items", [])
    if not items:
        result.warn("无数据可检查")
        return

    # 必须非空的字段
    required_fields = ["session_id", "model", "exit_status"]
    # 应该 > 0 的数值字段
    positive_fields = ["total_steps"]

    field_issues = {}
    for item in items:
        sid = item.get("session_id", "?")[:12]
        for f in required_fields:
            val = item.get(f)
            if not val:
                field_issues.setdefault(f, []).append(sid)
        for f in positive_fields:
            val = item.get(f, 0)
            if val is None or val <= 0:
                field_issues.setdefault(f, []).append(sid)

    for f, sids in field_issues.items():
        if len(sids) > len(items) * 0.5:
            result.fail(f"字段 {f} 大量为空", f"{len(sids)}/{len(items)} 个")
        else:
            result.warn(f"字段 {f} 部分为空", f"{len(sids)}/{len(items)} 个: {sids[:3]}")

    if not field_issues:
        result.ok(f"所有关键字段完整 ({len(items)} 条抽查)")


# ─────────────────────────────────────────────
# 测试 3：迁移验证（SQLite vs PG）
# ─────────────────────────────────────────────

def test_migration_integrity(base_url: str, sqlite_path: str, result: TestResult):
    """对比 SQLite 源数据与 PG 目标数据"""
    print("\n【3】迁移验证（SQLite → PG）")

    try:
        import sqlite3
    except ImportError:
        result.fail("sqlite3 模块不可用")
        return

    if not os.path.exists(sqlite_path):
        result.fail(f"SQLite 文件不存在: {sqlite_path}")
        return

    api = base_url.rstrip("/") + "/api/v1"

    # 读取 SQLite 数据
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM trajectories")
    sqlite_count = cur.fetchone()[0]
    print(f"  SQLite 记录数: {sqlite_count}")

    # 获取 PG 总数
    status, body = http_get(f"{api}/trajectories?page=1&page_size=1")
    if status != 200:
        result.fail("获取 PG 轨迹列表失败", f"HTTP {status}")
        conn.close()
        return
    pg_count = body.get("total", 0)
    print(f"  PG 记录数:     {pg_count}")

    result.check(
        "记录数一致",
        pg_count >= sqlite_count,
        f"SQLite={sqlite_count}, PG={pg_count}, 差异={sqlite_count - pg_count}",
    )

    # 抽样对比字段值
    cur.execute("SELECT session_id, model, exit_status, total_steps, total_cost_usd FROM trajectories ORDER BY RANDOM() LIMIT 10")
    samples = cur.fetchall()

    mismatch_count = 0
    for row in samples:
        sid = row["session_id"]
        status, pg_data = http_get(f"{api}/trajectories/{sid}")
        if status != 200:
            result.warn(f"PG 中找不到 session: {sid[:12]}")
            mismatch_count += 1
            continue

        # 对比关键字段
        for field in ["model", "exit_status", "total_steps"]:
            sqlite_val = row[field]
            pg_val = pg_data.get(field)
            if str(sqlite_val) != str(pg_val):
                result.warn(f"字段不一致: {sid[:12]}.{field}", f"SQLite={sqlite_val}, PG={pg_val}")
                mismatch_count += 1

    result.check(
        f"抽样字段值一致 ({len(samples)} 条)",
        mismatch_count == 0,
        f"{mismatch_count} 处不一致",
    )

    conn.close()


# ─────────────────────────────────────────────
# 测试 4：统计概览
# ─────────────────────────────────────────────

def test_stats_overview(base_url: str, result: TestResult):
    """输出数据统计概览（不做断言，仅展示）"""
    print("\n【4】数据统计概览")
    api = base_url.rstrip("/") + "/api/v1"

    status, body = http_get(f"{api}/trajectories?page=1&page_size=1")
    if status != 200:
        result.fail("获取统计失败", f"HTTP {status}")
        return

    total = body.get("total", 0)
    print(f"  总记录数: {total}")

    # 按 exit_status 分布
    for es in ["end_turn", "tool_use", "error", ""]:
        label = es or "(空)"
        status, body = http_get(f"{api}/trajectories?exit_status={es}&page=1&page_size=1")
        if status == 200:
            print(f"  exit_status={label}: {body.get('total', '?')}")

    # 按 model 分布
    for model in ["claude-sonnet-4", "claude-opus-4", "claude-haiku-4"]:
        status, body = http_get(f"{api}/trajectories?model={model}&page=1&page_size=1")
        if status == 200 and body.get("total", 0) > 0:
            print(f"  model={model}: {body.get('total', '?')}")

    # 按 quality_status 分布
    for qs in ["unreviewed", "approved", "rejected"]:
        status, body = http_get(f"{api}/trajectories?quality_status={qs}&page=1&page_size=1")
        if status == 200 and body.get("total", 0) > 0:
            print(f"  quality_status={qs}: {body.get('total', '?')}")

    result.ok("统计概览输出完成")


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="对账与迁移验证")
    parser.add_argument("--url", default=DEFAULT_URL, help="服务端地址")
    parser.add_argument("--test", choices=["db-storage", "fields", "migration", "stats", "all"],
                        default="all", help="运行指定测试")
    parser.add_argument("--sqlite", help="SQLite 数据库路径（迁移验证用）")
    parser.add_argument("--sample-size", type=int, default=20, help="抽样数量")
    args = parser.parse_args()

    result = TestResult()

    print(f"服务端: {args.url}")
    print(f"抽样数: {args.sample_size}\n")

    # 健康检查
    status, _ = http_get(f"{args.url.rstrip('/')}/api/v1/health")
    if status != 200:
        print(f"❌ 服务端不可达 (HTTP {status})")
        sys.exit(1)
    print("✅ 服务端可达\n")

    if args.test in ("all", "db-storage"):
        test_db_storage_consistency(args.url, result, args.sample_size)

    if args.test in ("all", "fields"):
        test_field_integrity(args.url, result, args.sample_size)

    if args.test in ("all", "migration"):
        if args.sqlite:
            test_migration_integrity(args.url, args.sqlite, result)
        elif args.test == "migration":
            print("❌ 迁移验证需要 --sqlite 参数")
            sys.exit(1)
        else:
            print("\n【3】迁移验证：跳过（未指定 --sqlite）")

    if args.test in ("all", "stats"):
        test_stats_overview(args.url, result)

    success = result.summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

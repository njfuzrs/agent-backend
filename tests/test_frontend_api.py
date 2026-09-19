#!/usr/bin/env python3
"""
test_frontend_api.py — 前端 API 回归测试

验证轨迹平台所有前端依赖的 API 端点在改造后仍然正常工作。

用法：
    python tests/test_frontend_api.py
    python tests/test_frontend_api.py --url http://your-server/traj

测试覆盖：
    1. 健康检查 /api/v1/health
    2. 轨迹列表 /api/v1/trajectories（分页、过滤、排序、搜索）
    3. 轨迹元数据 /api/v1/trajectories/{id}
    4. 详情分段加载（trajectory/history/info/raw-data/events）
    5. 标注更新 PATCH /api/v1/trajectories/{id}
    6. 软删除 + 恢复不可见
    7. 上传旧接口兼容 POST /api/v1/upload/traj
"""

import argparse
import base64
import gzip
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

import http.client
from urllib.parse import urlparse, urlencode

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

# 无生产 IP 默认值。未设 TRAJ_PLATFORM_URL 时仅回环，打生产请显式传 --url 或环境变量。
DEFAULT_URL = os.environ.get("TRAJ_PLATFORM_URL", "http://127.0.0.1:8900")
UPLOAD_TOKEN = _require_env("TRAJ_UPLOAD_TOKEN")
AUTH_USER = os.environ.get("TRAJ_AUTH_USER", "admin")
AUTH_PASS = _require_env("TRAJ_AUTH_PASS")

TEST_PREFIX = "test-api-"


# ─────────────────────────────────────────────
# HTTP 工具
# ─────────────────────────────────────────────

def _auth_header() -> dict:
    cred = base64.b64encode(f"{AUTH_USER}:{AUTH_PASS}".encode()).decode()
    return {"Authorization": f"Basic {cred}"}


def http_request(method: str, url: str, body=None, headers=None) -> tuple:
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or 80
    path = parsed.path
    if parsed.query:
        path += "?" + parsed.query

    conn = http.client.HTTPConnection(host, port, timeout=30)
    all_headers = _auth_header()
    if headers:
        all_headers.update(headers)

    if isinstance(body, dict):
        body = json.dumps(body).encode()
        all_headers.setdefault("Content-Type", "application/json")

    conn.request(method, path, body=body, headers=all_headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()

    try:
        return resp.status, json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return resp.status, data


def upload_session_file(base_url: str, session_id: str, file_type: str,
                        content: bytes, compressed: bool = False) -> tuple:
    """上传文件到 session-file 端点"""
    boundary = "----TestApiBoundary"

    def _field(name, value):
        return (
            f"\r\n--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}"
        )

    ct = "application/gzip" if compressed else "application/octet-stream"
    fn = f"session.traj{'.gz' if compressed else ''}" if file_type == "traj" else f"raw.jsonl{'.gz' if compressed else ''}"

    file_header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{fn}"\r\n'
        f"Content-Type: {ct}\r\n\r\n"
    ).encode()

    fields = (
        _field("session_id", session_id)
        + _field("file_type", file_type)
        + _field("tool_source", "claude-code")
        + (_field("compressed", "true") if compressed else "")
        + f"\r\n--{boundary}--\r\n"
    ).encode()

    sha256 = hashlib.sha256(content).hexdigest()
    full_body = file_header + content + fields

    parsed = urlparse(base_url)
    host = parsed.hostname
    port = parsed.port or 80
    base_path = (parsed.path or "").rstrip("/")
    upload_path = f"{base_path}/api/v1/upload/session-file"

    conn = http.client.HTTPConnection(host, port, timeout=60)
    conn.request("POST", upload_path, body=full_body, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "X-Upload-Token": UPLOAD_TOKEN,
        "X-Content-SHA256": sha256,
    })
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(data)
    except json.JSONDecodeError:
        return resp.status, {"raw": data.decode()[:500]}


# ─────────────────────────────────────────────
# 测试框架
# ─────────────────────────────────────────────

class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def ok(self, name: str):
        self.passed += 1
        print(f"  ✅ {name}")

    def fail(self, name: str, detail: str = ""):
        self.failed += 1
        msg = f"  ❌ {name}: {detail}" if detail else f"  ❌ {name}"
        self.errors.append(msg)
        print(msg)

    def check(self, name: str, condition: bool, detail: str = ""):
        if condition:
            self.ok(name)
        else:
            self.fail(name, detail)

    def summary(self) -> bool:
        total = self.passed + self.failed
        print(f"\n{'='*50}")
        print(f"总计: {total} 项, 通过: {self.passed}, 失败: {self.failed}")
        if self.errors:
            print("\n失败项:")
            for e in self.errors:
                print(f"  {e}")
        return self.failed == 0


# ─────────────────────────────────────────────
# 测试数据
# ─────────────────────────────────────────────

def make_traj(session_id: str) -> bytes:
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    traj = {
        "trajectory": [
            {"message_type": "action", "role": "assistant", "content": "test action",
             "action": "Read({\"file_path\":\"/tmp/x\"})", "agent": "primary",
             "tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}},
            {"message_type": "observation", "role": "user", "content": "file content", "agent": "primary"},
        ],
        "history": [
            {"role": "user", "content": f"api regression test {session_id}", "agent": "primary"},
            {"role": "assistant", "content": [{"type": "text", "text": "test"}], "agent": "primary"},
        ],
        "info": {
            "model_stats": {"tokens_sent": 500, "tokens_received": 100, "api_calls": 1, "total_cost_usd": 0.02},
            "exit_status": "end_turn", "has_thinking": False,
        },
        "metadata": {
            "session_id": session_id, "model": "claude-sonnet-4-20250514",
            "start_time": now, "end_time": now, "total_steps": 2, "total_api_calls": 1,
            "total_tokens_sent": 500, "total_tokens_received": 100, "total_tokens": 600,
            "total_cost_usd": 0.02, "exit_status": "end_turn",
            "tools_used": ["Read"], "files_edited": [],
            "has_thinking": False, "has_sub_agent": False,
            "working_directory": "/tmp/test", "user_prompts": [f"api test {session_id}"],
        },
    }
    return json.dumps(traj, ensure_ascii=False).encode()


def make_raw() -> bytes:
    return (json.dumps({"index": 1, "model": "claude-sonnet-4", "request": {}, "response": {}}) + "\n").encode()


def make_events(session_id: str) -> bytes:
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    lines = [
        json.dumps({"event": "SessionStart", "session_id": session_id, "timestamp": now}),
        json.dumps({"event": "Stop", "session_id": session_id, "timestamp": now}),
        json.dumps({"event": "SessionEnd", "session_id": session_id, "timestamp": now}),
    ]
    return ("\n".join(lines) + "\n").encode()


# ─────────────────────────────────────────────
# 测试用例
# ─────────────────────────────────────────────

def run_tests(base_url: str):
    result = TestResult()
    api = base_url.rstrip("/") + "/api/v1"
    session_id = TEST_PREFIX + str(uuid.uuid4())[:8]

    print(f"服务端: {base_url}")
    print(f"测试 session: {session_id}\n")

    # ── 1. 健康检查 ──
    print("【1】健康检查")
    status, body = http_request("GET", f"{api}/health")
    result.check("/health 返回 200", status == 200, f"got {status}")
    if status == 200:
        result.check("status=ok", body.get("status") == "ok")

    # ── 2. 上传测试数据（非压缩，向后兼容） ──
    print("\n【2】上传测试数据（非压缩格式，向后兼容）")
    traj_content = make_traj(session_id)
    status, body = upload_session_file(base_url, session_id, "traj", traj_content)
    result.check("上传 traj 返回 200", status == 200, f"got {status}: {body}")

    raw_content = make_raw()
    status, body = upload_session_file(base_url, session_id, "raw", raw_content)
    result.check("上传 raw 返回 200", status == 200, f"got {status}")

    events_content = make_events(session_id)
    status, body = upload_session_file(base_url, session_id, "events", events_content)
    result.check("上传 events 返回 200", status == 200, f"got {status}")

    # ── 3. 上传压缩格式 ──
    print("\n【3】上传压缩格式（新 SDK 格式）")
    session_id_gz = TEST_PREFIX + "gz-" + str(uuid.uuid4())[:8]
    traj_gz = gzip.compress(make_traj(session_id_gz))
    status, body = upload_session_file(base_url, session_id_gz, "traj", traj_gz, compressed=True)
    result.check("上传 gzip traj 返回 200", status == 200, f"got {status}: {body}")
    if status == 200:
        result.check("响应包含 sha256", "sha256" in body, f"keys={list(body.keys())}")

    # ── 4. 列表查询 ──
    print("\n【4】列表查询")
    # 基本列表
    status, body = http_request("GET", f"{api}/trajectories")
    result.check("列表返回 200", status == 200, f"got {status}")
    if status == 200:
        result.check("列表包含 total", "total" in body)
        result.check("列表包含 items", "items" in body)
        result.check("列表包含 page", "page" in body)

    # 分页
    status, body = http_request("GET", f"{api}/trajectories?page=1&page_size=5")
    result.check("分页查询返回 200", status == 200, f"got {status}")
    if status == 200:
        result.check("page_size 生效", len(body.get("items", [])) <= 5)

    # 搜索
    status, body = http_request("GET", f"{api}/trajectories?search={session_id[:12]}")
    result.check("搜索返回 200", status == 200, f"got {status}")
    if status == 200:
        found = any(i.get("session_id") == session_id for i in body.get("items", []))
        result.check("搜索结果包含测试 session", found)

    # 排序
    status, body = http_request("GET", f"{api}/trajectories?sort=-total_cost_usd&page_size=5")
    result.check("排序查询返回 200", status == 200, f"got {status}")

    # 过滤
    status, body = http_request("GET", f"{api}/trajectories?exit_status=end_turn&page_size=5")
    result.check("过滤查询返回 200", status == 200, f"got {status}")

    # ── 5. 元数据详情 ──
    print("\n【5】元数据详情")
    status, body = http_request("GET", f"{api}/trajectories/{session_id}")
    result.check("元数据返回 200", status == 200, f"got {status}")
    if status == 200:
        result.check("session_id 正确", body.get("session_id") == session_id)
        result.check("model 正确", "claude-sonnet" in body.get("model", ""))
        result.check("exit_status 正确", body.get("exit_status") == "end_turn")
        result.check("total_steps > 0", body.get("total_steps", 0) > 0)
        result.check("包含 tools_used", isinstance(body.get("tools_used"), list))
        result.check("包含 first_prompt", len(body.get("first_prompt", "")) > 0)

    # ── 6. 详情分段加载 ──
    print("\n【6】详情分段加载")

    # trajectory 步骤
    status, body = http_request("GET", f"{api}/trajectories/{session_id}/detail/trajectory")
    result.check("trajectory 返回 200", status == 200, f"got {status}")
    if status == 200:
        result.check("trajectory 有 total", "total" in body)
        result.check("trajectory 有 items", "items" in body)
        result.check("trajectory total=2", body.get("total") == 2, f"got {body.get('total')}")

    # trajectory 分页
    status, body = http_request("GET", f"{api}/trajectories/{session_id}/detail/trajectory?offset=0&limit=1")
    result.check("trajectory 分页返回 200", status == 200, f"got {status}")
    if status == 200:
        result.check("trajectory 分页 items=1", len(body.get("items", [])) == 1)

    # history
    status, body = http_request("GET", f"{api}/trajectories/{session_id}/detail/history")
    result.check("history 返回 200", status == 200, f"got {status}")
    if status == 200:
        result.check("history 是列表", isinstance(body, list))
        result.check("history 非空", len(body) > 0)

    # info
    status, body = http_request("GET", f"{api}/trajectories/{session_id}/detail/info")
    result.check("info 返回 200", status == 200, f"got {status}")
    if status == 200:
        result.check("info 包含 model_stats", "model_stats" in body)

    # raw 下载
    status, body = http_request("GET", f"{api}/trajectories/{session_id}/detail/raw")
    result.check("raw 下载返回 200", status == 200, f"got {status}")

    # raw-data
    status, body = http_request("GET", f"{api}/trajectories/{session_id}/detail/raw-data")
    result.check("raw-data 返回 200", status == 200, f"got {status}")
    if status == 200:
        result.check("raw-data 有 items", "items" in body)

    # events
    status, body = http_request("GET", f"{api}/trajectories/{session_id}/detail/events")
    result.check("events 返回 200", status == 200, f"got {status}")
    if status == 200:
        result.check("events 有 items", "items" in body)
        result.check("events total=3", body.get("total") == 3, f"got {body.get('total')}")

    # ── 7. 不存在的 session ──
    print("\n【7】不存在的 session 返回 404")
    status, _ = http_request("GET", f"{api}/trajectories/nonexistent-session-id-12345")
    result.check("不存在的 session 返回 404", status == 404, f"got {status}")

    # ── 8. 标注更新 ──
    print("\n【8】标注更新")
    status, body = http_request("PATCH", f"{api}/trajectories/{session_id}", body={
        "quality_rating": 5,
        "quality_status": "approved",
        "quality_notes": "自动化测试标注",
        "task_type": "feature",
        "project_name": "test-project",
        "tags": ["auto-test", "regression"],
    })
    result.check("标注更新返回 200", status == 200, f"got {status}")

    # 验证更新生效
    status, body = http_request("GET", f"{api}/trajectories/{session_id}")
    if status == 200:
        result.check("quality_rating=5", body.get("quality_rating") == 5)
        result.check("quality_status=approved", body.get("quality_status") == "approved")
        result.check("task_type=feature", body.get("task_type") == "feature")
        result.check("project_name=test-project", body.get("project_name") == "test-project")
        result.check("tags 包含 auto-test", "auto-test" in body.get("tags", []))

    # 过滤验证
    status, body = http_request("GET", f"{api}/trajectories?quality_status=approved&search={session_id[:12]}")
    if status == 200:
        found = any(i.get("session_id") == session_id for i in body.get("items", []))
        result.check("按 quality_status 过滤能找到", found)

    # ── 9. 软删除 ──
    print("\n【9】软删除")
    status, body = http_request("DELETE", f"{api}/trajectories/{session_id}")
    result.check("软删除返回 200", status == 200, f"got {status}")

    # 列表中不可见
    status, body = http_request("GET", f"{api}/trajectories?search={session_id[:12]}")
    if status == 200:
        found = any(i.get("session_id") == session_id for i in body.get("items", []))
        result.check("软删除后列表不可见", not found)

    # 详情 404
    status, _ = http_request("GET", f"{api}/trajectories/{session_id}")
    result.check("软删除后详情 404", status == 404, f"got {status}")

    # 清理第二个测试 session
    http_request("DELETE", f"{api}/trajectories/{session_id_gz}")

    return result.summary()


def main():
    parser = argparse.ArgumentParser(description="前端 API 回归测试")
    parser.add_argument("--url", default=DEFAULT_URL,
                        help="服务端地址（默认 http://127.0.0.1:8900，或环境变量 TRAJ_PLATFORM_URL）")
    args = parser.parse_args()

    success = run_tests(args.url)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

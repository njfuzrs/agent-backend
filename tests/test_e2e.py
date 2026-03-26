#!/usr/bin/env python3
"""
test_e2e.py — 端到端测试：采集 → 压缩上传 → 存储落盘 → DB 入库 → 前端 API 查询

用法：
    # 需要服务端已启动
    python tests/test_e2e.py                              # 使用默认地址
    python tests/test_e2e.py --url http://your-server/traj  # 指定服务端地址

测试流程：
    1. 构造一个模拟的 session（traj + raw.jsonl + events.jsonl）
    2. gzip 压缩 + SHA256 计算
    3. 上传三个文件到服务端
    4. 验证服务端返回 SHA256 一致
    5. 通过 API 查询轨迹列表，确认新 session 出现
    6. 通过 API 查询详情（trajectory/history/info/raw-data/events）
    7. 通过 API 更新标注
    8. 通过 API 软删除
    9. 确认列表中不再出现
   10. 清理：硬删除测试数据（如果支持）
"""

import argparse
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

# 复用 sync.py 的上传逻辑（stdlib only）
import http.client
from urllib.parse import urlparse

# ─────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────

DEFAULT_URL = os.environ.get("TRAJ_PLATFORM_URL", "http://127.0.0.1/traj")
UPLOAD_TOKEN = os.environ.get("TRAJ_UPLOAD_TOKEN", "<REDACTED_TOKEN>")
AUTH_USER = os.environ.get("TRAJ_AUTH_USER", "admin")
AUTH_PASS = os.environ.get("TRAJ_AUTH_PASS", "traj2026")

# 测试 session_id 前缀，便于识别和清理
TEST_PREFIX = "test-e2e-"


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def compress_and_hash(filepath: Path) -> tuple:
    """gzip 压缩并计算 SHA256"""
    gz_path = filepath.with_suffix(filepath.suffix + ".gz")
    with open(filepath, "rb") as f_in:
        with gzip.open(gz_path, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)
    sha256 = hashlib.sha256()
    with open(gz_path, "rb") as f:
        while True:
            chunk = f.read(64 * 1024)
            if not chunk:
                break
            sha256.update(chunk)
    return gz_path, sha256.hexdigest()


def http_request(method: str, url: str, body=None, headers=None, auth=None) -> tuple:
    """发送 HTTP 请求，返回 (status, body_dict_or_bytes)"""
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or 80
    path = parsed.path
    if parsed.query:
        path += "?" + parsed.query

    conn = http.client.HTTPConnection(host, port, timeout=30)
    all_headers = headers or {}
    if auth:
        import base64
        cred = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        all_headers["Authorization"] = f"Basic {cred}"

    conn.request(method, path, body=body, headers=all_headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()

    try:
        return resp.status, json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return resp.status, data


def upload_file(base_url: str, session_id: str, file_type: str,
                gz_path: Path, sha256: str) -> tuple:
    """上传单个压缩文件到服务端，返回 (status, response_dict)"""
    boundary = "----TestE2EBoundary"
    gz_filename = gz_path.name
    gz_size = gz_path.stat().st_size

    def _field(name, value):
        return (
            f"\r\n--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}"
        )

    file_header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{gz_filename}"\r\n'
        "Content-Type: application/gzip\r\n\r\n"
    )
    fields = (
        _field("session_id", session_id)
        + _field("file_type", file_type)
        + _field("tool_source", "claude-code")
        + _field("compressed", "true")
        + _field("user_id", "test-user")
        + _field("device_id", "test-device")
        + f"\r\n--{boundary}--\r\n"
    )

    header_bytes = file_header.encode()
    tail_bytes = fields.encode()
    content_length = len(header_bytes) + gz_size + len(tail_bytes)

    parsed = urlparse(base_url)
    host = parsed.hostname
    port = parsed.port or 80
    base_path = (parsed.path or "").rstrip("/")
    upload_path = f"{base_path}/api/v1/upload/session-file"

    conn = http.client.HTTPConnection(host, port, timeout=60)
    conn.putrequest("POST", upload_path)
    conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
    conn.putheader("Content-Length", str(content_length))
    conn.putheader("X-Upload-Token", UPLOAD_TOKEN)
    conn.putheader("X-Content-SHA256", sha256)
    conn.endheaders()

    conn.send(header_bytes)
    with open(gz_path, "rb") as f:
        while True:
            chunk = f.read(64 * 1024)
            if not chunk:
                break
            conn.send(chunk)
    conn.send(tail_bytes)

    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(body)
    except json.JSONDecodeError:
        return resp.status, {"raw": body.decode()[:500]}


# ─────────────────────────────────────────────
# 测试数据生成
# ─────────────────────────────────────────────

def generate_test_session(tmpdir: Path, session_id: str) -> Path:
    """生成一个模拟的 session 目录，包含 traj + raw.jsonl + events.jsonl"""
    session_dir = tmpdir / session_id
    session_dir.mkdir(parents=True)

    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    # session.traj
    traj = {
        "trajectory": [
            {
                "message_type": "action",
                "role": "assistant",
                "content": "让我读取文件内容\n\nTool: Read\nInput: {\"file_path\": \"/tmp/test.py\"}",
                "thought": "让我读取文件内容",
                "action": "Read({\"file_path\": \"/tmp/test.py\"})",
                "agent": "primary",
                "timestamp": now,
                "tool_use_id": "tu_001",
                "tool_name": "Read",
                "tool_input": {"file_path": "/tmp/test.py"},
            },
            {
                "message_type": "observation",
                "role": "user",
                "content": "print('hello world')",
                "agent": "primary",
                "is_error": False,
                "tool_use_id": "tu_001",
            },
        ],
        "history": [
            {"role": "system", "content": "You are a coding assistant.", "agent": "primary"},
            {"role": "user", "content": "读取 /tmp/test.py", "agent": "primary"},
            {"role": "assistant", "content": [{"type": "text", "text": "让我读取文件内容"}], "agent": "primary"},
        ],
        "info": {
            "model_stats": {"tokens_sent": 1000, "tokens_received": 200, "api_calls": 1, "total_cost_usd": 0.05},
            "exit_status": "end_turn",
            "has_thinking": False,
        },
        "metadata": {
            "session_id": session_id,
            "model": "claude-sonnet-4-20250514",
            "start_time": now,
            "end_time": now,
            "total_steps": 2,
            "total_api_calls": 1,
            "total_tokens_sent": 1000,
            "total_tokens_received": 200,
            "total_tokens": 1200,
            "total_cost_usd": 0.05,
            "exit_status": "end_turn",
            "tools_used": ["Read"],
            "files_edited": [],
            "has_thinking": False,
            "has_sub_agent": False,
            "working_directory": "/tmp/test-project",
            "user_prompts": ["读取 /tmp/test.py"],
        },
    }
    (session_dir / "session.traj").write_text(json.dumps(traj, ensure_ascii=False, indent=2))

    # raw.jsonl
    raw_line = {
        "timestamp": now,
        "index": 1,
        "model": "claude-sonnet-4-20250514",
        "request": {"messages": [{"role": "user", "content": "读取 /tmp/test.py"}], "model": "claude-sonnet-4-20250514"},
        "response": {"content": [{"type": "text", "text": "让我读取文件内容"}], "stop_reason": "end_turn"},
        "stop_reason": "end_turn",
    }
    (session_dir / "raw.jsonl").write_text(json.dumps(raw_line, ensure_ascii=False) + "\n")

    # events.jsonl
    events = [
        {"timestamp": now, "event": "SessionStart", "session_id": session_id, "source": "startup"},
        {"timestamp": now, "event": "UserPromptSubmit", "session_id": session_id, "prompt": "读取 /tmp/test.py"},
        {"timestamp": now, "event": "Stop", "session_id": session_id},
        {"timestamp": now, "event": "SessionEnd", "session_id": session_id, "source": "end"},
    ]
    (session_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n"
    )

    return session_dir


# ─────────────────────────────────────────────
# 测试用例
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

    def summary(self):
        total = self.passed + self.failed
        print(f"\n{'='*50}")
        print(f"总计: {total} 项, 通过: {self.passed}, 失败: {self.failed}")
        if self.errors:
            print("\n失败项:")
            for e in self.errors:
                print(f"  {e}")
        return self.failed == 0


def run_tests(base_url: str):
    """执行全部端到端测试"""
    result = TestResult()
    session_id = TEST_PREFIX + str(uuid.uuid4())[:8]
    api_base = base_url.rstrip("/") + "/api/v1"
    auth = (AUTH_USER, AUTH_PASS)

    print(f"服务端: {base_url}")
    print(f"测试 session_id: {session_id}")
    print()

    # ── 0. 健康检查 ──
    print("【0】健康检查")
    try:
        status, body = http_request("GET", f"{api_base}/health")
        result.check("健康检查返回 200", status == 200, f"got {status}")
    except Exception as e:
        result.fail("健康检查连接失败", str(e))
        print("\n服务端不可达，终止测试")
        result.summary()
        return False

    # ── 1. 生成测试数据 + 压缩 + 上传 ──
    print("\n【1】生成测试数据 + 压缩上传")
    tmpdir = Path(tempfile.mkdtemp(prefix="traj-test-"))
    try:
        session_dir = generate_test_session(tmpdir, session_id)

        upload_results = {}
        for file_type, filename in [("traj", "session.traj"), ("raw", "raw.jsonl"), ("events", "events.jsonl")]:
            filepath = session_dir / filename
            gz_path, sha256 = compress_and_hash(filepath)

            status, body = upload_file(base_url, session_id, file_type, gz_path, sha256)
            upload_results[file_type] = (status, body, sha256)

            result.check(
                f"上传 {filename} 返回 200",
                status == 200,
                f"got {status}: {body}",
            )
            if status == 200 and isinstance(body, dict):
                server_sha = body.get("sha256", "")
                result.check(
                    f"上传 {filename} SHA256 一致",
                    server_sha == sha256,
                    f"local={sha256[:16]} server={server_sha[:16]}",
                )

        # ── 2. 重复上传应返回 409（traj）或 200+skipped（raw/events） ──
        print("\n【2】幂等性测试（重复上传）")
        for file_type, filename in [("traj", "session.traj"), ("raw", "raw.jsonl")]:
            filepath = session_dir / filename
            gz_path, sha256 = compress_and_hash(filepath)
            status, body = upload_file(base_url, session_id, file_type, gz_path, sha256)
            if file_type == "traj":
                # traj 类型重复上传返回 409
                result.check(
                    f"重复上传 {filename} 返回 409",
                    status == 409,
                    f"got {status}: {body}",
                )
            else:
                # raw/events 类型重复上传返回 200+skipped
                result.check(
                    f"重复上传 {filename} 返回 skipped",
                    status == 200 and isinstance(body, dict) and body.get("status") == "skipped",
                    f"got {status}: {body}",
                )
            gz_path.unlink(missing_ok=True)

        # ── 3. 列表查询 ──
        print("\n【3】列表查询")
        # search 搜索的是 first_prompt 字段，测试数据的 first_prompt 是 "读取 /tmp/test.py"
        status, body = http_request("GET", f"{api_base}/trajectories?page_size=100&sort=-uploaded_at", auth=auth)
        result.check("列表查询返回 200", status == 200, f"got {status}")
        if status == 200:
            items = body.get("items", [])
            found = any(item.get("session_id") == session_id for item in items)
            result.check("列表中包含测试 session", found, f"items count={len(items)}")

        # ── 4. 详情查询 ──
        print("\n【4】详情查询")
        # 元数据
        status, body = http_request("GET", f"{api_base}/trajectories/{session_id}", auth=auth)
        result.check("元数据查询返回 200", status == 200, f"got {status}")
        if status == 200:
            result.check("model 正确", body.get("model") == "claude-sonnet-4-20250514")
            result.check("exit_status 正确", body.get("exit_status") == "end_turn")

        # trajectory 步骤
        status, body = http_request("GET", f"{api_base}/trajectories/{session_id}/detail/trajectory", auth=auth)
        result.check("trajectory 步骤查询返回 200", status == 200, f"got {status}")
        if status == 200:
            result.check("trajectory 步骤数 = 2", body.get("total") == 2, f"got {body.get('total')}")

        # history
        status, body = http_request("GET", f"{api_base}/trajectories/{session_id}/detail/history", auth=auth)
        result.check("history 查询返回 200", status == 200, f"got {status}")

        # info
        status, body = http_request("GET", f"{api_base}/trajectories/{session_id}/detail/info", auth=auth)
        result.check("info 查询返回 200", status == 200, f"got {status}")

        # raw-data
        status, body = http_request("GET", f"{api_base}/trajectories/{session_id}/detail/raw-data", auth=auth)
        result.check("raw-data 查询返回 200", status == 200, f"got {status}")

        # events
        status, body = http_request("GET", f"{api_base}/trajectories/{session_id}/detail/events", auth=auth)
        result.check("events 查询返回 200", status == 200, f"got {status}")
        if status == 200:
            result.check("events 数量 = 4", body.get("total") == 4, f"got {body.get('total')}")

        # ── 5. 标注更新 ──
        print("\n【5】标注更新")
        update_body = json.dumps({"quality_rating": 4, "quality_status": "approved", "task_type": "bug_fix"})
        status, body = http_request(
            "PATCH", f"{api_base}/trajectories/{session_id}",
            body=update_body,
            headers={"Content-Type": "application/json"},
            auth=auth,
        )
        result.check("标注更新返回 200", status == 200, f"got {status}")

        # 验证更新生效
        status, body = http_request("GET", f"{api_base}/trajectories/{session_id}", auth=auth)
        if status == 200:
            result.check("quality_rating 已更新", body.get("quality_rating") == 4)
            result.check("quality_status 已更新", body.get("quality_status") == "approved")

        # ── 6. 软删除 ──
        print("\n【6】软删除")
        status, body = http_request("DELETE", f"{api_base}/trajectories/{session_id}", auth=auth)
        result.check("软删除返回 200", status == 200, f"got {status}")

        # 验证列表中不再出现
        status, body = http_request("GET", f"{api_base}/trajectories?search={session_id[:12]}", auth=auth)
        if status == 200:
            items = body.get("items", [])
            found = any(item.get("session_id") == session_id for item in items)
            result.check("软删除后列表中不再出现", not found)

        # 验证详情返回 404
        status, body = http_request("GET", f"{api_base}/trajectories/{session_id}", auth=auth)
        result.check("软删除后详情返回 404", status == 404, f"got {status}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return result.summary()


def main():
    parser = argparse.ArgumentParser(description="端到端测试")
    parser.add_argument("--url", default=DEFAULT_URL, help="服务端地址")
    args = parser.parse_args()

    success = run_tests(args.url)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

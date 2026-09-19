#!/usr/bin/env python3
"""
test_concurrent.py — 并发测试：模拟多用户同时上传，验证无阻塞无数据丢失

用法：
    python tests/test_concurrent.py                                # 默认 50 并发
    python tests/test_concurrent.py --concurrency 100 --sessions 200
    python tests/test_concurrent.py --url http://your-server/traj

测试内容：
    1. 生成 N 个模拟 session
    2. 用 M 个并发线程同时上传（每个 session 上传 traj + raw + events 三个文件）
    3. 统计成功/失败/耗时
    4. 通过 API 查询确认所有 session 都入库
    5. 清理测试数据
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

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

# 无生产 IP 默认值。未设 TRAJ_PLATFORM_URL 时仅回环，打生产请显式传 --url 或环境变量。
DEFAULT_URL = os.environ.get("TRAJ_PLATFORM_URL", "http://127.0.0.1:8900")
UPLOAD_TOKEN = _require_env("TRAJ_UPLOAD_TOKEN")
AUTH_USER = os.environ.get("TRAJ_AUTH_USER", "admin")
AUTH_PASS = _require_env("TRAJ_AUTH_PASS")

TEST_PREFIX = "test-conc-"


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def compress_and_hash(filepath: Path) -> tuple:
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


def upload_file_sync(base_url: str, session_id: str, file_type: str,
                     gz_path: Path, sha256: str) -> dict:
    """同步上传单个文件（线程池中调用）"""
    boundary = "----TestConcBoundary"
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
        + _field("user_id", f"user-{threading_id()}")
        + _field("device_id", f"device-{threading_id()}")
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

    conn = http.client.HTTPConnection(host, port, timeout=120)
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

    return {"status": resp.status, "body": body.decode()[:300]}


def threading_id() -> str:
    import threading
    return str(threading.current_thread().ident)[-6:]


def generate_mini_session(tmpdir: Path, session_id: str) -> Path:
    """生成最小化的测试 session"""
    session_dir = tmpdir / session_id
    session_dir.mkdir(parents=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    traj = {
        "trajectory": [{"message_type": "action", "role": "assistant", "content": "test", "action": "test", "agent": "primary"}],
        "history": [{"role": "user", "content": f"concurrent test {session_id}", "agent": "primary"}],
        "info": {"model_stats": {"tokens_sent": 100, "tokens_received": 50, "api_calls": 1, "total_cost_usd": 0.01}, "exit_status": "end_turn"},
        "metadata": {
            "session_id": session_id, "model": "claude-sonnet-4-20250514",
            "start_time": now, "end_time": now, "total_steps": 1, "total_api_calls": 1,
            "total_tokens_sent": 100, "total_tokens_received": 50, "total_tokens": 150,
            "total_cost_usd": 0.01, "exit_status": "end_turn", "tools_used": [],
            "files_edited": [], "has_thinking": False, "has_sub_agent": False,
            "working_directory": "/tmp", "user_prompts": [f"concurrent test {session_id}"],
        },
    }
    (session_dir / "session.traj").write_text(json.dumps(traj, ensure_ascii=False))
    (session_dir / "raw.jsonl").write_text(json.dumps({"index": 1, "model": "claude-sonnet-4"}, ensure_ascii=False) + "\n")
    (session_dir / "events.jsonl").write_text(json.dumps({"event": "SessionStart", "session_id": session_id}, ensure_ascii=False) + "\n")
    return session_dir


def upload_one_session(base_url: str, session_dir: Path, session_id: str) -> dict:
    """上传一个 session 的全部文件，返回结果摘要"""
    results = {}
    for file_type, filename in [("traj", "session.traj"), ("raw", "raw.jsonl"), ("events", "events.jsonl")]:
        filepath = session_dir / filename
        if not filepath.exists():
            continue
        gz_path, sha256 = compress_and_hash(filepath)
        try:
            r = upload_file_sync(base_url, session_id, file_type, gz_path, sha256)
            results[file_type] = r["status"]
        except Exception as e:
            results[file_type] = f"error: {e}"
        finally:
            gz_path.unlink(missing_ok=True)
    return {"session_id": session_id, "results": results}


def http_get(url: str, auth=None) -> tuple:
    parsed = urlparse(url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=30)
    headers = {}
    if auth:
        cred = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        headers["Authorization"] = f"Basic {cred}"
    path = parsed.path
    if parsed.query:
        path += "?" + parsed.query
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return resp.status, data


def http_delete(url: str, auth=None) -> int:
    parsed = urlparse(url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=30)
    headers = {}
    if auth:
        cred = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        headers["Authorization"] = f"Basic {cred}"
    conn.request("DELETE", parsed.path, headers=headers)
    resp = conn.getresponse()
    resp.read()
    conn.close()
    return resp.status


# ─────────────────────────────────────────────
# 主测试
# ─────────────────────────────────────────────

def run_concurrent_test(base_url: str, num_sessions: int, concurrency: int):
    api_base = base_url.rstrip("/") + "/api/v1"
    auth = (AUTH_USER, AUTH_PASS)

    # 健康检查
    print(f"服务端: {base_url}")
    print(f"并发数: {concurrency}, 会话数: {num_sessions}")
    try:
        status, _ = http_get(f"{api_base}/health")
        if status != 200:
            print(f"❌ 健康检查失败: HTTP {status}")
            return False
    except Exception as e:
        print(f"❌ 服务端不可达: {e}")
        return False
    print("✅ 健康检查通过\n")

    # 生成测试数据
    tmpdir = Path(tempfile.mkdtemp(prefix="traj-conc-"))
    session_ids = [TEST_PREFIX + str(uuid.uuid4())[:8] for _ in range(num_sessions)]

    print(f"生成 {num_sessions} 个测试 session...")
    session_dirs = {}
    for sid in session_ids:
        session_dirs[sid] = generate_mini_session(tmpdir, sid)

    # 并发上传
    print(f"开始并发上传（{concurrency} 线程）...")
    t0 = time.time()
    success_count = 0
    fail_count = 0
    error_details = []

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(upload_one_session, base_url, session_dirs[sid], sid): sid
            for sid in session_ids
        }
        for future in as_completed(futures):
            sid = futures[future]
            try:
                result = future.result()
                statuses = result["results"]
                if all(s == 200 for s in statuses.values()):
                    success_count += 1
                else:
                    fail_count += 1
                    error_details.append(f"  {sid[:12]}: {statuses}")
            except Exception as e:
                fail_count += 1
                error_details.append(f"  {sid[:12]}: exception={e}")

    elapsed = time.time() - t0
    rps = num_sessions / elapsed if elapsed > 0 else 0

    print(f"\n上传完成: {elapsed:.1f}s, {rps:.1f} sessions/s")
    print(f"  成功: {success_count}/{num_sessions}")
    print(f"  失败: {fail_count}/{num_sessions}")
    if error_details:
        print("  失败详情:")
        for d in error_details[:10]:
            print(d)
        if len(error_details) > 10:
            print(f"  ... 还有 {len(error_details) - 10} 个")

    # 验证入库
    print(f"\n验证 DB 入库...")
    time.sleep(2)  # 等待异步写入完成
    found_count = 0
    missing = []
    for sid in session_ids:
        status, body = http_get(f"{api_base}/trajectories/{sid}", auth=auth)
        if status == 200:
            found_count += 1
        else:
            missing.append(sid)

    print(f"  DB 中找到: {found_count}/{num_sessions}")
    if missing:
        print(f"  缺失: {missing[:5]}{'...' if len(missing) > 5 else ''}")

    # 清理测试数据
    print(f"\n清理测试数据...")
    cleaned = 0
    for sid in session_ids:
        status = http_delete(f"{api_base}/trajectories/{sid}", auth=auth)
        if status == 200:
            cleaned += 1
    print(f"  软删除: {cleaned}/{num_sessions}")

    shutil.rmtree(tmpdir, ignore_errors=True)

    # 结果
    all_ok = (success_count == num_sessions and found_count == num_sessions)
    print(f"\n{'='*50}")
    if all_ok:
        print(f"✅ 并发测试通过: {num_sessions} sessions, {concurrency} 并发, {elapsed:.1f}s, {rps:.1f} sessions/s")
    else:
        print(f"❌ 并发测试失败: 上传成功 {success_count}, DB 入库 {found_count}, 期望 {num_sessions}")
    return all_ok


def main():
    parser = argparse.ArgumentParser(description="并发上传测试")
    parser.add_argument("--url", default=DEFAULT_URL,
                        help="服务端地址（默认 http://127.0.0.1:8900，或环境变量 TRAJ_PLATFORM_URL）")
    parser.add_argument("--sessions", type=int, default=50, help="测试会话数量")
    parser.add_argument("--concurrency", type=int, default=50, help="并发线程数")
    args = parser.parse_args()

    success = run_concurrent_test(args.url, args.sessions, args.concurrency)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

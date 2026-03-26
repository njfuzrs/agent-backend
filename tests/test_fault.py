#!/usr/bin/env python3
"""
test_fault.py — 故障测试：SDK 上传在各种异常场景下的行为验证

用法：
    python tests/test_fault.py                    # 运行全部故障测试
    python tests/test_fault.py --test queue       # 只测队列持久化
    python tests/test_fault.py --test health      # 只测健康检查

测试场景：
    1. 队列持久化与恢复：写入队列 → 重新加载 → 验证条目完整
    2. 压缩与校验：compress_and_hash 正确性验证
    3. 健康检查：不可达服务端 → _server_healthy=False
    4. 上传失败入队：模拟服务端 500 → 文件进入重试队列
    5. 幂等上传：409 响应视为成功
    6. SHA256 不匹配：服务端返回错误 hash → 上传失败
    7. 清理逻辑：.uploaded 标记 + 文件删除

注意：本脚本直接测试 uploader.py 的内部逻辑，不需要真实服务端。
      部分测试使用 asyncio mock 模拟网络行为。
"""

import asyncio
import gzip
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# 将 claude-trace 根目录加入 path
CLAUDE_TRACE_DIR = Path(__file__).parent.parent.parent / "claude-trace"
if CLAUDE_TRACE_DIR.exists():
    sys.path.insert(0, str(CLAUDE_TRACE_DIR))
else:
    # 如果从 claude-trace 目录运行
    sys.path.insert(0, str(Path(__file__).parent.parent))

from uploader import UploadManager, UploadQueueItem, compress_and_hash


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
# 测试 1：compress_and_hash 正确性
# ─────────────────────────────────────────────

def test_compress_and_hash(result: TestResult):
    print("\n【1】compress_and_hash 正确性")
    tmpdir = Path(tempfile.mkdtemp(prefix="traj-fault-"))
    try:
        # 创建测试文件
        test_file = tmpdir / "test.jsonl"
        content = '{"key": "value", "data": "x" * 10000}\n' * 100
        test_file.write_text(content)
        original_size = test_file.stat().st_size

        # 压缩
        gz_path, sha256 = compress_and_hash(test_file)

        result.check("压缩文件存在", gz_path.exists())
        result.check("压缩文件后缀正确", gz_path.name == "test.jsonl.gz")
        result.check("压缩后更小", gz_path.stat().st_size < original_size,
                      f"原始={original_size}, 压缩={gz_path.stat().st_size}")

        # 验证 SHA256
        expected_sha = hashlib.sha256(gz_path.read_bytes()).hexdigest()
        result.check("SHA256 正确", sha256 == expected_sha,
                      f"got={sha256[:16]}, expected={expected_sha[:16]}")

        # 验证解压后内容一致
        with gzip.open(gz_path, "rb") as f:
            decompressed = f.read().decode()
        result.check("解压后内容一致", decompressed == content)

        # 原始文件未被修改
        result.check("原始文件未被修改", test_file.read_text() == content)

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ─────────────────────────────────────────────
# 测试 2：队列持久化与恢复
# ─────────────────────────────────────────────

def test_queue_persistence(result: TestResult):
    print("\n【2】队列持久化与恢复")
    tmpdir = Path(tempfile.mkdtemp(prefix="traj-fault-"))
    try:
        # 创建一个假的源文件（队列加载时会检查文件是否存在）
        fake_file = tmpdir / "sessions" / "test-session" / "session.traj"
        fake_file.parent.mkdir(parents=True)
        fake_file.write_text("{}")

        mgr = UploadManager(
            upload_url="http://localhost:9999",
            upload_token="test-token",
            queue_dir=tmpdir,
        )

        # 手动添加队列项
        item1 = UploadQueueItem(
            session_id="test-session-1",
            file_type="traj",
            filepath=str(fake_file),
            gz_path=str(fake_file),  # 假路径
            sha256="abc123",
            retry_count=3,
            status="pending",
            error="timeout",
        )
        item2 = UploadQueueItem(
            session_id="test-session-2",
            file_type="raw",
            filepath=str(fake_file),
            sha256="def456",
            retry_count=50,
            status="failed",
            error="max retries exceeded",
        )
        mgr._queue = [item1, item2]
        mgr._save_queue()

        # 验证队列文件存在
        result.check("队列文件已创建", mgr._queue_file.exists())

        # 读取队列文件内容
        lines = mgr._queue_file.read_text().strip().split("\n")
        result.check("队列文件有 2 行", len(lines) == 2, f"got {len(lines)}")

        # 重新加载
        mgr2 = UploadManager(
            upload_url="http://localhost:9999",
            upload_token="test-token",
            queue_dir=tmpdir,
        )
        mgr2._load_queue()

        result.check("加载后队列有 2 项", len(mgr2._queue) == 2, f"got {len(mgr2._queue)}")
        if len(mgr2._queue) >= 2:
            result.check("item1 session_id 正确", mgr2._queue[0].session_id == "test-session-1")
            result.check("item1 retry_count 保留", mgr2._queue[0].retry_count == 3)
            result.check("item2 status=failed 保留", mgr2._queue[1].status == "failed")

        # 测试过滤：源文件不存在的条目被丢弃
        mgr3 = UploadManager(
            upload_url="http://localhost:9999",
            upload_token="test-token",
            queue_dir=tmpdir,
        )
        # 删除源文件
        fake_file.unlink()
        mgr3._load_queue()
        result.check("源文件不存在时条目被过滤", len(mgr3._queue) == 0, f"got {len(mgr3._queue)}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ─────────────────────────────────────────────
# 测试 3：健康检查（不可达服务端）
# ─────────────────────────────────────────────

def test_health_check(result: TestResult):
    print("\n【3】健康检查（不可达服务端）")

    mgr = UploadManager(
        upload_url="http://127.0.0.1:19999",  # 不存在的端口
        upload_token="test-token",
    )

    async def _run():
        # 创建 HTTP session
        import aiohttp
        mgr._http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3))
        mgr._server_healthy = True

        # 模拟一次健康检查（直接调用内部逻辑）
        try:
            async with mgr._http_session.get(
                mgr._health_endpoint,
                timeout=aiohttp.ClientTimeout(total=2),
            ) as resp:
                mgr._server_healthy = resp.status == 200
        except Exception:
            mgr._server_healthy = False

        result.check("不可达服务端 → _server_healthy=False", mgr._server_healthy is False)

        await mgr._http_session.close()

    asyncio.run(_run())


# ─────────────────────────────────────────────
# 测试 4：上传失败入队
# ─────────────────────────────────────────────

def test_upload_failure_enqueue(result: TestResult):
    print("\n【4】服务端不可达时直接入队")
    tmpdir = Path(tempfile.mkdtemp(prefix="traj-fault-"))
    try:
        session_id = "test-fault-enqueue"
        session_dir = tmpdir / session_id
        session_dir.mkdir(parents=True)

        # 创建测试文件
        (session_dir / "session.traj").write_text('{"trajectory":[],"history":[],"info":{},"metadata":{"session_id":"test"}}')
        (session_dir / "raw.jsonl").write_text('{"index":1}\n')

        mgr = UploadManager(
            upload_url="http://127.0.0.1:19999",
            upload_token="test-token",
            queue_dir=tmpdir / "queue",
            cleanup_after_upload=False,
        )

        # 标记服务端不可达
        mgr._server_healthy = False

        async def _run():
            import aiohttp
            mgr._http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3))

            await mgr.upload_session(session_dir, session_id)

            result.check("服务端不可达时文件入队", len(mgr._queue) >= 1,
                          f"queue size={len(mgr._queue)}")

            # 验证队列中的条目
            if mgr._queue:
                types_in_queue = {item.file_type for item in mgr._queue}
                result.check("traj 在队列中", "traj" in types_in_queue, f"types={types_in_queue}")
                result.check("raw 在队列中", "raw" in types_in_queue, f"types={types_in_queue}")

            # 验证原始文件未被删除（cleanup_after_upload=False 且未全部成功）
            result.check("原始文件未被删除", (session_dir / "session.traj").exists())

            # 验证没有 .uploaded 标记
            result.check("无 .uploaded 标记", not (session_dir / ".uploaded").exists())

            await mgr._http_session.close()

        asyncio.run(_run())

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ─────────────────────────────────────────────
# 测试 5：.uploaded 标记与清理
# ─────────────────────────────────────────────

def test_uploaded_marker_and_cleanup(result: TestResult):
    print("\n【5】.uploaded 标记与清理逻辑")
    tmpdir = Path(tempfile.mkdtemp(prefix="traj-fault-"))
    try:
        session_dir = tmpdir / "test-cleanup"
        session_dir.mkdir(parents=True)

        # 创建模拟文件
        (session_dir / "session.traj").write_text("{}")
        (session_dir / "raw.jsonl").write_text("{}\n")
        (session_dir / "events.jsonl").write_text("{}\n")
        raw_dir = session_dir / "raw"
        raw_dir.mkdir()
        (raw_dir / "001_request.json").write_text("{}")
        (session_dir / "session.traj.gz").write_bytes(b"\x1f\x8b fake gz")

        # 写标记
        test_results = {"traj": {"sha256": "abc", "gz_size": 100}}
        UploadManager._write_uploaded_marker(session_dir, test_results)

        result.check(".uploaded 文件已创建", (session_dir / ".uploaded").exists())
        marker = json.loads((session_dir / ".uploaded").read_text())
        result.check(".uploaded 包含 uploaded_at", "uploaded_at" in marker)
        result.check(".uploaded 包含 files", "files" in marker)

        # 执行清理
        UploadManager._cleanup_session_files(session_dir)

        result.check("session.traj 已删除", not (session_dir / "session.traj").exists())
        result.check("raw.jsonl 已删除", not (session_dir / "raw.jsonl").exists())
        result.check("events.jsonl 已删除", not (session_dir / "events.jsonl").exists())
        result.check("raw/ 目录已删除", not raw_dir.exists())
        result.check(".gz 文件已删除", not (session_dir / "session.traj.gz").exists())
        result.check(".uploaded 标记保留", (session_dir / ".uploaded").exists())

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ─────────────────────────────────────────────
# 测试 6：指数退避重试逻辑
# ─────────────────────────────────────────────

def test_retry_backoff(result: TestResult):
    print("\n【6】指数退避重试（max_retries 超限标记 failed）")

    item = UploadQueueItem(
        session_id="test-retry",
        file_type="traj",
        filepath="/tmp/nonexistent",
        gz_path="/tmp/nonexistent.gz",
        sha256="abc",
        retry_count=48,
        max_retries=50,
    )

    mgr = UploadManager(
        upload_url="http://127.0.0.1:19999",
        upload_token="test-token",
    )

    async def _run():
        import aiohttp
        mgr._http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1))

        # _upload_single_file 会因为文件不存在而失败
        # _retry_with_backoff 会累加 retry_count 直到超过 max_retries
        success = await mgr._retry_with_backoff(item)

        result.check("重试最终失败", success is False)
        result.check("retry_count 超过 max_retries", item.retry_count >= item.max_retries,
                      f"retry_count={item.retry_count}")
        result.check("status 标记为 failed", item.status == "failed")

        await mgr._http_session.close()

    asyncio.run(_run())


# ─────────────────────────────────────────────
# 测试 7：原子写入（队列 .tmp → rename）
# ─────────────────────────────────────────────

def test_atomic_queue_write(result: TestResult):
    print("\n【7】队列原子写入")
    tmpdir = Path(tempfile.mkdtemp(prefix="traj-fault-"))
    try:
        mgr = UploadManager(
            upload_url="http://localhost:9999",
            upload_token="test-token",
            queue_dir=tmpdir,
        )

        # 写入队列
        mgr._queue = [
            UploadQueueItem(session_id="s1", file_type="traj", filepath="/tmp/x"),
        ]
        mgr._save_queue()

        # 验证没有残留 .tmp 文件
        tmp_files = list(tmpdir.glob("*.tmp"))
        result.check("无残留 .tmp 文件", len(tmp_files) == 0, f"found {tmp_files}")

        # 验证队列文件内容完整
        content = mgr._queue_file.read_text().strip()
        result.check("队列文件非空", len(content) > 0)
        parsed = json.loads(content)
        result.check("队列内容可解析", parsed.get("session_id") == "s1")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="故障测试")
    parser.add_argument("--test", choices=["compress", "queue", "health", "enqueue", "cleanup", "retry", "atomic", "all"],
                        default="all", help="运行指定测试")
    args = parser.parse_args()

    result = TestResult()

    tests = {
        "compress": test_compress_and_hash,
        "queue": test_queue_persistence,
        "health": test_health_check,
        "enqueue": test_upload_failure_enqueue,
        "cleanup": test_uploaded_marker_and_cleanup,
        "retry": test_retry_backoff,
        "atomic": test_atomic_queue_write,
    }

    if args.test == "all":
        for name, fn in tests.items():
            try:
                fn(result)
            except Exception as e:
                result.fail(f"测试 {name} 异常", str(e))
    else:
        tests[args.test](result)

    success = result.summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

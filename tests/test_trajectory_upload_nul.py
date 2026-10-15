"""轨迹上传：NUL 字符与 OSS 孤儿。

背景（2026-10）：生产 7 天 11 次 500（sqlstate=22021，PG text 不接受 0x00），
NUL 出现在 history[].content[].text 与 metadata.user_prompts[]。上传先存 OSS 再写 DB，
DB 失败后客户端重试命中「OSS 已有文件」→ 409 → 客户端当作已上传，会话永远成了孤儿。

锁住三件事：
- 带 NUL 的轨迹能入库，写进 DB 的字段不含 NUL，OSS 里存的仍是原字节；
- OSS 有文件、DB 无行时，不带 force 的重试会把它补进库（不是 409）；
- DB 已有行时照旧 409（客户端据此跳过）；解析失败（400）不在存储里留文件。

SQLite 本身接受 NUL，所以「不含 NUL」靠直接断言库里的值，不靠 PG 报错。
"""

from __future__ import annotations

import gzip
import json
import sqlite3

import pytest
from test_auth_login import client  # noqa: F401

@pytest.fixture
def store(tmp_path, monkeypatch):
    from app.modules.trajectory.router import upload as upload_mod
    from app.modules.trajectory.service.storage import LocalStorage

    s = LocalStorage(str(tmp_path / "data" / "sessions"))
    monkeypatch.setattr(upload_mod, "storage", s)
    return s


def _traj(session_id: str) -> bytes:
    data = {
        "metadata": {
            "session_id": session_id,
            "user_prompts": ["读一下\x00这个文件"],
            "working_directory": "/tmp/w\x00d",
        },
        "info": {},
        "history": [{"role": "user", "content": [{"type": "text", "text": "a\x00b"}]}],
        "trajectory": [
            {"message_type": "action", "tool_name": "bash", "tool_use_id": "t1",
             "tool_input": {"command": "echo \x00x"}},
        ],
    }
    # json.dumps 把 NUL 写成 \u0000，与客户端产物一致
    return gzip.compress(json.dumps(data).encode())


def _post(client, session_id: str, body: bytes, **params):
    from app.core.config import settings

    return client.post(
        "/api/v1/upload/session-file",
        headers={"X-Upload-Token": settings.data_plane.UPLOAD_TOKEN},
        params=params,
        data={"session_id": session_id, "file_type": "traj", "tool_source": "sid-code"},
        files={"file": ("session.traj.gz", body, "application/gzip")},
    )


def _row(db_path, session_id: str):
    con = sqlite3.connect(db_path)
    try:
        traj = con.execute(
            "select id, first_prompt, working_directory from trajectories where session_id = ?",
            (session_id,),
        ).fetchone()
        steps = con.execute(
            "select tool_input_summary from tool_steps where trajectory_id = ?", (traj[0],)
        ).fetchall() if traj else []
        return traj, steps
    finally:
        con.close()


def test_nul_traj_is_stored_without_nul_in_db(client, store):
    body = _traj("s-nul")
    resp = _post(client, "s-nul", body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "created"

    traj, steps = _row(client.db_path, "s-nul")
    assert traj[1] == "读一下这个文件"
    assert traj[2] == "/tmp/wd"
    assert steps == [("echo x",)]
    # 存储里是原字节，sha256 对得上原文件
    assert store.get("sessions/s-nul/session.traj.gz") == body


def test_orphan_in_storage_is_healed_by_retry_without_force(client, store):
    body = _traj("s-orphan")
    # 模拟事故现场：OSS 已有文件，DB 没有行
    store.put("sessions/s-orphan/session.traj.gz", body)

    resp = _post(client, "s-orphan", body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "created"
    assert _row(client.db_path, "s-orphan")[0] is not None


def test_existing_db_row_still_conflicts(client, store):
    body = _traj("s-dup")
    assert _post(client, "s-dup", body).status_code == 200
    again = _post(client, "s-dup", body)
    assert again.status_code == 409
    assert again.headers["X-Session-Id"] == "s-dup"


def test_invalid_traj_leaves_no_file(client, store):
    resp = _post(client, "s-bad", gzip.compress(b"{not json"))
    assert resp.status_code == 400
    assert not store.exists("sessions/s-bad/session.traj.gz")

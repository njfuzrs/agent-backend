"""失败可见的行为测试。只锁方案 §6 里 PR-L2 的断言：7–10、12。

断言用 caplog，强制 LOG_FORMAT=text。要锁的是字段值，不是 JSON 排版。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("AUTH_PASSWORD", "ci-not-a-secret")
os.environ.setdefault("UPLOAD_TOKEN", "ci-not-a-secret")
# 显式 text：本机 .env 若指向 PG，默认会是 json，caplog 的断言对不上。
os.environ["LOG_FORMAT"] = "text"

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture
def client(tmp_path, monkeypatch):
    """每个用例独立 SQLite，并给 reindex 一个空的 sessions 目录。"""
    db_path = tmp_path / "logging.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.core import db as db_mod
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", db_url)
    monkeypatch.setattr(settings.control_plane, "CTL_ENROLL_ENABLED", True)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setattr(settings.storage, "SESSIONS_DIR", str(sessions))
    monkeypatch.setattr(settings.storage, "TRAJ_FILES_DIR", str(tmp_path / "traj_files"))

    engine = create_async_engine(db_url, echo=False)

    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "engine", engine)
    monkeypatch.setattr(db_mod, "async_session", session_factory)

    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(alembic_cfg, "head")

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as tc:
        yield tc

    engine.sync_engine.dispose()


def rendered(caplog) -> str:
    """用线上的文本格式渲染。caplog.text 不含字段，断言要看的是 journald 那一行。"""
    from app.core.logging import TextFormatter

    fmt = TextFormatter()
    own = [r for r in caplog.records if r.name == "agent" or r.name.startswith("agent.")]
    return "\n".join(fmt.format(r) for r in own)


def named(caplog, name: str) -> list:
    return [r for r in caplog.records if r.name == name]


# ---------------------------------------------------------------------------
# 断言 7：错误的上传 token 只产生一条 agent.auth warning，且不含 token
# ---------------------------------------------------------------------------
def test_bad_upload_token_logs_one_rejection(client, caplog):
    token = "wrong-token-value-should-not-leak-0123456789"

    with caplog.at_level(logging.WARNING, logger="agent"):
        resp = client.post(
            "/api/v1/upload/session-file",
            headers={"X-Upload-Token": token},
            data={"session_id": "sess-1", "file_type": "traj"},
            files={"file": ("session.traj", b"{}", "application/octet-stream")},
        )

    assert resp.status_code == 401, resp.text
    auth = named(caplog, "agent.auth")
    assert len(auth) == 1
    assert auth[0].levelno == logging.WARNING
    text = rendered(caplog)
    assert "event=auth_rejected" in text
    assert "reason=token_rejected" in text
    # token 的任何子串都不进日志。
    assert token not in text
    assert "wrong-token" not in text


def test_missing_bearer_logs_reason(client, caplog):
    """没有 Authorization 头是 missing_bearer，与头坏了分开。"""
    with caplog.at_level(logging.WARNING, logger="agent.auth"):
        resp = client.post("/api/v1/events", json={"events": []})
    assert resp.status_code == 401
    assert "reason=missing_bearer" in rendered(caplog)


def test_malformed_bearer_logs_reason(client, caplog):
    with caplog.at_level(logging.WARNING, logger="agent.auth"):
        resp = client.post(
            "/api/v1/events",
            json={"events": []},
            headers={"Authorization": "Token abc"},
        )
    assert resp.status_code == 401
    assert "reason=malformed" in rendered(caplog)


def test_unknown_credential_logs_reason(client, caplog):
    with caplog.at_level(logging.WARNING, logger="agent.auth"):
        resp = client.post(
            "/api/v1/events",
            json={"events": []},
            headers={"Authorization": "Bearer this-credential-does-not-exist"},
        )
    assert resp.status_code == 401
    text = rendered(caplog)
    assert "reason=unknown" in text
    assert "this-credential-does-not-exist" not in text


def test_enroll_with_bad_code_logs_rejection(client, caplog):
    """注册码不对记 enroll_rejected，不记码的任何片段。"""
    code = "enroll-code-that-does-not-exist"

    with caplog.at_level(logging.WARNING, logger="agent.auth"):
        resp = client.post(
            "/api/v1/ctl/enroll",
            json={"device_id": "dev-1"},
            headers={"X-Enroll-Token": code},
        )

    assert resp.status_code == 401
    auth = named(caplog, "agent.auth")
    assert len(auth) == 1
    assert "reason=enroll_rejected" in rendered(caplog)
    assert code not in rendered(caplog)


def test_basic_auth_rejection_logs_reason(client, caplog):
    """口令不对记 basic_rejected，不记尝试的口令。"""
    with caplog.at_level(logging.WARNING, logger="agent.auth"):
        resp = client.get(
            "/api/v1/trajectories",
            auth=("admin", "guessed-password"),
        )
    assert resp.status_code == 401
    text = rendered(caplog)
    assert "reason=basic_rejected" in text
    assert "guessed-password" not in text


# ---------------------------------------------------------------------------
# 断言 8：未捕获异常返回 500，body 不含异常消息、含 request_id，日志含栈
# ---------------------------------------------------------------------------
def test_unhandled_exception_hides_message_and_logs_stack(client, caplog, monkeypatch):
    from app.modules.trajectory.router import trajectories as traj_router

    secret = "boom-secret-detail-should-not-leak"

    async def _explode(session_id: str, db):  # noqa: ARG001 — 签名要与原函数一致
        raise RuntimeError(secret)

    monkeypatch.setattr(traj_router, "_get_traj_or_404", _explode)

    from app.core.config import settings

    with caplog.at_level(logging.ERROR, logger="agent.access"), caplog.at_level(
        logging.ERROR, logger="agent"
    ):
        resp = client.get(
            "/api/v1/trajectories/sess-1/detail/info",
            auth=(settings.AUTH_USERNAME, settings.AUTH_PASSWORD),
        )

    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"] == "internal error"
    assert body["request_id"]
    assert secret not in resp.text

    errors = [r for r in named(caplog, "agent") if r.levelno == logging.ERROR]
    assert len(errors) == 1
    text = rendered(caplog)
    assert "event=unhandled_exception" in text
    assert "exc_type=RuntimeError" in text
    # 栈在日志里，不在响应里。
    assert "RuntimeError" in text
    assert secret in text
    assert f"request_id={body['request_id']}" in text
    # 5xx 的访问日志是 error，且与兜底日志同一个编号。
    access = named(caplog, "agent.access")
    assert len(access) == 1
    assert access[0].levelno == logging.ERROR
    assert "status=500" in rendered(caplog)


# ---------------------------------------------------------------------------
# 断言 9：数据库异常只留 sqlstate 与固定文本，没有绑定参数
# ---------------------------------------------------------------------------
def test_db_error_log_omits_statement_and_params():
    """直接构造 DBAPIError。不走真实数据库：要锁的是裁剪，不是驱动。"""
    from sqlalchemy.exc import DBAPIError

    from app.core.logging import db_error_fields

    class _Orig(Exception):
        sqlstate = "23505"

    secret = "bound-param-should-not-leak"
    exc = DBAPIError(
        statement="INSERT INTO t (secret) VALUES (%s)",
        params={"secret": secret},
        orig=_Orig("duplicate"),
    )

    fields, logged = db_error_fields(exc)
    assert fields["exc_type"] == "DBAPIError"
    assert fields["sqlstate"] == "23505"
    assert secret not in str(logged)
    assert "INSERT" not in str(logged)
    assert str(logged) == "db error, statement omitted (DBAPIError)"


def test_db_error_without_sqlstate_omits_the_field():
    from sqlalchemy.exc import DBAPIError

    from app.core.logging import db_error_fields

    exc = DBAPIError(statement="SELECT 1", params=None, orig=Exception("down"))
    fields, _logged = db_error_fields(exc)
    assert "sqlstate" not in fields


def test_commit_failure_log_omits_statement(client, caplog, monkeypatch):
    """last_seen_at 提交失败走 agent.db。栈里只有固定文本，没有语句与参数。

    鉴权本身仍然成功：心跳写失败不撤销已经授予的信任。
    """
    from sqlalchemy.exc import DBAPIError

    from app.core import db as db_mod

    code = _issue_code(client)
    cred = _enroll(client, code, device_id="dev-commit")

    secret = "commit-param-should-not-leak"
    real_factory = db_mod.async_session

    def _factory():
        session = real_factory()

        async def _fail():
            raise DBAPIError(
                statement="UPDATE device_credentials SET expires_at=%s",
                params={"expires_at": secret},
                orig=Exception("disk full"),
            )

        session.commit = _fail
        return session

    # 只包住控制面那个会话。enroll 走 get_db，不受影响。
    monkeypatch.setattr(db_mod, "async_session", _factory)

    with caplog.at_level(logging.ERROR, logger="agent.db"):
        resp = client.get(
            "/api/v1/ctl/whoami",
            headers={"Authorization": f"Bearer {cred}"},
        )

    assert resp.status_code == 200, resp.text
    text = rendered(caplog)
    assert "event=commit_failed" in text
    assert "db error, statement omitted (DBAPIError)" in text
    assert secret not in text
    assert "UPDATE device_credentials" not in text


def _login(client) -> None:
    from app.core.config import settings

    resp = client.post(
        "/api/v1/auth/login",
        json={"username": settings.AUTH_USERNAME, "password": settings.AUTH_PASSWORD},
    )
    assert resp.status_code == 200, resp.text


def _issue_code(client) -> str:
    _login(client)
    resp = client.post(
        "/api/v1/identity/enroll-codes",
        json={"org_id": "corp", "org_name": "corp", "note": "t"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["code"]


def _enroll(client, code: str, device_id: str) -> str:
    resp = client.post(
        "/api/v1/ctl/enroll",
        json={"device_id": device_id, "user_id": "u", "org_id": "corp"},
        headers={"X-Enroll-Token": code},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["credential"]


def test_non_db_error_is_logged_unchanged():
    """裁剪只针对数据库异常。别的异常照记完整栈。"""
    from app.core.logging import db_error_fields

    exc = RuntimeError("plain")
    fields, logged = db_error_fields(exc)
    assert fields == {"exc_type": "RuntimeError"}
    assert logged is exc


# ---------------------------------------------------------------------------
# 断言 10：422 的响应不含提交的字段值
# ---------------------------------------------------------------------------
def test_validation_error_does_not_echo_input(client, caplog):
    """管理台写入缺字段走 Pydantic。响应只留固定短句，值不回显。"""
    from app.core.config import settings

    login = client.post(
        "/api/v1/auth/login",
        json={"username": settings.AUTH_USERNAME, "password": settings.AUTH_PASSWORD},
    )
    assert login.status_code == 200, login.text

    leaked = "secret-value-should-not-echo"
    with caplog.at_level(logging.WARNING, logger="agent"):
        resp = client.post(
            "/api/v1/budgets",
            json={
                "scope_type": "org",
                "scope_id": "corp",
                "org_id": "corp",
                "period": "monthly",
                "limit_usd": leaked,
                "reason": "x",
            },
        )

    assert resp.status_code == 422
    body = resp.json()
    assert body["detail"] == "invalid request"
    assert leaked not in resp.text
    assert body["request_id"]
    # 日志记哪些字段没过，不记值。
    text = rendered(caplog)
    assert "reason=invalid_request" in text
    assert "fields=body.limit_usd" in text
    assert leaked not in text


# ---------------------------------------------------------------------------
# 断言 12：reindex 单文件解析失败记一条 warning，接口仍返回 errors 计数
# ---------------------------------------------------------------------------
def test_reindex_logs_parse_failure_and_counts_it(client, caplog, tmp_path, monkeypatch):
    """reindex 读的是模块导入时定下的 SESSIONS_DIR 常量，改 settings 对它无效。"""
    import app.modules.trajectory.router.upload as upload_router
    from app.core.config import settings

    sessions = tmp_path / "reindex-sessions"
    sessions.mkdir()
    monkeypatch.setattr(upload_router, "SESSIONS_DIR", sessions)

    bad = sessions / "broken-session"
    bad.mkdir()
    (bad / "session.traj").write_bytes(b"this is not json")

    with caplog.at_level(logging.WARNING, logger="agent.trajectory"):
        resp = client.post(
            "/api/v1/upload/reindex",
            headers={"X-Upload-Token": settings.UPLOAD_TOKEN},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["errors"] == 1
    warnings = [r for r in named(caplog, "agent.trajectory") if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    text = rendered(caplog)
    assert "event=reindex_failed" in text
    assert "exc_type=JSONDecodeError" in text
    # 记相对路径，不记部署目录。
    assert "broken-session/session.traj" in text
    assert str(tmp_path) not in text


# ---------------------------------------------------------------------------
# §3.6 其余失败点：校验和不回传两端哈希、批量失败不回传异常原文
# ---------------------------------------------------------------------------
def test_checksum_mismatch_logs_reason_without_hashes(client, caplog):
    from app.core.config import settings

    with caplog.at_level(logging.WARNING, logger="agent.trajectory"):
        resp = client.post(
            "/api/v1/upload/session-file",
            headers={
                "X-Upload-Token": settings.UPLOAD_TOKEN,
                "X-Content-SHA256": "deadbeef" * 8,
            },
            data={"session_id": "sess-9", "file_type": "traj"},
            files={"file": ("session.traj", b"{}", "application/octet-stream")},
        )

    assert resp.status_code == 400
    text = rendered(caplog)
    assert "event=upload_rejected" in text
    assert "reason=checksum_mismatch" in text
    assert "session_id=sess-9" in text
    # 两端哈希都不进日志。响应里仍回（客户端对账要用），这是既有契约。
    assert "deadbeef" not in text


def test_batch_failure_returns_fixed_reason(client, caplog):
    """坏文件不再把 str(exc) 回给客户端，日志记 exc_type。"""
    from app.core.config import settings

    with caplog.at_level(logging.ERROR, logger="agent.trajectory"):
        resp = client.post(
            "/api/v1/upload/batch",
            headers={"X-Upload-Token": settings.UPLOAD_TOKEN},
            data={"tool_source": "claude-code"},
            files={"files": ("bad.traj", b"not-json", "application/octet-stream")},
        )

    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert results[0]["status"] == "error"
    assert results[0]["reason"] == "parse_error"
    assert "Expecting value" not in resp.text
    assert "event=upload_failed" in rendered(caplog)
    assert "exc_type=JSONDecodeError" in rendered(caplog)


def test_parse_failure_on_detail_is_logged(client, caplog, monkeypatch):
    """已入库的文件损坏。详情端点记 parse_failed，响应不带解析原文。"""
    from app.modules.trajectory.router import trajectories as traj_router

    class _Traj:
        session_id = "sess-corrupt"
        oss_key = None
        traj_file_path = "sessions/sess-corrupt/session.traj"

    async def _found(session_id, db):  # noqa: ARG001
        return _Traj()

    monkeypatch.setattr(traj_router, "_get_traj_or_404", _found)
    monkeypatch.setattr(traj_router, "_read_traj_content", lambda traj: b"not-json")

    from app.core.config import settings

    with caplog.at_level(logging.WARNING, logger="agent.trajectory"):
        resp = client.get(
            "/api/v1/trajectories/sess-corrupt/detail/info",
            auth=(settings.AUTH_USERNAME, settings.AUTH_PASSWORD),
        )

    assert resp.status_code == 500
    assert "not-json" not in resp.text
    text = rendered(caplog)
    assert "event=parse_failed" in text
    assert "session_id=sess-corrupt" in text

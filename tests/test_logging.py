"""日志内核的行为测试。只锁方案 §6 里 PR-L1 的断言：1–6、11、13。

不测「日志框架」的矩阵。断言用 caplog，强制 LOG_FORMAT=text——
JSON 里多一个空格，文本断言就全红，而这里要锁的是字段值不是排版。
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
    """每个用例独立 SQLite。与 test_flag 同一套装置，日志测试不该依赖别的模块的库。"""
    db_path = tmp_path / "logging.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.core import db as db_mod
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", db_url)

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


def _access(caplog):
    """只看访问日志。别的 logger 的记录不算。"""
    return [r for r in caplog.records if r.name == "agent.access"]


def rendered(caplog) -> str:
    """用线上的文本格式渲染 caplog 抓到的记录。

    caplog.text 走 pytest 自己的格式，不含字段。断言要看的是 journald 里的那一行。
    """
    from app.core.logging import TextFormatter

    fmt = TextFormatter()
    # 只渲染我们自己的记录。同一次 caplog 里还有 alembic 的迁移日志，
    # 那些记录没有 agent_* 字段，拿去格式化会抛，整段断言就变成空字符串。
    own = [r for r in caplog.records if r.name == "agent" or r.name.startswith("agent.")]
    return "\n".join(fmt.format(r) for r in own)


# ---------------------------------------------------------------------------
# 断言 1：agent 的 INFO 在默认配置下可见
# ---------------------------------------------------------------------------
def test_agent_logger_info_is_emitted(caplog):
    """直接防止退回「只有 uvicorn.error 才看得见」。"""
    from app.core.logging import get_logger

    with caplog.at_level(logging.INFO, logger="agent"):
        get_logger("agent").info("schema checked", event="schema_checked", outcome="ok")

    assert any(r.name == "agent" and r.levelno == logging.INFO for r in caplog.records)


# ---------------------------------------------------------------------------
# 断言 2：非 agent. 前缀的名字拒绝
# ---------------------------------------------------------------------------
def test_logger_name_outside_prefix_is_rejected():
    from app.core.logging import get_logger

    with pytest.raises(ValueError):
        get_logger("app.modules.trajectory")


# ---------------------------------------------------------------------------
# 断言 3：200 响应带 X-Request-ID，访问日志与它相同，且带 version
# ---------------------------------------------------------------------------
def test_request_id_roundtrips_and_version_present(client, caplog, monkeypatch):
    from app.core.logging import reset_version_cache

    monkeypatch.setenv("AGENT_VERSION", "abc1234")
    reset_version_cache()

    with caplog.at_level(logging.INFO, logger="agent.access"):
        resp = client.get("/api/v1/ctl/budget")

    assert resp.status_code == 401
    request_id = resp.headers["x-request-id"]
    assert request_id

    access = _access(caplog)
    assert len(access) == 1
    text = rendered(caplog)
    assert f"request_id={request_id}" in text
    assert "version=abc1234" in text

    reset_version_cache()


# ---------------------------------------------------------------------------
# 断言 4：含换行的 X-Request-ID 被丢弃
# ---------------------------------------------------------------------------
def test_request_id_with_newline_is_discarded(client, caplog):
    forged = "abcdef12\nforged-line"

    with caplog.at_level(logging.INFO, logger="agent.access"):
        resp = client.get("/api/v1/ctl/budget", headers={"X-Request-ID": forged})

    generated = resp.headers["x-request-id"]
    assert "\n" not in generated
    assert generated != forged
    # 伪造的那半行不能出现在任何日志里，否则一条日志就被拆成了两条。
    assert "forged-line" not in rendered(caplog)
    assert f"request_id={generated}" in rendered(caplog)


# ---------------------------------------------------------------------------
# 断言 5：health 的 200 不记；非 2xx 照记
# ---------------------------------------------------------------------------
def test_health_200_is_quiet_but_failure_is_logged(client, caplog):
    with caplog.at_level(logging.DEBUG, logger="agent.access"):
        resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert _access(caplog) == []

    # 跳过规则只认路由模板 + 2xx。换成返回 503 的 ASGI app，模板不变，
    # 所以这条断言锁的是「失败不被吞」，不是「换了条路由」。
    # 改 route.endpoint 不生效：Starlette 在注册时就把 endpoint 编译进 route.app，
    # 运行时调用的是后者。
    from starlette.responses import JSONResponse

    from app.main import app, health

    async def _down(scope, receive, send):
        await JSONResponse({"detail": "down"}, status_code=503)(scope, receive, send)

    route = next(r for r in app.routes if getattr(r, "endpoint", None) is health)
    original = route.app
    route.app = _down
    try:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="agent.access"):
            resp = client.get("/api/v1/health")
    finally:
        route.app = original

    assert resp.status_code == 503
    access = _access(caplog)
    assert len(access) == 1
    assert access[0].levelno == logging.ERROR
    assert "route=/api/v1/health" in rendered(caplog)


# ---------------------------------------------------------------------------
# 断言 6：flags 的快速 200 不产生 info；慢的产生
# ---------------------------------------------------------------------------
def test_flags_fast_200_is_debug_only(client, caplog):
    with caplog.at_level(logging.INFO, logger="agent.access"):
        resp = client.get("/api/v1/ctl/flags")
    assert resp.status_code == 200
    assert _access(caplog) == []

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="agent.access"):
        client.get("/api/v1/ctl/flags")
    access = _access(caplog)
    assert len(access) == 1
    assert access[0].levelno == logging.DEBUG


def test_flags_slow_200_is_logged_at_info(client, caplog, monkeypatch):
    """耗时达到阈值就升到 info。用假时钟，不真的睡一秒。

    只替换中间件自己的时钟。替换 time.monotonic 会冻结 sqlite 与 httpx 的超时，
    整组测试卡死（2026-09-24 实测）。
    """
    import app.core.middleware as mw

    ticks = iter([0.0, mw.SLOW_REQUEST_MS / 1000 + 0.01])
    monkeypatch.setattr(mw, "_now", lambda: next(ticks))

    with caplog.at_level(logging.INFO, logger="agent.access"):
        resp = client.get("/api/v1/ctl/flags")
    assert resp.status_code == 200
    access = _access(caplog)
    assert len(access) == 1
    assert access[0].levelno == logging.INFO


# ---------------------------------------------------------------------------
# 断言 11：白名单精确匹配
# ---------------------------------------------------------------------------
def test_whitelist_rejects_password_but_accepts_target_id():
    """password 拒绝；target_id 通过。防止谁把精确匹配改成子串匹配。"""
    from app.core.logging import get_logger

    logger = get_logger("agent.flag")
    with pytest.raises(TypeError):
        logger.info("flag written", password="secret")

    # 不抛就是通过。target_id 含不了被拒的子串，但这条断言锁的是「名字本身被接受」。
    logger.info("flag written", event="admin_write", target_id="flag-1")


def test_whitelist_rejects_forged_request_id():
    """框架字段调用点传了也拒绝。伪造 request_id 会让按编号排查失效。"""
    from app.core.logging import get_logger

    with pytest.raises(TypeError):
        get_logger("agent").info("schema checked", request_id="forged-by-caller")


# ---------------------------------------------------------------------------
# 断言 13：route 是模板，不含真实的 session_id
# ---------------------------------------------------------------------------
def test_access_log_route_is_template_not_real_path(client, caplog):
    session_id = "11111111-2222-3333-4444-555555555555"

    with caplog.at_level(logging.INFO, logger="agent.access"):
        resp = client.get(f"/api/v1/trajectories/{session_id}")

    # 无会话是 401，但路由已经匹配上了，模板拿得到。
    assert resp.status_code == 401
    access = _access(caplog)
    assert len(access) == 1
    assert "route=/api/v1/trajectories/{session_id}" in rendered(caplog)
    assert session_id not in rendered(caplog).split("route=")[1]


def test_unmatched_path_logs_dash_route(client, caplog):
    """404 没有模板。记 -，而不是把真实路径填进 route。"""
    with caplog.at_level(logging.INFO, logger="agent.access"):
        resp = client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404
    assert "route=-" in rendered(caplog)

"""审计关联与就绪的行为测试。锁方案 §8 里 PR-L3 的完成标准。

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
    """每个用例独立 SQLite。与 test_logging 同一套装置。"""
    db_path = tmp_path / "logging.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.core import db as db_mod
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", db_url)
    monkeypatch.setattr(settings.control_plane, "CTL_ENROLL_ENABLED", True)

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
    """用线上的文本格式渲染 caplog 抓到的记录。

    caplog.text 走 pytest 自己的格式，不含字段。断言要看的是 journald 里的那一行。
    """
    from app.core.logging import TextFormatter

    fmt = TextFormatter()
    own = [r for r in caplog.records if r.name == "agent" or r.name.startswith("agent.")]
    return "\n".join(fmt.format(r) for r in own)


def named(caplog, name: str):
    return [r for r in caplog.records if r.name == name]


def _login(client) -> None:
    from app.core.config import settings

    resp = client.post(
        "/api/v1/auth/login",
        json={"username": settings.AUTH_USERNAME, "password": settings.AUTH_PASSWORD},
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# 一次 policy 变更：审计行的 request_id 与 actor 都对得上 admin_write
# ---------------------------------------------------------------------------
def test_policy_change_links_audit_row_to_admin_write(client, caplog):
    """完成标准的前半句：审计行能跳回同一条 admin_write，两边 actor 逐字相同。

    用响应头里的 X-Request-ID 当连接点。生产上这一步是 journald 检索，
    测试里没有 journald，caplog 就是那条日志。
    """
    _login(client)
    client.post(
        "/api/v1/identity/organizations",
        json={"org_id": "corp-shanghai", "name": "上海"},
    )

    with caplog.at_level(logging.INFO, logger="agent"):
        resp = client.post(
            "/api/v1/policies",
            json={
                "scope_type": "org",
                "scope_id": "corp-shanghai",
                "org_id": "corp-shanghai",
                "settings": {"permissions": {"deny": ["Bash(curl *)"]}},
                "reason": "上线",
            },
            headers={"X-Request-ID": "policy-change-0001"},
        )

    assert resp.status_code == 201, resp.text
    assert resp.headers["x-request-id"] == "policy-change-0001"

    writes = named(caplog, "agent.policy")
    assert len(writes) == 1
    text = rendered(caplog)
    assert "event=admin_write" in text
    assert "action=create" in text
    assert "request_id=policy-change-0001" in text
    # reason 在审计表里，不进日志。
    assert "上线" not in text

    from app.core.config import settings

    audit = client.get("/api/v1/policies/audit").json()["items"]
    created = next(a for a in audit if a["action"] == "create")
    assert created["request_id"] == "policy-change-0001"
    assert created["actor"] == settings.AUTH_USERNAME
    assert f"actor={settings.AUTH_USERNAME}" in text


def test_flag_and_budget_changes_carry_request_id(client, caplog):
    """三张表同一条规则。flag 与 budget 也要能从审计行跳回日志。"""
    _login(client)
    client.post(
        "/api/v1/identity/organizations",
        json={"org_id": "corp-shanghai", "name": "上海"},
    )

    with caplog.at_level(logging.INFO, logger="agent"):
        created = client.post(
            "/api/v1/flags",
            json={"key": "max_turns_limit", "value": 20, "reason": "初始值"},
            headers={"X-Request-ID": "flag-change-00001"},
        )
    assert created.status_code == 201, created.text
    flag_audit = client.get("/api/v1/flags/audit").json()["items"]
    assert flag_audit[0]["request_id"] == "flag-change-00001"
    assert "request_id=flag-change-00001" in rendered(caplog)

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="agent"):
        budget = client.post(
            "/api/v1/budgets",
            json={
                "scope_type": "org",
                "scope_id": "corp-shanghai",
                "org_id": "corp-shanghai",
                "period": "monthly",
                "limit_usd": 100.0,
                "reason": "季度预算",
            },
            headers={"X-Request-ID": "budget-change-0001"},
        )
    assert budget.status_code == 201, budget.text
    budget_audit = client.get("/api/v1/budgets/audit").json()["items"]
    assert budget_audit[0]["request_id"] == "budget-change-0001"
    assert "request_id=budget-change-0001" in rendered(caplog)


def test_audit_request_id_stays_empty_without_a_request(client):
    """脚本直接改库没有请求。取不到就留空，不为它编造编号。

    client 参数只为了让库先建好。这条路径本身不经过任何请求。
    """
    import asyncio

    from sqlalchemy import select

    from app.core import db as db_mod
    from app.core.logging import clear_context
    from app.modules.flag.model import FeatureFlagAudit
    from app.modules.flag.schemas import FlagCreate
    from app.modules.flag.service import flags as flag_service

    clear_context()

    async def _run():
        async with db_mod.async_session() as db:
            await flag_service.create_flag(
                db, FlagCreate(key="max_turns_limit", value=20, reason="脚本"), actor="script"
            )
            row = await db.execute(select(FeatureFlagAudit))
            return row.scalar_one()

    audit = asyncio.run(_run())
    assert audit.request_id is None
    assert audit.actor == "script"


# ---------------------------------------------------------------------------
# identity 的写操作：吊销、签发注册码、enroll 成功
# ---------------------------------------------------------------------------
def test_revoke_and_enroll_code_are_logged(client, caplog):
    """identity 没有审计表，这两条日志是唯一的运行记录。注册码明文不进日志。"""
    _login(client)

    with caplog.at_level(logging.INFO, logger="agent"):
        issued = client.post(
            "/api/v1/identity/enroll-codes",
            json={"org_id": "corp-shanghai", "org_name": "上海", "note": "test"},
        )
    assert issued.status_code == 201, issued.text
    code = issued.json()["code"]
    text = rendered(caplog)
    assert "event=admin_write" in text
    assert "action=enroll_code" in text
    assert "target_id=corp-shanghai" in text
    assert code not in text

    enrolled = client.post(
        "/api/v1/ctl/enroll",
        json={"device_id": "dev-1", "org_id": "corp-shanghai"},
        headers={"X-Enroll-Token": code},
    )
    assert enrolled.status_code == 201, enrolled.text

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="agent"):
        revoked = client.post("/api/v1/identity/devices/dev-1/revoke")
    assert revoked.status_code == 200, revoked.text
    text = rendered(caplog)
    assert "event=admin_write" in text
    assert "action=revoke" in text
    assert "target_id=dev-1" in text


def test_enroll_success_is_logged(client, caplog):
    """enroll 成功记 device_enrolled，带 device_id 与 org_id，不记码。"""
    _login(client)
    issued = client.post(
        "/api/v1/identity/enroll-codes",
        json={"org_id": "corp-shanghai", "org_name": "上海"},
    )
    code = issued.json()["code"]

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="agent"):
        resp = client.post(
            "/api/v1/ctl/enroll",
            json={"device_id": "dev-9", "org_id": "corp-shanghai"},
            headers={"X-Enroll-Token": code},
        )
    assert resp.status_code == 201, resp.text

    enrolled = [r for r in named(caplog, "agent.identity") if "device_enrolled" in rendered_one(r)]
    assert len(enrolled) == 1
    text = rendered_one(enrolled[0])
    assert "device_id=dev-9" in text
    assert "org_id=corp-shanghai" in text
    assert code not in text
    assert resp.json()["credential"] not in text


def rendered_one(record) -> str:
    from app.core.logging import TextFormatter

    return TextFormatter().format(record)


# ---------------------------------------------------------------------------
# 就绪检查：数据库停了返回 503 且有一条 error，health 仍为 200
# ---------------------------------------------------------------------------
def test_ready_ok_is_quiet(client, caplog):
    """成功不打日志。本机每 60 秒一条的话，和 health 是同一个刷屏问题。"""
    with caplog.at_level(logging.DEBUG, logger="agent"):
        resp = client.get("/api/v1/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert named(caplog, "agent") == []
    # 2xx 也不进访问日志。
    assert named(caplog, "agent.access") == []


def test_ready_reports_db_down_while_health_stays_ok(client, caplog, monkeypatch):
    """完成标准的后半句：停掉数据库时 ready 返回 503 且有一条 error，health 仍为 200。

    不停真的库。把探活换成一个立刻失败的调用，失败点与「库连不上」相同。
    """
    import app.core.ready as ready_mod

    async def _down():
        raise ConnectionError("database is down")

    monkeypatch.setattr(ready_mod, "_select_one", _down)

    with caplog.at_level(logging.ERROR, logger="agent"):
        resp = client.get("/api/v1/ready")
    assert resp.status_code == 503
    assert resp.json() == {"status": "db_unavailable"}
    # 异常消息里可能有语句和参数，不能进响应，也不能进日志。
    assert "database is down" not in resp.text
    text = rendered(caplog)
    assert "database is down" not in text
    assert "event=ready_failed" in text
    assert "outcome=db_unavailable" in text
    assert "exc_type=ConnectionError" in text

    errors = [r for r in named(caplog, "agent") if r.levelno == logging.ERROR]
    assert len(errors) == 1

    # health 是「进程活着」，不探库。ready 失败不能把它拖成非 200。
    health = client.get("/api/v1/health")
    assert health.status_code == 200


def test_ready_timeout_is_db_unavailable(client, caplog, monkeypatch):
    """超时与连不上是同一个结果：503，一条 error。不真等那一秒。"""
    import app.core.ready as ready_mod

    monkeypatch.setattr(ready_mod, "DB_TIMEOUT_SECONDS", 0.01)

    async def _slow():
        import asyncio

        await asyncio.sleep(1)

    monkeypatch.setattr(ready_mod, "_select_one", _slow)

    with caplog.at_level(logging.ERROR, logger="agent"):
        resp = client.get("/api/v1/ready")
    assert resp.status_code == 503
    assert resp.json() == {"status": "db_unavailable"}
    assert "event=ready_failed" in rendered(caplog)

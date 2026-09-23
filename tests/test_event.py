"""event 模块：上报契约 / 幂等 / 白名单 / 两个固定聚合。

规划 §4 M4 验收：
- POST /events 无凭据 401（不是 202）；X-Upload-Token 当鉴权也 401
- 同一批重复投两次 → 第二次 deduped=3、库里仍 3 行（幂等是本模块核心用例）
- org_id 从凭据取，body 里塞攻击者的 org 无效
- 未知 eventName / 嵌套 metadata → 202 rejected，不退整批
- 501 条 → 413；缺 events 键 → 400
- session-coverage 含「有轨迹零事件」；policy-audit 拆真报/误报
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("AUTH_PASSWORD", "ci-not-a-secret")
os.environ.setdefault("UPLOAD_TOKEN", "ci-not-a-secret")

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture
def client(tmp_path, monkeypatch):
    """每个用例独立 SQLite：改 settings 原地字段，不重建对象（各模块已持有引用）。"""
    db_path = tmp_path / "event.db"
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


def _login(client) -> None:
    from app.core.config import settings

    resp = client.post(
        "/api/v1/auth/login",
        json={"username": settings.AUTH_USERNAME, "password": settings.AUTH_PASSWORD},
    )
    assert resp.status_code == 200, resp.text


def _issue_code(client, org_id: str = "corp-shanghai", team_id: str | None = "infra") -> str:
    _login(client)
    body = {"org_id": org_id, "org_name": org_id, "note": "test"}
    if team_id:
        body["team_id"] = team_id
    resp = client.post("/api/v1/identity/enroll-codes", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["code"]


def _enroll(
    client,
    code: str,
    device_id: str,
    org_id: str = "corp-shanghai",
    team_id: str | None = "infra",
) -> str:
    body = {
        "device_id": device_id,
        "user_id": "zhangsan@corp.com",
        "org_id": org_id,
        "platform": "darwin",
        "ver": "0.1.604",
    }
    if team_id:
        body["team_id"] = team_id
    resp = client.post(
        "/api/v1/ctl/enroll",
        json=body,
        headers={"X-Enroll-Token": code},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["credential"]


def _event(
    name: str = "tool_call",
    ts: int = 1_780_557_354_650,
    metadata: dict | None = None,
) -> dict:
    meta = {
        "_ctx_session_id": "20260922-200236-35147fb4",
        "_ctx_platform": "darwin",
        "tool_name": "Bash",
    }
    if metadata is not None:
        meta.update(metadata)
    return {"eventName": name, "timestamp": ts, "metadata": meta}


def _post(client, cred: str, events: list):
    return client.post(
        "/api/v1/events",
        json={"events": events},
        headers={"Authorization": f"Bearer {cred}"},
    )


def _db_count(client) -> int:
    """管理台列表的 total = 库里行数（测试里事件很少，limit 盖得住）。"""
    _login(client)
    resp = client.get("/api/v1/events", params={"limit": 500})
    assert resp.status_code == 200, resp.text
    return resp.json()["total"]


# ---------------------------------------------------------------------------
# 鉴权 fail-closed
# ---------------------------------------------------------------------------
def test_unauthenticated_post_is_401(client):
    resp = client.post("/api/v1/events", json={"events": []})
    assert resp.status_code == 401, resp.text
    assert resp.status_code != 202


def test_bad_bearer_is_401(client):
    resp = client.post(
        "/api/v1/events",
        json={"events": []},
        headers={"Authorization": "Bearer totally-not-a-credential"},
    )
    assert resp.status_code == 401


def test_upload_token_without_bearer_is_401(client):
    from app.core.config import settings

    resp = client.post(
        "/api/v1/events",
        json={"events": [_event()]},
        headers={"X-Upload-Token": settings.data_plane.UPLOAD_TOKEN},
    )
    assert resp.status_code == 401


def test_upload_token_cannot_read_events(client):
    from app.core.config import settings

    resp = client.get(
        "/api/v1/events",
        headers={"X-Upload-Token": settings.data_plane.UPLOAD_TOKEN},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 上报：202 / 幂等 / 身份从凭据
# ---------------------------------------------------------------------------
def test_empty_array_is_202_zeros(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-empty")
    resp = _post(client, cred, [])
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"accepted": 0, "deduped": 0, "rejected": 0}


def test_missing_events_key_is_400(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-shape")
    resp = client.post(
        "/api/v1/events",
        json={"not_events": []},
        headers={"Authorization": f"Bearer {cred}"},
    )
    assert resp.status_code == 400, resp.text


def test_batch_of_three_accepted(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-ok")
    events = [
        _event("tool_call", 1_780_557_354_650),
        _event("tool_success", 1_780_557_354_651),
        _event("permission_deny", 1_780_557_354_652),
    ]
    resp = _post(client, cred, events)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["accepted"] == 3
    assert body["deduped"] == 0
    assert body["rejected"] == 0
    assert _db_count(client) == 3


def test_same_batch_posted_twice_is_deduped(client):
    """核心用例：退避 + 磁盘重放会重复投递，第二次必须全部去重、库里仍 3 行。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-idem")
    events = [
        _event("tool_call", 1_780_557_354_660),
        _event("tool_success", 1_780_557_354_661),
        _event("tool_failure", 1_780_557_354_662),
    ]
    first = _post(client, cred, events)
    assert first.status_code == 202, first.text
    assert first.json()["accepted"] == 3

    second = _post(client, cred, events)
    assert second.status_code == 202, second.text
    assert second.json()["accepted"] == 0
    assert second.json()["deduped"] == 3
    assert second.json()["rejected"] == 0
    assert _db_count(client) == 3


def test_same_content_different_device_are_two_rows(client):
    code_a = _issue_code(client)
    cred_a = _enroll(client, code_a, "dev-a")
    code_b = _issue_code(client)
    cred_b = _enroll(client, code_b, "dev-b")
    ev = [_event("tool_call", 1_780_557_354_670)]
    assert _post(client, cred_a, ev).json()["accepted"] == 1
    assert _post(client, cred_b, ev).json()["accepted"] == 1
    assert _db_count(client) == 2


def test_same_content_timestamp_plus_1ms_are_two_rows(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-ts")
    a = _post(client, cred, [_event("tool_call", 1_780_557_354_680)])
    b = _post(client, cred, [_event("tool_call", 1_780_557_354_681)])
    assert a.json()["accepted"] == 1
    assert b.json()["accepted"] == 1
    assert _db_count(client) == 2


def test_body_org_id_is_ignored(client):
    """body 里塞 org_id 不能改归属 —— 库里必须是凭据的那个。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-org")
    ev = _event(
        "tool_call",
        1_780_557_354_690,
        metadata={"org_id": "攻击者的org", "_PROTECTED_org_id": "evil"},
    )
    resp = _post(client, cred, [ev])
    assert resp.status_code == 202, resp.text
    assert resp.json()["accepted"] == 1
    _login(client)
    items = client.get("/api/v1/events").json()["items"]
    assert items[0]["org_id"] == "corp-shanghai"
    assert items[0]["device_id"] == "dev-org"


def test_unknown_event_name_rejected_not_stored(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-unk")
    resp = _post(client, cred, [_event("tool_faliure", 1_780_557_354_700)])
    assert resp.status_code == 202, resp.text
    assert resp.json()["rejected"] == 1
    assert resp.json()["accepted"] == 0
    assert _db_count(client) == 0
    _login(client)
    rejects = client.get("/api/v1/events/rejects").json()
    assert rejects["total"] == 1
    assert rejects["items"][0]["event_name"] == "tool_faliure"


def test_mixed_batch_does_not_reject_whole(client):
    """2 好 1 坏 → 202 accepted=2 rejected=1，不整批退。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-mix")
    events = [
        _event("tool_call", 1_780_557_354_710),
        _event("not_a_real_event", 1_780_557_354_711),
        _event("tool_success", 1_780_557_354_712),
    ]
    resp = _post(client, cred, events)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["accepted"] == 2
    assert body["rejected"] == 1
    assert _db_count(client) == 2


def test_501_events_is_413(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-413")
    events = [_event("tool_call", 1_780_557_354_720 + i) for i in range(501)]
    resp = _post(client, cred, events)
    assert resp.status_code == 413, resp.text
    assert _db_count(client) == 0


def test_missing_session_id_still_ingested(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-nosid")
    ev = {
        "eventName": "tool_call",
        "timestamp": 1_780_557_354_730,
        "metadata": {"tool_name": "Bash"},
    }
    resp = _post(client, cred, [ev])
    assert resp.status_code == 202, resp.text
    assert resp.json()["accepted"] == 1
    _login(client)
    item = client.get("/api/v1/events").json()["items"][0]
    assert item["session_id"] is None


def test_nested_metadata_rejected(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-nest")
    ev = _event("tool_call", 1_780_557_354_740, metadata={"nested": {"a": 1}})
    resp = _post(client, cred, [ev])
    assert resp.status_code == 202, resp.text
    assert resp.json()["rejected"] == 1
    assert resp.json()["accepted"] == 0
    assert _db_count(client) == 0


def test_long_string_truncated_with_flag(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-trunc")
    ev = _event("tool_call", 1_780_557_354_750, metadata={"blob": "x" * 2000})
    resp = _post(client, cred, [ev])
    assert resp.status_code == 202, resp.text
    assert resp.json()["accepted"] == 1
    _login(client)
    meta = client.get("/api/v1/events").json()["items"][0]["metadata"]
    assert meta["_truncated"] is True
    assert len(meta["blob"]) == 1024


# ---------------------------------------------------------------------------
# 两个固定聚合
# ---------------------------------------------------------------------------
def _insert_trajectory(session_id: str, device_id: str = "dev-traj") -> None:
    """有意跨模块写表：测试要造「有轨迹零事件」的会话，不走轨迹上传通道。

    不能走 async engine 的 sync_engine：aiosqlite 的 sync 适配要 greenlet，
    TestClient 线程里会 MissingGreenlet。URL 是 sqlite+aiosqlite:///绝对路径，
    剥前缀后给标准库 sqlite3 直写。
    """
    import sqlite3

    from app.core.config import settings

    db_path = settings.DATABASE_URL.split("sqlite+aiosqlite:///", 1)[1]
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO trajectories (
                session_id, tool_source, model, traj_file_path,
                uploaded_at, updated_at, device_id,
                total_steps, total_api_calls, total_tokens, total_cost_usd,
                exit_status, first_prompt, quality_status, traj_file_size,
                has_thinking, has_sub_agent, task_type, project_name,
                tools_used, tags
            ) VALUES (
                ?, 'claude-code', 'test', '/tmp/x.traj', ?, ?, ?,
                0, 0, 0, 0.0,
                '', '', 'unreviewed', 0,
                0, 0, '', '',
                '[]', '[]'
            )
            """,
            (session_id, "2026-09-23T00:00:00+00:00", "2026-09-23T00:00:00+00:00", device_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_session_coverage_includes_trajectory_without_events(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-cov")
    sid_with_both = "20260922-200236-aaaaaaaa"
    sid_events_only = "20260922-200236-bbbbbbbb"
    sid_traj_only = "20260922-200236-cccccccc"

    _insert_trajectory(sid_with_both)
    _insert_trajectory(sid_traj_only)

    events = [
        _event("policy_enforced", 1_780_557_354_760, metadata={"_ctx_session_id": sid_with_both}),
        _event("tool_call", 1_780_557_354_761, metadata={"_ctx_session_id": sid_events_only}),
    ]
    assert _post(client, cred, events).json()["accepted"] == 2

    _login(client)
    cov = client.get("/api/v1/events/stats/session-coverage").json()
    assert cov["sessions_with_events"] == 2
    assert cov["with_trajectory"] == 1
    assert cov["without_trajectory"] == 1
    assert cov["trajectory_without_events"] == 1
    by_sid = {row["session_id"]: row for row in cov["items"]}
    assert sid_traj_only in by_sid
    assert by_sid[sid_traj_only]["has_trajectory"] is True
    assert by_sid[sid_traj_only]["event_count"] == 0
    assert by_sid[sid_with_both]["has_trajectory"] is True
    assert by_sid[sid_with_both]["policy_enforced"] == 1
    assert by_sid[sid_events_only]["has_trajectory"] is False


def test_policy_audit_splits_false_positives(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-audit")
    events = [
        _event(
            "policy_enforced",
            1_780_557_354_770,
            metadata={"outcome": "applied"},
        ),
        _event(
            "policy_enforced",
            1_780_557_354_771,
            metadata={"outcome": "none"},
        ),
        _event(
            "policy_enforced",
            1_780_557_354_772,
            metadata={"outcome": "error"},
        ),
        _event(
            "guardrail_triggered",
            1_780_557_354_773,
            metadata={"falsePositive": "confirmed_true_positive"},
        ),
        _event(
            "guardrail_triggered",
            1_780_557_354_774,
            metadata={"falsePositive": "suspected_false_positive"},
        ),
        _event(
            "guardrail_triggered",
            1_780_557_354_775,
            metadata={"falsePositive": "unknown"},
        ),
        _event("permission_deny", 1_780_557_354_776),
    ]
    assert _post(client, cred, events).json()["accepted"] == 7

    _login(client)
    audit = client.get("/api/v1/events/stats/policy-audit").json()
    assert len(audit["items"]) == 1
    row = audit["items"][0]
    assert row["device_id"] == "dev-audit"
    assert row["org_id"] == "corp-shanghai"
    assert row["policy_applied"] == 1
    assert row["policy_none_or_error"] == 2
    assert row["guardrail_total"] == 3
    assert row["guardrail_true_positive"] == 1
    assert row["guardrail_false_positive"] == 1
    assert row["guardrail_unknown"] == 1
    assert row["permission_deny"] == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

def test_list_events_daily_buckets_and_since(client):
    """管理台柱状图走 GET /events 的 daily 字段，按 received_at 日分桶。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-daily")
    events = [
        _event("tool_call", 1_780_557_354_780),
        _event("tool_success", 1_780_557_354_781),
    ]
    assert _post(client, cred, events).json()["accepted"] == 2
    _login(client)
    body = client.get("/api/v1/events").json()
    assert body["total"] == 2
    assert body["daily"]
    assert sum(b["count"] for b in body["daily"]) == 2
    assert all(len(b["date"]) == 10 for b in body["daily"])

    future = client.get("/api/v1/events", params={"since": "2099-01-01T00:00:00+00:00"}).json()
    assert future["total"] == 0
    assert future["daily"] == []


def test_list_trajectories_filters_by_device_id(client):
    """设备列表「轨迹」跳转依赖这个参数。不改响应形状，只加筛选。"""
    _insert_trajectory("20260922-200236-dddddddd", device_id="dev-keep")
    _insert_trajectory("20260922-200236-eeeeeeee", device_id="dev-other")
    _login(client)
    body = client.get("/api/v1/trajectories", params={"device_id": "dev-keep"}).json()
    ids = {row["session_id"] for row in body["items"]}
    assert "20260922-200236-dddddddd" in ids
    assert "20260922-200236-eeeeeeee" not in ids


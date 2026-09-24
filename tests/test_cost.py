"""cost 模块：上报 upsert / 预算下发求值 / 门禁 / 一个固定聚合。

规划 §4 M5 验收：
- POST /usage/ledger 无凭据 401（不是 200）；X-Upload-Token 当鉴权也 401
- 同一 (device, session) 投 30 次 → 库里仍 1 行，cost 是最后一次的值（不是 30 倍）
- org_id / device_id 从凭据取，body 里塞攻击者的 org 无效
- 缺 sessionId → 400（不是 422）；body 超上限 → 413
- GET /ctl/budget 无凭据 401；device > team > org 不合并；三层都没有 204
- enforcement 默认 alert；管理台可切 block；未知值 422
- reason 必填；同层同周期第二条 enabled → 409
- 上传 token 写不进 /budgets
- 账本无 DELETE（管理台只读）
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
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
    db_path = tmp_path / "cost.db"
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


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
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
        "ver": "0.1.605",
    }
    if team_id:
        body["team_id"] = team_id
    resp = client.post("/api/v1/ctl/enroll", json=body, headers={"X-Enroll-Token": code})
    assert resp.status_code == 201, resp.text
    return resp.json()["credential"]


# 周期键由服务端按**当前 UTC** 算（guard.current_period_key），所以夹具时间必须
# 落在当前周期内，不能写死一个年份 —— 写死会让 used_usd 永远是 0，且下个月才暴露。
# 取当月 15 号 12:00 UTC：离月初/月末都远，不会在跨月瞬间抖动。
_NOW = datetime.now(timezone.utc)
_THIS_MONTH_MID = _NOW.replace(day=15, hour=12, minute=0, second=0, microsecond=0)
TS = int(_THIS_MONTH_MID.timestamp())
# 两个月前，必然是另一个 monthly 周期键
TS_OTHER_MONTH = int((_THIS_MONTH_MID - timedelta(days=60)).timestamp())
THIS_MONTH_KEY = _THIS_MONTH_MID.strftime("%Y-%m")


def _entry(session_id: str = "20260923-181500-a1b2c3d4", ts: int = TS, **over) -> dict:
    """客户端 UsageLedgerEntry 今天落盘的那个形状（契约 §1）。"""
    body = {
        "ts": ts,
        "sessionId": session_id,
        "model": "deepseek-v4-pro",
        "provider": "openai",
        "promptTotal": 128000,
        "cacheHit": 96000,
        "cacheWrite": 0,
        "uncachedInput": 32000,
        "output": 2400,
        "costUSD": 0.1842,
        "savingsUSD": 0.0720,
        "durationMs": 185000,
        "sideInputTokens": 1200,
        "sideOutputTokens": 80,
        "sideCostUSD": 0.0031,
        "endpointHost": "gw.corp.internal",
        "appVersion": "0.1.605",
    }
    body.update(over)
    return body


def _post(client, cred: str, entry: dict):
    return client.post(
        "/api/v1/usage/ledger",
        json=entry,
        headers={"Authorization": f"Bearer {cred}"},
    )


def _ledger_rows(client, **params) -> list[dict]:
    _login(client)
    resp = client.get("/api/v1/usage/ledger", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


def _create_budget(
    client,
    scope_type: str = "org",
    scope_id: str = "corp-shanghai",
    org_id: str = "corp-shanghai",
    period: str = "monthly",
    limit_usd: float = 100.0,
    enforcement: str | None = None,
    reason: str = "季度预算",
):
    _login(client)
    body = {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "org_id": org_id,
        "period": period,
        "limit_usd": limit_usd,
        "reason": reason,
    }
    if enforcement is not None:
        body["enforcement"] = enforcement
    return client.post("/api/v1/budgets", json=body)


def _get_budget(client, cred: str, headers: dict | None = None):
    h = {"Authorization": f"Bearer {cred}"}
    if headers:
        h.update(headers)
    return client.get("/api/v1/ctl/budget", headers=h)


# ---------------------------------------------------------------------------
# 上报鉴权（fail-closed）
# ---------------------------------------------------------------------------
def test_ingest_without_credential_is_401(client):
    """无凭据必须 401，不是 200 —— 匿名 upsert 能把别人的成本改成 0。"""
    resp = client.post("/api/v1/usage/ledger", json=_entry())
    assert resp.status_code == 401, resp.text


def test_ingest_with_upload_token_is_401(client):
    """数据面 token 不得当控制面鉴权（边界测试 ① 的运行时对应）。"""
    from app.core.config import settings

    resp = client.post(
        "/api/v1/usage/ledger",
        json=_entry(),
        headers={"X-Upload-Token": settings.UPLOAD_TOKEN},
    )
    assert resp.status_code == 401, resp.text


def test_ingest_with_bad_bearer_is_401(client):
    """坏凭据必须 401（fail-closed），不是 200。"""
    resp = client.post(
        "/api/v1/usage/ledger",
        json=_entry(),
        headers={"Authorization": "Bearer not-a-real-credential"},
    )
    assert resp.status_code == 401, resp.text


def test_ingest_with_revoked_credential_is_401(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-revoke")
    assert _post(client, cred, _entry()).status_code == 200
    _login(client)
    assert client.post("/api/v1/identity/devices/dev-revoke/revoke").status_code == 200
    assert _post(client, cred, _entry("s-after-revoke")).status_code == 401


# ---------------------------------------------------------------------------
# upsert（本模块核心用例）
# ---------------------------------------------------------------------------
def test_first_report_is_inserted(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-ins")
    resp = _post(client, cred, _entry())
    assert resp.status_code == 200, resp.text
    assert resp.json()["upserted"] == "inserted"

    rows = _ledger_rows(client)
    assert len(rows) == 1
    row = rows[0]
    assert row["device_id"] == "dev-ins"
    assert row["org_id"] == "corp-shanghai"
    assert row["team_id"] == "infra"
    assert row["session_id"] == "20260923-181500-a1b2c3d4"
    assert row["cost_usd"] == pytest.approx(0.1842)
    assert row["side_cost_usd"] == pytest.approx(0.0031)


def test_thirty_reports_of_one_session_stay_one_row(client):
    """出口：一个会话上报 30 次，库里 1 行，cost 是最后一次的值（不是 30 倍）。

    这是规划点名要防的 append 退化。latest-wins = 整行覆盖。
    """
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-upsert")
    last_cost = 0.0
    for i in range(30):
        last_cost = round(0.01 * (i + 1), 4)
        resp = _post(
            client,
            cred,
            _entry(costUSD=last_cost, promptTotal=1000 * (i + 1), output=10 * (i + 1)),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["upserted"] == ("inserted" if i == 0 else "updated")

    rows = _ledger_rows(client)
    assert len(rows) == 1, f"30 次上报应仍是 1 行，实际 {len(rows)}"
    assert rows[0]["cost_usd"] == pytest.approx(last_cost)
    assert rows[0]["prompt_total"] == 30000
    assert rows[0]["output"] == 300


def test_upsert_is_full_row_overwrite_not_merge(client):
    """latest-wins 是整行覆盖：新一次不带 side_* 时旧值不得残留。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-overwrite")
    assert _post(client, cred, _entry()).status_code == 200

    lean = _entry()
    for key in ("sideInputTokens", "sideOutputTokens", "sideCostUSD"):
        lean.pop(key)
    assert _post(client, cred, lean).json()["upserted"] == "updated"

    row = _ledger_rows(client)[0]
    assert row["side_cost_usd"] is None, "整行覆盖，旧 side 值不得残留"
    assert row["side_input_tokens"] is None


def test_absent_side_fields_are_stored_as_null_not_zero(client):
    """客户端不落三个恒零字段。存 0 会让「无影子」与「影子恰好为 0」不可分。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-noside")
    lean = _entry("s-noside")
    for key in ("sideInputTokens", "sideOutputTokens", "sideCostUSD"):
        lean.pop(key)
    assert _post(client, cred, lean).status_code == 200

    row = _ledger_rows(client, device_id="dev-noside")[0]
    assert row["side_cost_usd"] is None
    assert row["side_input_tokens"] is None
    assert row["side_output_tokens"] is None


def test_same_session_different_device_are_two_rows(client):
    """upsert 键是 (device_id, session_id)。两台设备撞 sessionId 不得互相覆盖。"""
    code_a = _issue_code(client)
    cred_a = _enroll(client, code_a, "dev-a")
    code_b = _issue_code(client)
    cred_b = _enroll(client, code_b, "dev-b")
    assert _post(client, cred_a, _entry("same-sid", costUSD=1.0)).status_code == 200
    assert _post(client, cred_b, _entry("same-sid", costUSD=2.0)).status_code == 200

    rows = _ledger_rows(client)
    assert len(rows) == 2
    assert {r["device_id"] for r in rows} == {"dev-a", "dev-b"}


def test_body_identity_fields_are_ignored(client):
    """body 里塞 deviceId / orgId 不能改归属 —— 库里必须是凭据的那个。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-real")
    entry = _entry(deviceId="攻击者的设备", orgId="攻击者的org", teamId="evil")
    assert _post(client, cred, entry).status_code == 200

    row = _ledger_rows(client)[0]
    assert row["device_id"] == "dev-real"
    assert row["org_id"] == "corp-shanghai"
    assert row["team_id"] == "infra"


# ---------------------------------------------------------------------------
# 上报形状（fail-closed 400，不是 422）
# ---------------------------------------------------------------------------
def test_missing_session_id_is_400(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-shape")
    entry = _entry()
    entry.pop("sessionId")
    resp = _post(client, cred, entry)
    assert resp.status_code == 400, resp.text


def test_non_object_body_is_400(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-shape2")
    resp = client.post(
        "/api/v1/usage/ledger",
        json=[_entry()],
        headers={"Authorization": f"Bearer {cred}"},
    )
    assert resp.status_code == 400, resp.text


def test_cost_as_string_is_400(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-type")
    resp = _post(client, cred, _entry(costUSD="0.18"))
    assert resp.status_code == 400, resp.text


def test_oversized_body_is_413(client):
    from app.modules.cost.service.guard import MAX_BODY_BYTES

    code = _issue_code(client)
    cred = _enroll(client, code, "dev-big")
    resp = _post(client, cred, _entry(appVersion="x" * (MAX_BODY_BYTES + 100)))
    assert resp.status_code == 413, resp.text


def test_unknown_extra_field_is_accepted(client):
    """字段可少不可改名；客户端加新字段时服务端不能 400 整条（会永久缺会话）。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-extra")
    resp = _post(client, cred, _entry(brandNewFieldFromClient=123))
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# 预算下发（fail-open 通道，但必须鉴权）
# ---------------------------------------------------------------------------
def test_budget_serve_without_credential_is_401(client):
    resp = client.get("/api/v1/ctl/budget")
    assert resp.status_code == 401, resp.text


def test_no_budget_at_any_layer_is_204(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-none")
    resp = _get_budget(client, cred)
    assert resp.status_code == 204, resp.text
    assert resp.headers.get("x-budget-generation") == '"none"'


def test_org_budget_is_delivered_with_default_alert(client):
    """enforcement 默认 alert（告警放行）。规划默认值，不是部署配置。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-org")
    assert _create_budget(client).status_code == 201

    resp = _get_budget(client, cred)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "remote"
    assert body["scope_type"] == "org"
    assert body["limit_usd"] == pytest.approx(100.0)
    assert body["enforcement"] == "alert"
    assert body["used_usd"] == pytest.approx(0.0)
    assert "private" in resp.headers.get("cache-control", "")
    assert "public" not in resp.headers.get("cache-control", "")
    assert resp.headers.get("etag", "").startswith('"')


def test_device_budget_wins_over_team_and_org_without_merge(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-win")
    assert _create_budget(client, scope_type="org", limit_usd=500.0).status_code == 201
    assert _create_budget(
        client, scope_type="team", scope_id="infra", limit_usd=200.0
    ).status_code == 201
    assert _create_budget(
        client, scope_type="device", scope_id="dev-win", limit_usd=10.0, enforcement="block"
    ).status_code == 201

    body = _get_budget(client, cred).json()
    assert body["scope_type"] == "device"
    assert body["limit_usd"] == pytest.approx(10.0)
    assert body["enforcement"] == "block", "device 命中则不合并 team/org"


def test_team_budget_wins_over_org(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-team")
    assert _create_budget(client, scope_type="org", limit_usd=500.0).status_code == 201
    assert _create_budget(
        client, scope_type="team", scope_id="infra", limit_usd=200.0
    ).status_code == 201

    body = _get_budget(client, cred).json()
    assert body["scope_type"] == "team"
    assert body["limit_usd"] == pytest.approx(200.0)


def test_disabled_budget_is_not_delivered(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-disabled")
    created = _create_budget(client)
    assert created.status_code == 201
    bid = created.json()["id"]

    _login(client)
    resp = client.post(f"/api/v1/budgets/{bid}/disable", params={"reason": "季度结束"})
    assert resp.status_code == 200, resp.text
    assert _get_budget(client, cred).status_code == 204


def test_used_usd_counts_reported_ledger(client):
    """used_usd 从 usage_ledger 现算，不存表。不读 trajectories.total_cost_usd。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-used")
    assert _create_budget(client, period="monthly", limit_usd=100.0).status_code == 201

    assert _post(client, cred, _entry("s1", costUSD=1.5)).status_code == 200
    assert _post(client, cred, _entry("s2", costUSD=2.25)).status_code == 200

    body = _get_budget(client, cred).json()
    assert body["used_usd"] == pytest.approx(3.75)
    assert body["period_key"] == THIS_MONTH_KEY


def test_org_used_usd_sums_across_devices(client):
    """org 层 used_usd = 该 org 各设备 cost_usd 之和。跨会话预算只能服务端算。"""
    code_a = _issue_code(client)
    cred_a = _enroll(client, code_a, "dev-sum1")
    code_b = _issue_code(client)
    cred_b = _enroll(client, code_b, "dev-sum2")
    assert _create_budget(client, scope_type="org", period="monthly", limit_usd=100.0).status_code == 201

    assert _post(client, cred_a, _entry("s1", costUSD=1.0)).status_code == 200
    assert _post(client, cred_a, _entry("s2", costUSD=2.0)).status_code == 200
    assert _post(client, cred_b, _entry("s3", costUSD=4.5)).status_code == 200

    body = _get_budget(client, cred_a).json()
    assert body["scope_type"] == "org"
    assert body["used_usd"] == pytest.approx(7.5)


def test_used_usd_excludes_other_period(client):
    """上月的账本不进本月 used_usd。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-period")
    assert _create_budget(client, period="monthly").status_code == 201
    assert _post(client, cred, _entry("s-old", ts=TS_OTHER_MONTH, costUSD=9.0)).status_code == 200
    assert _post(client, cred, _entry("s-now", costUSD=1.0)).status_code == 200

    body = _get_budget(client, cred).json()
    assert body["used_usd"] == pytest.approx(1.0)


def test_etag_round_trip_is_304(client):
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-etag")
    assert _create_budget(client).status_code == 201
    first = _get_budget(client, cred)
    etag = first.headers["etag"]
    again = _get_budget(client, cred, headers={"If-None-Match": etag})
    assert again.status_code == 304


def test_etag_changes_when_used_usd_changes(client):
    """ETag 必须纳入 used_usd —— 否则客户端 304 永远看不到「该告警了」。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-etag2")
    assert _create_budget(client).status_code == 201
    before = _get_budget(client, cred).headers["etag"]
    assert _post(client, cred, _entry("s-new", costUSD=5.0)).status_code == 200
    after = _get_budget(client, cred).headers["etag"]
    assert before != after


def test_budget_serve_is_read_only(client):
    """给下发端点加写方法 = 谁有设备凭据谁能改全公司预算。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-ro")
    for method in ("post", "put", "patch", "delete"):
        resp = client.request(
            method.upper(),
            "/api/v1/ctl/budget",
            headers={"Authorization": f"Bearer {cred}"},
        )
        assert resp.status_code == 405, f"{method.upper()} /ctl/budget → {resp.status_code}"


# ---------------------------------------------------------------------------
# 管理台门禁（fail-closed）
# ---------------------------------------------------------------------------
def test_admin_endpoints_require_web_session(client):
    body = {
        "scope_type": "org",
        "scope_id": "corp-shanghai",
        "org_id": "corp-shanghai",
        "period": "monthly",
        "limit_usd": 10,
        "reason": "x",
    }
    for method, path in [
        ("get", "/api/v1/usage/ledger"),
        ("get", "/api/v1/usage/ledger/stats/by-scope"),
        ("get", "/api/v1/budgets"),
        ("post", "/api/v1/budgets"),
        ("patch", "/api/v1/budgets/1"),
        ("delete", "/api/v1/budgets/1"),
        ("get", "/api/v1/budgets/audit"),
    ]:
        resp = client.request(
            method.upper(),
            path,
            json=body if method in {"post", "patch"} else None,
            params={"reason": "x"} if method == "delete" else None,
        )
        assert resp.status_code == 401, f"{method.upper()} {path} → {resp.status_code}"


def test_upload_token_cannot_write_budgets(client):
    from app.core.config import settings

    resp = client.post(
        "/api/v1/budgets",
        json={
            "scope_type": "org",
            "scope_id": "corp-shanghai",
            "org_id": "corp-shanghai",
            "period": "monthly",
            "limit_usd": 10,
            "reason": "x",
        },
        headers={"X-Upload-Token": settings.UPLOAD_TOKEN},
    )
    assert resp.status_code == 401, resp.text


def test_missing_reason_is_422(client):
    _issue_code(client)
    resp = _create_budget(client, reason="")
    assert resp.status_code == 422, resp.text


def test_unknown_enforcement_is_422(client):
    """没有 downgrade 档：T1 没接到 loop，配了也不会生效。"""
    _issue_code(client)
    resp = _create_budget(client, enforcement="downgrade")
    assert resp.status_code == 422, resp.text


def test_unknown_period_is_422(client):
    _issue_code(client)
    resp = _create_budget(client, period="quarterly")
    assert resp.status_code == 422, resp.text


def test_negative_limit_is_422(client):
    _issue_code(client)
    resp = _create_budget(client, limit_usd=-1)
    assert resp.status_code == 422, resp.text


def test_unknown_field_is_422(client):
    _issue_code(client)
    _login(client)
    resp = client.post(
        "/api/v1/budgets",
        json={
            "scope_type": "org",
            "scope_id": "corp-shanghai",
            "org_id": "corp-shanghai",
            "period": "monthly",
            "limit_usd": 10,
            "reason": "x",
            "rpm": 60,
        },
    )
    assert resp.status_code == 422, resp.text


def test_unknown_scope_is_404(client):
    _issue_code(client)
    assert _create_budget(client, scope_type="org", scope_id="no-such", org_id="no-such").status_code == 404
    assert _create_budget(client, scope_type="team", scope_id="no-such-team").status_code == 404
    assert _create_budget(client, scope_type="device", scope_id="no-such-dev").status_code == 404


def test_second_enabled_budget_same_scope_period_is_409(client):
    _issue_code(client)
    assert _create_budget(client).status_code == 201
    second = _create_budget(client, limit_usd=200.0)
    assert second.status_code == 409, second.text


def test_same_scope_different_period_is_allowed(client):
    """唯一约束带 period：monthly + daily 可以并存。下发取更紧的那档。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-two")
    assert _create_budget(client, period="monthly", limit_usd=100.0).status_code == 201
    assert _create_budget(client, period="daily", limit_usd=5.0).status_code == 201

    body = _get_budget(client, cred).json()
    assert body["period"] == "daily"
    assert body["limit_usd"] == pytest.approx(5.0)


def test_enforcement_can_be_switched_to_block(client):
    """管理台可切硬拦。默认 alert，切 block 要有审计。"""
    code = _issue_code(client)
    cred = _enroll(client, code, "dev-block")
    created = _create_budget(client)
    assert created.status_code == 201
    bid = created.json()["id"]

    _login(client)
    resp = client.patch(
        f"/api/v1/budgets/{bid}",
        json={"enforcement": "block", "reason": "连续两周超支"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["enforcement"] == "block"
    assert _get_budget(client, cred).json()["enforcement"] == "block"

    audit = client.get("/api/v1/budgets/audit").json()["items"]
    actions = [a["action"] for a in audit]
    assert "update" in actions
    changed = next(a for a in audit if a["action"] == "update")
    assert changed["old"]["enforcement"] == "alert"
    assert changed["new"]["enforcement"] == "block"
    assert changed["reason"] == "连续两周超支"


def test_audit_survives_delete(client):
    _issue_code(client)
    created = _create_budget(client)
    bid = created.json()["id"]
    _login(client)
    assert client.delete(f"/api/v1/budgets/{bid}", params={"reason": "不再管控"}).status_code == 200

    audit = client.get("/api/v1/budgets/audit").json()["items"]
    deleted = [a for a in audit if a["action"] == "delete"]
    assert deleted, "删除后审计必须留下一条"
    assert deleted[0]["scope_type"] == "org"
    assert deleted[0]["scope_id"] == "corp-shanghai"


def test_evaluate_preview_reports_hit_layer(client):
    code = _issue_code(client)
    _enroll(client, code, "dev-eval")
    assert _create_budget(client, scope_type="org", limit_usd=500.0).status_code == 201
    assert _create_budget(
        client, scope_type="team", scope_id="infra", limit_usd=50.0
    ).status_code == 201

    _login(client)
    resp = client.get("/api/v1/budgets/evaluate", params={"device_id": "dev-eval"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["layer"] == "team"
    assert body["limit_usd"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# 聚合（一个固定视图）
# ---------------------------------------------------------------------------
def test_by_scope_groups_per_device(client):
    code_a = _issue_code(client)
    cred_a = _enroll(client, code_a, "dev-x")
    code_b = _issue_code(client)
    cred_b = _enroll(client, code_b, "dev-y")
    assert _post(client, cred_a, _entry("s1", costUSD=1.0)).status_code == 200
    assert _post(client, cred_a, _entry("s2", costUSD=2.0)).status_code == 200
    assert _post(client, cred_b, _entry("s3", costUSD=0.5)).status_code == 200

    _login(client)
    resp = client.get("/api/v1/usage/ledger/stats/by-scope", params={"period": "monthly"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["period_key"] == THIS_MONTH_KEY
    by_device = {i["device_id"]: i for i in body["items"]}
    assert by_device["dev-x"]["sessions"] == 2
    assert by_device["dev-x"]["cost_usd"] == pytest.approx(3.0)
    assert by_device["dev-y"]["cost_usd"] == pytest.approx(0.5)
    # 口径禁令：响应不得含任何派生单价列
    for item in body["items"]:
        assert not {k for k in item if "per" in k.lower() or "price" in k.lower()}


def test_by_scope_bad_period_key_is_400(client):
    _login(client)
    resp = client.get(
        "/api/v1/usage/ledger/stats/by-scope",
        params={"period": "monthly", "period_key": "not-a-month"},
    )
    assert resp.status_code == 400, resp.text


def test_ledger_list_filters_by_device(client):
    code_a = _issue_code(client)
    cred_a = _enroll(client, code_a, "dev-f1")
    code_b = _issue_code(client)
    cred_b = _enroll(client, code_b, "dev-f2")
    assert _post(client, cred_a, _entry("s1")).status_code == 200
    assert _post(client, cred_b, _entry("s2")).status_code == 200

    rows = _ledger_rows(client, device_id="dev-f1")
    assert len(rows) == 1
    assert rows[0]["device_id"] == "dev-f1"

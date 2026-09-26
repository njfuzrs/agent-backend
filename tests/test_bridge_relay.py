"""sidecar 中继：拒 query、首帧、配对、抢接、强制断开。

不走主应用的 TestClient。主应用 --workers 2，装不下配对；中继是另一个
FastAPI 实例，测试直接起它。配对的真相在进程内存里，所以每个用例都换一份
干净的 Relay，不能串。

契约里「正确 token 放 query 仍 4001」是防旧客户端静默退回的那一条，
必须在这里而不是 HTTP 测试里。
"""

from __future__ import annotations

import asyncio
import json
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
def ctx(tmp_path, monkeypatch):
    """独立库 + 一份干净的中继内存。返回 (http, relay)。"""
    db_path = tmp_path / "relay.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"

    from sqlalchemy import event  # noqa: I001 — 函数内导入保持「先第三方，后本地」，不按 isort 把本地提前
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.core import db as db_mod
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", db_url)
    monkeypatch.setattr(settings.control_plane, "CTL_ENROLL_ENABLED", True)

    engine = create_async_engine(db_url, echo=False)

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

    from app.modules.bridge.sidecar import main as sidecar_main
    from app.modules.bridge.sidecar import relay as relay_mod

    fresh = relay_mod.reset_relay()
    monkeypatch.setattr(sidecar_main, "relay", fresh)
    monkeypatch.setattr(relay_mod, "relay", fresh)

    from fastapi.testclient import TestClient  # noqa: I001 — 同上，先第三方后本地

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as http, TestClient(
        sidecar_main.app, raise_server_exceptions=False
    ) as ws_client:
        yield http, ws_client, fresh

    engine.sync_engine.dispose()


def _login(client) -> None:
    from app.core.config import settings

    resp = client.post(
        "/api/v1/auth/login",
        json={"username": settings.AUTH_USERNAME, "password": settings.AUTH_PASSWORD},
    )
    assert resp.status_code == 200, resp.text


def _credential(client, device_id: str = "dev-1") -> str:
    _login(client)
    code = client.post(
        "/api/v1/identity/enroll-codes",
        json={"org_id": "corp-shanghai", "org_name": "corp-shanghai", "team_id": "infra"},
    )
    assert code.status_code == 201, code.text
    resp = client.post(
        "/api/v1/ctl/enroll",
        json={
            "device_id": device_id,
            "user_id": "zhangsan@corp.com",
            "org_id": "corp-shanghai",
            "team_id": "infra",
            "platform": "darwin",
            "ver": "0.1.604",
        },
        headers={"X-Enroll-Token": code.json()["code"]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["credential"]


def _session(client, cred: str) -> dict:
    resp = client.post(
        "/api/v1/ctl/bridge/sessions",
        json={"ver": "0.1.604"},
        headers={"Authorization": f"Bearer {cred}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _controller(client, session_id: str) -> str:
    client.cookies.clear()
    _login(client)
    resp = client.post(f"/api/v1/bridge/sessions/{session_id}/controller-token")
    assert resp.status_code == 201, resp.text
    return resp.json()["session_token"]


def _auth(token: str, role: str) -> str:
    return json.dumps({"type": "auth", "token": token, "role": role})


def _close_frame(ws) -> tuple[int, str]:
    """等对端关闭。starlette 把关闭帧变成异常，code 与 reason 在上面。"""
    with pytest.raises(Exception) as caught:
        ws.receive_text()
    exc = caught.value
    code = getattr(exc, "code", None)
    reason = getattr(exc, "reason", None)
    if not isinstance(code, int):
        args = getattr(exc, "args", ())
        if args and isinstance(args[0], int):
            code = args[0]
            if len(args) > 1 and isinstance(args[1], str):
                reason = args[1]
    if not isinstance(code, int):
        raise AssertionError(f"连接断了但没有 close code: {exc!r}")  # noqa: TRY004 — 断言失败就是 AssertionError，不是类型错误
    return code, reason or ""


def _close_code(ws) -> int:
    return _close_frame(ws)[0]


def _session_row(portal, session_id: str):
    """库里的会话行。空闲超时不许改 state、不许写 disconnect_reason。

    必须借中继所在的事件循环读。``asyncio.run`` 另开一个循环，aiosqlite 的
    连接在那边用不了；再开一条同步连接则会让文件库上的异步连接失效。
    """
    from sqlalchemy import select  # noqa: I001 — 同上，先第三方后本地

    from app.core import db as db_mod
    from app.modules.bridge.model import BridgeAudit, BridgeSession

    async def _read():
        async with db_mod.async_session() as db:
            row = await db.execute(select(BridgeSession).where(BridgeSession.id == session_id))
            session = row.scalar_one()
            audits = await db.execute(
                select(BridgeAudit.action).where(BridgeAudit.session_id == session_id)
            )
            # 只带出普通值。ORM 对象出了这段 session 就是游离的。
            return (session.state, session.disconnect_reason), [a[0] for a in audits]

    return portal.call(_read)


# ---------------------------------------------------------------------------
# 握手
# ---------------------------------------------------------------------------
def test_token_in_query_is_rejected_even_when_correct(ctx):
    http, ws_client, _relay = ctx
    issued = _session(http, _credential(http))
    with ws_client.websocket_connect(
        f"/api/v1/bridge/ws?token={issued['session_token']}"
    ) as ws:
        assert _close_code(ws) == 4001


def test_bad_token_is_4001(ctx):
    http, ws_client, _relay = ctx
    _credential(http)
    with ws_client.websocket_connect("/api/v1/bridge/ws") as ws:
        ws.send_text(_auth("this-is-not-a-token", "cli"))
        assert _close_code(ws) == 4001


def test_non_auth_first_frame_is_4001(ctx):
    _http, ws_client, _relay = ctx
    with ws_client.websocket_connect("/api/v1/bridge/ws") as ws:
        ws.send_text(json.dumps({"type": "user_message", "data": "hi"}))
        assert _close_code(ws) == 4001


def test_auth_timeout_is_4001(ctx, monkeypatch):
    """首帧超时与坏 token 同一个码，不给「还差一步」的信号。"""
    from app.modules.bridge.sidecar import relay as relay_mod

    monkeypatch.setattr(relay_mod, "AUTH_FRAME_TIMEOUT_SECONDS", 0.2)
    _http, ws_client, _relay = ctx
    with ws_client.websocket_connect("/api/v1/bridge/ws") as ws:
        assert _close_code(ws) == 4001


def test_role_mismatch_is_4001(ctx):
    """CLI 的 token 拿去当 controller 用，hash 对上也拒绝。"""
    http, ws_client, _relay = ctx
    issued = _session(http, _credential(http))
    with ws_client.websocket_connect("/api/v1/bridge/ws") as ws:
        ws.send_text(_auth(issued["session_token"], "controller"))
        assert _close_code(ws) == 4001


def test_valid_auth_gets_session_id(ctx):
    http, ws_client, _relay = ctx
    issued = _session(http, _credential(http))
    with ws_client.websocket_connect("/api/v1/bridge/ws") as ws:
        ws.send_text(_auth(issued["session_token"], "cli"))
        ok = json.loads(ws.receive_text())
        assert ok == {"type": "auth_ok", "session_id": issued["session_id"]}


# ---------------------------------------------------------------------------
# 配对与转发
# ---------------------------------------------------------------------------
def test_paired_sides_receive_each_others_text(ctx):
    http, ws_client, _relay = ctx
    issued = _session(http, _credential(http))
    controller = _controller(http, issued["session_id"])
    with ws_client.websocket_connect("/api/v1/bridge/ws") as cli, ws_client.websocket_connect(
        "/api/v1/bridge/ws"
    ) as ctrl:
        cli.send_text(_auth(issued["session_token"], "cli"))
        ctrl.send_text(_auth(controller, "controller"))
        assert json.loads(cli.receive_text())["type"] == "auth_ok"
        assert json.loads(ctrl.receive_text())["type"] == "auth_ok"

        cli.send_text(json.dumps({"type": "text", "data": "hello"}))
        assert json.loads(ctrl.receive_text()) == {"type": "text", "data": "hello"}

        # 心跳必须穿过去。控制端可能只看不发，靠它保活。
        # 同时回给发送方自己：CLI 的探活看的是这一帧有没有回来。
        cli.send_text(json.dumps({"type": "status", "data": {"ping": True}}))
        assert json.loads(ctrl.receive_text())["data"]["ping"] is True
        assert json.loads(cli.receive_text())["data"]["ping"] is True

        ctrl.send_text(json.dumps({"type": "user_message", "data": "跑测试"}))
        assert json.loads(cli.receive_text())["data"] == "跑测试"


def test_unknown_type_is_dropped_and_connection_stays(ctx):
    http, ws_client, relay = ctx
    issued = _session(http, _credential(http))
    controller = _controller(http, issued["session_id"])
    with ws_client.websocket_connect("/api/v1/bridge/ws") as cli, ws_client.websocket_connect(
        "/api/v1/bridge/ws"
    ) as ctrl:
        cli.send_text(_auth(issued["session_token"], "cli"))
        ctrl.send_text(_auth(controller, "controller"))
        cli.receive_text()
        ctrl.receive_text()

        before = relay.dropped_frames
        cli.send_text(json.dumps({"type": "shell", "data": "rm -rf /"}))
        cli.send_text(json.dumps({"type": "text", "data": "still here"}))
        assert json.loads(ctrl.receive_text())["data"] == "still here"
        assert relay.dropped_frames > before


def test_oversized_frame_is_dropped_not_disconnected(ctx, monkeypatch):
    from app.modules.bridge.sidecar import relay as relay_mod

    # 握手响应大约 70 字节，上限要放得过它，只截业务帧。
    monkeypatch.setattr(relay_mod, "MAX_FRAME_BYTES", 256)
    http, ws_client, _relay = ctx
    issued = _session(http, _credential(http))
    controller = _controller(http, issued["session_id"])
    with ws_client.websocket_connect("/api/v1/bridge/ws") as cli, ws_client.websocket_connect(
        "/api/v1/bridge/ws"
    ) as ctrl:
        cli.send_text(_auth(issued["session_token"], "cli"))
        ctrl.send_text(_auth(controller, "controller"))
        cli.receive_text()
        ctrl.receive_text()

        cli.send_text(json.dumps({"type": "text", "data": "x" * 500}))
        cli.send_text(json.dumps({"type": "text", "data": "ok"}))
        assert json.loads(ctrl.receive_text())["data"] == "ok"


def test_second_cli_replaces_the_first(ctx):
    http, ws_client, _relay = ctx
    issued = _session(http, _credential(http))
    with ws_client.websocket_connect("/api/v1/bridge/ws") as first:
        first.send_text(_auth(issued["session_token"], "cli"))
        assert json.loads(first.receive_text())["type"] == "auth_ok"
        with ws_client.websocket_connect("/api/v1/bridge/ws") as second:
            second.send_text(_auth(issued["session_token"], "cli"))
            assert json.loads(second.receive_text())["type"] == "auth_ok"
            assert _close_code(first) == 4003


def test_peer_disconnect_closes_the_other_side(ctx):
    http, ws_client, _relay = ctx
    issued = _session(http, _credential(http))
    controller = _controller(http, issued["session_id"])
    with ws_client.websocket_connect("/api/v1/bridge/ws") as cli:
        cli.send_text(_auth(issued["session_token"], "cli"))
        assert json.loads(cli.receive_text())["type"] == "auth_ok"
        with ws_client.websocket_connect("/api/v1/bridge/ws") as ctrl:
            ctrl.send_text(_auth(controller, "controller"))
            assert json.loads(ctrl.receive_text())["type"] == "auth_ok"
        # 控制端的 with 结束即断开，CLI 这一侧应随之关闭。
        assert _close_code(cli) == 1001


def test_admin_disconnect_closes_both_sides(ctx):
    """管理台只改库。sidecar 轮询到 disconnected 后关两侧，close code 1008。"""
    http, ws_client, relay = ctx
    issued = _session(http, _credential(http))
    controller = _controller(http, issued["session_id"])
    with ws_client.websocket_connect("/api/v1/bridge/ws") as cli, ws_client.websocket_connect(
        "/api/v1/bridge/ws"
    ) as ctrl:
        cli.send_text(_auth(issued["session_token"], "cli"))
        ctrl.send_text(_auth(controller, "controller"))
        cli.receive_text()
        ctrl.receive_text()

        http.cookies.clear()
        _login(http)
        resp = http.post(
            f"/api/v1/bridge/sessions/{issued['session_id']}/disconnect",
            json={"reason": "测试强制断开"},
        )
        assert resp.status_code == 204, resp.text

        asyncio.run(relay.poll_disconnects())
        assert _close_frame(cli) == (1008, "admin_disconnect")
        assert _close_frame(ctrl) == (1008, "admin_disconnect")

        (state, reason), actions = _session_row(cli.portal, issued["session_id"])
        assert state == "disconnected"
        assert reason == "测试强制断开"
        assert "disconnect" in actions


def test_binary_frame_closes_the_connection(ctx):
    http, ws_client, _relay = ctx
    issued = _session(http, _credential(http))
    with ws_client.websocket_connect("/api/v1/bridge/ws") as ws:
        ws.send_text(_auth(issued["session_token"], "cli"))
        assert json.loads(ws.receive_text())["type"] == "auth_ok"
        ws.send_bytes(b"\x00\x01")
        assert _close_code(ws) == 4001


def test_revoked_device_credential_rejects_existing_session(ctx):
    """吊销设备凭据必须让已签发、未过期的 session 立刻失效。

    吊销查的是 devices 的字符串标识，不是 device_credentials 的整数外键。
    查错列的话这条会绿着坏掉：凭据明明吊销了，握手仍然通过。
    """
    http, ws_client, _relay = ctx
    cred = _credential(http, "dev-revoke")
    issued = _session(http, cred)

    http.cookies.clear()
    _login(http)
    revoked = http.post("/api/v1/identity/devices/dev-revoke/revoke")
    assert revoked.status_code == 200, revoked.text

    with ws_client.websocket_connect("/api/v1/bridge/ws") as ws:
        ws.send_text(_auth(issued["session_token"], "cli"))
        assert _close_code(ws) == 4001




def test_idle_verdict_uses_the_newer_side():
    """一侧静默、另一侧有心跳，不判空闲。两侧都旧才判。

    修复前按本侧 last_inbound 计：控制端只读不发，60 秒后被当成
    管理员强制断开。人在权限弹窗前犹豫超过一分钟就是这个形态。
    """
    from app.modules.bridge.sidecar.relay import _idle_verdict

    # 控制端的帧停在 90 秒前，CLI 的心跳停在 30 秒前。上限 60。
    assert _idle_verdict({"cli": 70, "controller": 10}, now=100, limit=60) is None
    # 只有 CLI 一侧有记录（控制端连上后再没发过），一样不算静默。
    assert _idle_verdict({"cli": 70}, now=100, limit=60) is None
    # 两侧都超过上限，才关。
    assert _idle_verdict({"cli": 30, "controller": 10}, now=100, limit=60) == "idle"
    # 刚连上、还没有帧，不关。
    assert _idle_verdict({}, now=100, limit=60) is None


def test_one_silent_side_stays_paired(ctx):
    """控制端一帧不发，CLI 的心跳穿过中继，连接还在、会话仍是 paired。

    判定本身见 test_idle_verdict_uses_the_newer_side。这里钉的是另一半：
    这种不对称没有被记成断开，下一帧也穿得过去。
    """
    http, ws_client, _relay = ctx
    issued = _session(http, _credential(http))
    controller = _controller(http, issued["session_id"])
    with ws_client.websocket_connect("/api/v1/bridge/ws") as cli, ws_client.websocket_connect(
        "/api/v1/bridge/ws"
    ) as ctrl:
        cli.send_text(_auth(issued["session_token"], "cli"))
        ctrl.send_text(_auth(controller, "controller"))
        cli.receive_text()
        ctrl.receive_text()

        # 控制端至此再无发送。只有 CLI 的心跳。
        cli.send_text(json.dumps({"type": "status", "data": {"ping": True}}))
        assert json.loads(ctrl.receive_text())["data"]["ping"] is True
        # 回显先到。读掉它，下一帧才是业务帧。
        assert json.loads(cli.receive_text())["data"]["ping"] is True

        ctrl.send_text(json.dumps({"type": "user_message", "data": "回复 pong"}))
        assert json.loads(cli.receive_text())["data"] == "回复 pong"
        (state, reason), actions = _session_row(cli.portal, issued["session_id"])
        assert state == "paired"
        assert reason is None
        assert "disconnect" not in actions


def test_idle_close_is_1001_and_keeps_the_session(ctx):
    """空闲关掉的是 1001 idle_timeout，且不把会话标成 disconnected。

    标了会让旧 token 永久作废，重连即 4001——那正是空闲不该有的后果。
    也不写 disconnect_reason：空闲不是审计理由。

    不靠真等 60 秒。把两侧的最近一帧拨到上限之外，在中继自己的循环上
    跑泵用的那个判定，再走它 verdict == "idle" 时的两句关闭。
    """
    from app.modules.bridge.sidecar import relay as relay_mod

    http, ws_client, _relay = ctx
    issued = _session(http, _credential(http))
    controller = _controller(http, issued["session_id"])
    with ws_client.websocket_connect("/api/v1/bridge/ws") as cli, ws_client.websocket_connect(
        "/api/v1/bridge/ws"
    ) as ctrl:
        cli.send_text(_auth(issued["session_token"], "cli"))
        ctrl.send_text(_auth(controller, "controller"))
        cli.receive_text()
        ctrl.receive_text()

        # 泵阻塞在这一轮的 recv 上，轮询间隔是 2 秒，醒来才看空闲。
        # 把上限拨到 0 而不是改间隔：间隔是模块级常量，改了会漏到别的用例。
        # 判定用的是同一个名字，所以这一侧醒来时看到的上限就是 0。
        relay_mod.IDLE_TIMEOUT_SECONDS = 0
        assert _close_frame(cli) == (1001, "idle_timeout")

        # 对端不跟着关，会话也不改。with 退出才会走 _leave，所以在退出前查。
        ctrl.send_text(json.dumps({"type": "user_message", "data": "还在"}))
        (state, reason), actions = _session_row(ctrl.portal, issued["session_id"])
        assert state == "paired"
        assert reason is None
        assert "disconnect" not in actions

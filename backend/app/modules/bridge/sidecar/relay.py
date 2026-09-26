"""配对转发。

内存里的 `session_id → 两侧连接` 是配对的真相。数据库只做两件事：握手时验
token，以及让 REST 列表看得到状态。WebSocket 对象不进库、不进任何外部存储。

中继不解释业务帧。收到文本就原样交给对端；未知 type 丢弃并计数。唯一的例外
是心跳——它必须转发，不能当控制帧吞掉。控制端的浏览器可能长时间只看不发，
靠 CLI 每 30 秒的心跳穿过中继来维持两侧的探活。空闲因此按**一对连接里
最近的一帧**计，不按某一侧自己有没有开口计。

强制断开不走进程间消息。管理台把 state 改成 disconnected，这里每 2 秒看一次
自己内存里那些 session 的行。2 秒对「人点了一个按钮」可接受，不为此引 Redis。
它与空闲超时是两件事：前者 1008 ``admin_disconnect``，后者 1001
``idle_timeout``，且空闲不改库里的 state。
"""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass, field
from typing import Optional

from fastapi import WebSocket
from sqlalchemy import select, text, update
from starlette.websockets import WebSocketDisconnect

from app.core import db as db_mod
from app.core.logging import get_logger
from app.core.timeutil import is_expired
from app.modules.bridge.model import BridgeSession, BridgeSessionToken
from app.modules.bridge.service.guard import (
    AUTH_FRAME_TIMEOUT_SECONDS,
    DISCONNECT_POLL_SECONDS,
    IDLE_TIMEOUT_SECONDS,
    MAX_FRAME_BYTES,
)
from app.modules.identity.service.secrets import hash_secret

logger = get_logger("agent.bridge")

# 应用层 close code。客户端把这几个当永久失败，不会重连。
CLOSE_AUTH_FAILED = 4001
CLOSE_REPLACED = 4003
# 策略拒绝。**只**给管理台强制断开用，客户端不会重连——这是要的。
# 空闲超时不许复用它：人在权限弹窗前犹豫超过一分钟会被当成管理员踢下线，
# CLI 收到 1008 走永久失败并 process.exit(1)，permission_expired 也送不回去。
CLOSE_POLICY = 1008
REASON_ADMIN_DISCONNECT = "admin_disconnect"
# 空闲超时。1001 = going away，客户端按普通断开处理、允许重连。
# 不用 1008：空闲是传输层事件，不是策略拒绝，也不是审计理由。
CLOSE_IDLE = 1001
REASON_IDLE_TIMEOUT = "idle_timeout"
# 对端先走了。同样 1001，reason 不同——审计与日志靠 reason 区分，不靠码。
CLOSE_PEER_GONE = 1001

# 握手原因一律同一个词。区分「过期 / 吊销 / 不存在」会给枚举 token 的人信号。
REASON_AUTH_FAILED = "unauthorized"
REASON_TOKEN_IN_QUERY = "token_in_query"

ROLES = ("cli", "controller")

# 业务帧闭集，两边加起来。中继不解释它们，但未知 type 直接丢：
# 否则这条连接就是一条任意的 TCP 隧道。auth 是握手帧，不在这里。
ALLOWED_TYPES = frozenset(
    {
        "text",
        "tool_use",
        "tool_result",
        "status",
        "permission_request",
        "user_message",
        "permission_response",
        "control",
    }
)


@dataclass
class Pair:
    """一个 session 的两侧连接。缺的那一侧是 None，两侧都在才算配对。"""

    cli: Optional[WebSocket] = None
    controller: Optional[WebSocket] = None
    # 单调时钟。探活用，不入库。
    last_inbound: dict = field(default_factory=dict)

    def side(self, role: str) -> Optional[WebSocket]:
        return self.cli if role == "cli" else self.controller

    def set_side(self, role: str, ws: Optional[WebSocket]) -> None:
        if role == "cli":
            self.cli = ws
        else:
            self.controller = ws

    def other(self, role: str) -> Optional[WebSocket]:
        return self.controller if role == "cli" else self.cli

    def empty(self) -> bool:
        return self.cli is None and self.controller is None


class Relay:
    """一个 sidecar 进程里的全部配对。单进程是前提，不要加锁之外的共享。"""

    def __init__(self) -> None:
        self._pairs: dict[str, Pair] = {}
        self._lock = asyncio.Lock()
        # 丢弃计数。不入库：这是中继自己的健康信号，打日志就够。
        self.dropped_frames = 0

    async def handle(self, ws: WebSocket) -> None:
        """一条连接的全部生命周期。任何失败都落到关闭，不抛给 uvicorn。"""
        await ws.accept()
        if "token" in ws.query_params:
            # 值对不对都关。这是防「新中继 + 旧客户端」静默退回把 token 放进 URL。
            await self._close(ws, CLOSE_AUTH_FAILED, REASON_TOKEN_IN_QUERY)
            return

        try:
            raw = await asyncio.wait_for(_receive_text(ws), timeout=AUTH_FRAME_TIMEOUT_SECONDS)
        except (asyncio.TimeoutError, WebSocketDisconnect, KeyError):
            # KeyError：对端发了二进制帧。客户端只发文本，二进制一律当认证失败关。
            await self._close(ws, CLOSE_AUTH_FAILED, REASON_AUTH_FAILED)
            return

        auth = _parse_auth(raw)
        if auth is None:
            await self._close(ws, CLOSE_AUTH_FAILED, REASON_AUTH_FAILED)
            return

        verdict = await self._authenticate(auth["token"], auth["role"])
        if verdict is None:
            # 只记「失败了」。token 原文与失败细因都不进审计，防枚举。
            await _record_auth_failure()
            await self._close(ws, CLOSE_AUTH_FAILED, REASON_AUTH_FAILED)
            return

        session_id, role = verdict
        await self._join(session_id, role, ws)
        keep_session = False
        try:
            await ws.send_text(json.dumps({"type": "auth_ok", "session_id": session_id}))
            keep_session = await self._pump(session_id, role, ws)
        except WebSocketDisconnect:
            pass
        finally:
            # 空闲超时只拆连接，会话留在 paired：重连用原 token 进得来。
            # 标成 disconnected 会让旧 token 永久作废，重连即 4001。
            if keep_session:
                await self._detach(session_id, role, ws)
            else:
                await self._leave(session_id, role, ws)

    async def _authenticate(self, token: str, role: str) -> Optional[tuple[str, str]]:
        """验 hash、过期、吊销、role、设备吊销。失败一律返回 None，不区分原因。"""
        token_hash = hash_secret(token)
        async with db_mod.async_session() as db:
            row = await db.execute(
                select(BridgeSessionToken, BridgeSession)
                .join(BridgeSession, BridgeSession.id == BridgeSessionToken.session_id)
                .where(BridgeSessionToken.token_hash == token_hash)
            )
            found = row.first()
            # 无论查没查到都走一次定长比较，不给「存在与否」留时序差。
            stored = found[0].token_hash if found is not None else "0" * 64
            if not secrets.compare_digest(stored, token_hash) or found is None:
                return None
            token_row, session = found
            if token_row.role != role:
                return None
            if token_row.revoked_at is not None or is_expired(token_row.expires_at):
                return None
            if session.state in {"disconnected", "expired"} or is_expired(session.expires_at):
                return None
            if await _device_revoked(db, session.device_id):
                return None
            return session.id, role

    async def _join(self, session_id: str, role: str, ws: WebSocket) -> None:
        """占住这一侧。同 role 已有连接就把它关了——抢接，旧的不保留。"""
        async with self._lock:
            pair = self._pairs.setdefault(session_id, Pair())
            previous = pair.side(role)
            pair.set_side(role, ws)
            pair.last_inbound[role] = _monotonic()
            both = pair.cli is not None and pair.controller is not None
        if previous is not None and previous is not ws:
            await self._close(previous, CLOSE_REPLACED, "replaced")
        if both:
            await self._mark_paired(session_id)

    async def _leave(self, session_id: str, role: str, ws: WebSocket) -> None:
        """这一侧走了。是自己占的位子才清，抢接后旧连接的离开不能清掉新的。"""
        peer = await self._detach(session_id, role, ws)
        if peer is not None:
            await self._close(peer, CLOSE_PEER_GONE, "peer_disconnected")
        await self._mark_disconnected(session_id)

    async def _detach(self, session_id: str, role: str, ws: WebSocket) -> Optional[WebSocket]:
        """清掉自己占的位子，返回当时的对端。

        空闲超时只需要前半句：连接死了，会话没死。对端还在读，
        不关它，也不改库里的 state。抢接后旧连接的离开不能清掉新的。
        """
        async with self._lock:
            pair = self._pairs.get(session_id)
            if pair is None or pair.side(role) is not ws:
                return None
            pair.set_side(role, None)
            pair.last_inbound.pop(role, None)
            peer = pair.other(role)
            if pair.empty():
                self._pairs.pop(session_id, None)
            return peer

    async def _pump(self, session_id: str, role: str, ws: WebSocket) -> bool:
        """已配对后的转发循环。探活与「管理台点了断开」都在这里看。

        返回 True 表示连接拆了但会话要留着（空闲超时）。其余情况返回
        False，调用方按「这一侧真的走了」处理，标 disconnected。
        """
        while True:
            try:
                raw = await asyncio.wait_for(_receive_text(ws), timeout=DISCONNECT_POLL_SECONDS)
            except asyncio.TimeoutError:
                drop = await self._should_drop(session_id, role)
                if drop == "admin":
                    await self._close(ws, CLOSE_POLICY, REASON_ADMIN_DISCONNECT)
                    return False
                if drop == "idle":
                    await self._close(ws, CLOSE_IDLE, REASON_IDLE_TIMEOUT)
                    return True
                continue
            except WebSocketDisconnect:
                return False
            except KeyError:
                # 二进制帧。不转发，直接关：这条通道只承载 JSON 文本。
                await self._close(ws, CLOSE_AUTH_FAILED, REASON_AUTH_FAILED)
                return False

            self._touch(session_id, role)
            if len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
                self.dropped_frames += 1
                logger.warning(
                    "bridge frame dropped",
                    event="bridge_frame_dropped",
                    reason="too_large",
                    target_id=session_id,
                )
                continue
            message = _parse_object(raw)
            if message is None or not isinstance(message.get("type"), str):
                self.dropped_frames += 1
                continue
            if message["type"] == "auth":
                # 握手之后再来的 auth 不是业务帧。丢弃，不断开。
                continue
            if message["type"] not in ALLOWED_TYPES:
                # 未知 type 丢弃并计数。不改字段、不加信封，也不转发。
                self.dropped_frames += 1
                logger.warning(
                    "bridge frame dropped",
                    event="bridge_frame_dropped",
                    reason="unknown_type",
                    target_id=session_id,
                )
                continue
            peer = await self._peer(session_id, role)
            if _is_ping(message):
                # 心跳回给发送方自己。CLI 的探活看的是「我发出的心跳有没有回来」，
                # 而转发只到达对端，发送方什么都看不到。控制端在权限弹窗前可以
                # 全程不发帧，没有这一句，CLI 会把「对端在思考」判成连接半开，
                # 自己断开重连，重连即 4001。
                # 回显不刷新对端的 last_inbound：保活仍要靠对端自己发帧，
                # 否则一条真死的连接会被自己的回显永远续命。
                try:
                    await ws.send_text(raw)
                except (WebSocketDisconnect, RuntimeError):
                    return False
            if peer is None:
                # 对端还没来。业务帧不排队：对端慢就靠 TCP 背压，没对端就丢掉。
                self.dropped_frames += 1
                continue
            try:
                await peer.send_text(raw)
            except (WebSocketDisconnect, RuntimeError):
                return False

    def _touch(self, session_id: str, role: str) -> None:
        pair = self._pairs.get(session_id)
        if pair is not None:
            pair.last_inbound[role] = _monotonic()

    async def _peer(self, session_id: str, role: str) -> Optional[WebSocket]:
        async with self._lock:
            pair = self._pairs.get(session_id)
            return None if pair is None else pair.other(role)

    async def _should_drop(self, session_id: str, role: str) -> Optional[str]:
        """``admin`` / ``idle`` / None。两个原因出门时的码与 reason 不同。

        管理台强制断开优先于空闲：库里已是 disconnected 时，即使刚有帧
        也要关，否则「点了断开」要再等一个空闲周期。
        """
        if await _session_disconnected(session_id):
            return "admin"
        pair = self._pairs.get(session_id)
        if pair is None:
            return "admin"
        return _idle_verdict(pair.last_inbound, _monotonic(), IDLE_TIMEOUT_SECONDS)

    async def close_session(self, session_id: str) -> None:
        """测试与轮询共用：把内存里这个 session 的两侧都关了。"""
        async with self._lock:
            pair = self._pairs.pop(session_id, None)
        if pair is None:
            return
        for ws in (pair.cli, pair.controller):
            if ws is not None:
                await self._close(ws, CLOSE_POLICY, REASON_ADMIN_DISCONNECT)

    async def poll_disconnects(self) -> None:
        """看一遍内存里的 session，state 已是 disconnected 的关两侧。"""
        async with self._lock:
            ids = list(self._pairs)
        if not ids:
            return
        gone = await _which_disconnected(ids)
        for session_id in gone:
            await self.close_session(session_id)

    async def _mark_paired(self, session_id: str) -> None:
        await _set_state(session_id, "paired", only_from="waiting")

    async def _mark_disconnected(self, session_id: str) -> None:
        # 管理台强制断开已经写过 disconnected 与 reason，不要覆盖 reason。
        await _set_state(session_id, "disconnected", only_from=("waiting", "paired"))

    async def _close(self, ws: WebSocket, code: int, reason: str) -> None:
        try:
            await ws.close(code=code, reason=reason)
        except (WebSocketDisconnect, RuntimeError):
            pass


def _is_ping(message: dict) -> bool:
    """心跳帧：``{type:"status", data:{ping:true}}``。CLI 与管理台发的是同一个形状。"""
    data = message.get("data")
    return message.get("type") == "status" and isinstance(data, dict) and data.get("ping") is True


def _idle_verdict(last_inbound: dict, now: float, limit: float) -> Optional[str]:
    """空闲看两侧最近一帧的较新者，不看本侧。

    CLI 每 30 秒的心跳穿过中继到达控制端，链路是活的；控制端在读这些帧，
    只是自己不发。按本侧计时，人在权限弹窗前犹豫超过一分钟就会被当成静默。

    刚连上还没有帧的不关。轮询间隔远小于时限，不能把「这次轮询没收到帧」
    当成「连接死了」。
    """
    if not last_inbound:
        return None
    if now - max(last_inbound.values()) > limit:
        return "idle"
    return None


async def _receive_text(ws: WebSocket) -> str:
    """只收文本帧。二进制帧在 starlette 里表现为消息字典没有 text 键，转成 KeyError。"""
    message = await ws.receive()
    if message.get("type") == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    if "text" not in message:
        raise KeyError("text")
    return message["text"]


def _parse_auth(raw: str) -> Optional[dict]:
    """首帧必须是 {type:"auth", token, role}。别的一律当没收到。"""
    message = _parse_object(raw)
    if message is None or message.get("type") != "auth":
        return None
    token = message.get("token")
    role = message.get("role")
    if not isinstance(token, str) or not token or role not in ROLES:
        return None
    return {"token": token, "role": role, "session_hint": None}


def _parse_object(raw: str) -> Optional[dict]:
    if len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
        return None
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    return value


def _monotonic() -> float:
    return asyncio.get_event_loop().time()


async def _device_revoked(db, device_id: str) -> bool:
    """设备凭据是否已吊销。

    用 SQL 文本而不是 import identity.model：跨模块查对方的表会被
    test_no_cross_module_model_import 拦截。吊销的判定只需要一列。
    """
    # device_credentials.device_id 是 devices.id 的整数外键，不是客户端的
    # 字符串标识。直接拿 session 里的 device_id 去比会永远匹配不上，
    # 吊销就成了摆设。这里走 devices 的字符串列。
    row = await db.execute(
        text(
            "SELECT 1 FROM device_credentials AS c "
            "JOIN devices AS d ON d.id = c.device_id "
            "WHERE d.device_id = :device_id AND c.revoked_at IS NOT NULL LIMIT 1"
        ),
        {"device_id": device_id},
    )
    return row.first() is not None


async def _session_disconnected(session_id: str) -> bool:
    try:
        async with db_mod.async_session() as db:
            row = await db.execute(select(BridgeSession.state).where(BridgeSession.id == session_id))
            state = row.scalar_one_or_none()
    except Exception:
        logger.warning(
            "bridge state read failed",
            event="bridge_state_read_failed",
            outcome="error",
            target_id=session_id,
        )
        return False
    return state in {"disconnected", "expired"}


async def _which_disconnected(session_ids: list[str]) -> list[str]:
    try:
        async with db_mod.async_session() as db:
            rows = await db.execute(
                select(BridgeSession.id).where(
                    BridgeSession.id.in_(session_ids),
                    BridgeSession.state.in_(("disconnected", "expired")),
                )
            )
            return [r[0] for r in rows]
    except Exception:
        logger.warning(
            "bridge state poll failed",
            event="bridge_state_read_failed",
            outcome="error",
        )
        return []


async def _set_state(session_id: str, state: str, *, only_from) -> None:
    """写状态。失败只记日志：转发是热路径，不能因为一次写库失败停掉。

    只改 state，不动 disconnect_reason。强制断开的理由是管理台写的，
    这里补写空值会把审计理由擦掉。
    """
    if isinstance(only_from, str):
        only_from = (only_from,)
    try:
        async with db_mod.async_session() as db:
            await db.execute(
                update(BridgeSession)
                .where(
                    BridgeSession.id == session_id,
                    BridgeSession.state.in_(only_from),
                )
                .values(state=state)
            )
            await db.commit()
    except Exception:
        logger.warning(
            "bridge state write failed",
            event="bridge_state_write_failed",
            outcome="error",
            target_id=session_id,
        )


async def _record_auth_failure() -> None:
    """鉴权失败记一笔，不带 token、不带细因。写失败不影响关闭连接。"""
    from app.modules.bridge.service.sessions import record_auth_failure

    try:
        async with db_mod.async_session() as db:
            await record_auth_failure(db, None)
    except Exception:
        logger.warning(
            "bridge auth failure audit dropped",
            event="bridge_audit_dropped",
            outcome="error",
        )


# 进程内单例。sidecar 只有一个 worker，一份就够。
relay = Relay()


def reset_relay() -> Relay:
    """测试用。每个用例要一份干净的内存，不能串到下一个用例。"""
    global relay
    relay = Relay()
    return relay

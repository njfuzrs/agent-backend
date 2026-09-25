"""bridge 写入门禁。超限即拒绝，不是警告。

上限是防一台被盗设备把 sidecar 撑死，不是容量规划（契约 §7）。
"""

from fastapi import HTTPException

# 单组织同时处于 waiting / paired 的 session 数。超过创建即 429。
MAX_ONLINE_PER_ORG = 50

# 未配对的 session 超过这么久，列表接口把它标成 expired，token 随之失效。
UNPAIRED_WAIT_MINUTES = 10

# session 本身的寿命。活跃不续期：要续就再签发一张，比悄悄续期可审计。
SESSION_TTL_HOURS = 8

# 首帧必须在这么多秒内到达。超时与坏 token 同一个 close code，不给枚举信号。
AUTH_FRAME_TIMEOUT_SECONDS = 5

# 连接这么多秒没有任何入向（含心跳）就当半开，主动关。
IDLE_TIMEOUT_SECONDS = 60

# sidecar 轮询「管理台是不是点了断开」的间隔。2 秒对强制断开可接受，
# 不为此引一条消息通道。
DISCONNECT_POLL_SECONDS = 2

# 单帧上限。超过丢帧计数，不断连接——一个大 tool_result 不该打死配对。
MAX_FRAME_BYTES = 1_000_000

ONLINE_STATES = ("waiting", "paired")

ROLES = frozenset({"cli", "controller"})


def require_role(role: str) -> str:
    """role 只允许两个字面量。别的值一律 422，不落库。"""
    if role not in ROLES:
        raise HTTPException(status_code=422, detail="role 只允许 cli / controller")
    return role


def require_reason(reason: str | None) -> str:
    """强制断开的 reason 必填。空串与纯空白都拒绝。"""
    text = (reason or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="强制断开必须填写 reason")
    return text

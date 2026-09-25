"""事件白名单、上限、截断、指纹。

上限值**写死在常量里，不做成环境变量**（服务端设计 §8）：
    能配 = 生产上可能被调成一个没人记得的值；
    这些数字的依据在契约 §7，改它们要一起改文档 —— 这是对的。

门禁方向：白名单是 **fail-closed**（未知名字不入库），字段校验是**逐条**的
fail-open（坏的那条计 rejected，好的照常入库）。两者不是矛盾：白名单挡的是
「进了库就会长出仪表盘」的脏名字，逐条放行挡的是「一条坏事件毁掉一整批」。
"""

import hashlib
import json
from typing import Any

# --- 上限（契约 §7）。改这里要同步改 01-契约.md ---------------------------------
MAX_EVENTS_PER_REQUEST = 500  # 客户端默认 batchSize=100，重放批次可能更大
MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MiB。500 条 × ~1KB 有 2 倍余量
MAX_METADATA_KEYS = 64  # 实测 ~15 个 _ctx_* + 业务字段
MAX_STRING_VALUE_CHARS = 1024  # 超出**截断不拒绝**，并打 _truncated 标记

# 截断标记位。加在被截断的那条 metadata 里，让「这个值不完整」可见。
TRUNCATED_FLAG = "_truncated"

# --- 事件名白名单（契约 §2）---------------------------------------------------
# = 客户端 `EVENT_NAMES` 全集 + `startup_timing`（历史事件，app.ts 在发，不在
# EVENT_NAMES 表里）+ M4 新增三类。
#
# 为什么要白名单：事件名进了库就会长出仪表盘和查询。一个拼错的名字
# （`tool_faliure`）入库后只会表现为「那条曲线比预期低」，没有任何东西会红。
# 白名单让拼错变成 rejected 计数，可观测。
#
# 代价：客户端加新事件时这里要同步，否则新事件被静默丢。对策是 rejected 落
# event_rejects 表 + logger.warning 带上名字。**不要**做成「自动学习新名字」——
# 那等于没有白名单。
ALLOWED_EVENT_NAMES = frozenset(
    {
        "tool_call",
        "tool_success",
        "tool_failure",
        "permission_prompt",
        "permission_allow",
        "permission_deny",
        "context_compact",
        "context_compact_skipped",
        "command_invoke",
        "command_rejected",
        "error_occurred",
        "memory_index_health",
        "memory_inject",
        "memory_guard",
        "startup_timing",
        # M4 新增（客户端 PR-4.2）
        "policy_enforced",
        "guardrail_triggered",
        "context_assembled",
    }
)

# metadata 里的会话 join key。缺失不拒绝，只是 join 不上。
CTX_SESSION_ID = "_ctx_session_id"

# rejected 的原因分类。落 event_rejects.reason，让「为什么被拒」不用翻日志。
REASON_UNKNOWN_NAME = "unknown_event_name"
REASON_BAD_SHAPE = "bad_shape"
REASON_METADATA_NOT_DICT = "metadata_not_dict"
REASON_TOO_MANY_KEYS = "metadata_too_many_keys"
REASON_NESTED_VALUE = "metadata_nested_value"


class RejectedEvent(Exception):
    """单条事件被拒。**只计数，不退整批**（契约 §1）。

    用异常而不是返回 None：调用方必须拿到「为什么被拒」才能写 event_rejects.reason
    和 logger.warning。返回 None 会让原因在调用点丢失。
    """

    def __init__(self, reason: str, event_name: str = ""):
        super().__init__(reason)
        self.reason = reason
        # 可能是任意字符串（客户端拼错），不保证在白名单里
        self.event_name = event_name


def canonical_json(metadata: dict[str, Any]) -> str:
    """指纹用的规范化 JSON。键排序 + 紧凑分隔符，保证同内容同字节。

    ensure_ascii=False：中文值不转义。与库里存的那份保持同一套编码，
    否则「存的」和「算指纹的」是两个字符串。
    """
    return json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(device_id: str, event_name: str, client_ts: int, metadata: dict[str, Any]) -> str:
    """内容指纹 = 幂等键（契约 §3）。

    客户端**不提供** event_id（全仓没有事件级 UUID），所以幂等键由服务端算。
    `device_id` 来自 DeviceContext 而不是 body —— 两台设备发同样内容的事件不该
    互相去重。

    够用的理由：真正要去重的是「同一条事件被投了两次」，而重投的是**字节级相同**
    的那个对象（disk-cache.ts 原样 JSON.stringify 落盘、原样读回重发），指纹必然相同。

    两种不够用的情况，**不要当 bug 修**：
    1. 两条真实不同的事件恰好 device + name + ts + metadata 全同 → 被误去重，少一条。
       可接受：同一毫秒内同一设备发了两条完全一样的事件，对计数类分析无差别。
    2. 客户端将来加了 event_id 或改了 metadata 富化 → 指纹变化，历史重放会**重复
       入库一次**。发版切换窗口内 deduped 会掉、accepted 会涨，要在那次发版记录里
       说明，不要当异常查。

    明确不做：用 (device_id, session_id, event_name, timestamp) 做键 —— tool_call
    在同一毫秒可以有多条（并行工具），这个键会把它们误并成一条，直接污染
    「哪个工具最不可靠」的分子。
    """
    raw = "\x00".join([device_id, event_name, str(client_ts), canonical_json(metadata)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def validate_metadata(metadata: Any, event_name: str) -> dict[str, Any]:
    """校验并规范化 metadata。返回可入库的副本；不合法抛 RejectedEvent。

    字符串超长**截断不拒绝**：截断掉的是内容，事件本身仍有计数价值。截断后在该条
    metadata 里加 `_truncated: true`，让「这个值不完整」可见。

    嵌套对象/数组直接拒：客户端 `EventMetadataValue` 只允许 string / number /
    boolean，出现嵌套即客户端 bug，入库会让 metadata 的形状不再可预期。
    """
    if not isinstance(metadata, dict):
        raise RejectedEvent(REASON_METADATA_NOT_DICT, event_name)
    if len(metadata) > MAX_METADATA_KEYS:
        raise RejectedEvent(REASON_TOO_MANY_KEYS, event_name)

    cleaned: dict[str, Any] = {}
    truncated = False
    for key, value in metadata.items():
        # 键必须是字符串：JSON 对象键本来就是字符串，非字符串意味着上游不是 JSON
        if not isinstance(key, str):
            raise RejectedEvent(REASON_NESTED_VALUE, event_name)
        # bool 必须在 int 之前判断 —— Python 里 bool 是 int 的子类。
        # None / 嵌套都不在 EventMetadataValue（string | number | boolean）里。
        if isinstance(value, bool):
            cleaned[key] = value
        elif isinstance(value, (int, float)):
            cleaned[key] = value
        elif isinstance(value, str):
            if len(value) > MAX_STRING_VALUE_CHARS:
                cleaned[key] = value[:MAX_STRING_VALUE_CHARS]
                truncated = True
            else:
                cleaned[key] = value
        else:
            # dict / list / 其它 —— EventMetadataValue 不允许
            raise RejectedEvent(REASON_NESTED_VALUE, event_name)

    if truncated:
        cleaned[TRUNCATED_FLAG] = True
    return cleaned


def validate_event_name(name: Any) -> str:
    """白名单校验。未知名字抛 RejectedEvent，**不入库**。"""
    if not isinstance(name, str) or not name:
        raise RejectedEvent(REASON_BAD_SHAPE, "")
    if name not in ALLOWED_EVENT_NAMES:
        # 名字不在这里打。它随 RejectedEvent 进批次汇总那条 events_rejected，
        # 一条批次只留一条日志（方案 §3.6）。这里再记就是每个坏名字一条。
        raise RejectedEvent(REASON_UNKNOWN_NAME, name)
    return name


def validate_client_ts(value: Any, event_name: str) -> int:
    """客户端毫秒 epoch。不做时钟纠偏（契约 §4）。

    为什么不纠偏：客户端时钟偏移无法在服务端单方面判定（没有 NTP 往返）。纠偏会
    制造一个「看起来精确、实际是猜的」时间列。偏移大的机器在 join 时会表现为错位，
    那时按 received_at 排查。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RejectedEvent(REASON_BAD_SHAPE, event_name)
    return int(value)

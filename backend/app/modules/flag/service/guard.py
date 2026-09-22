"""flag 门禁（规划 PR-2.3）。校验失败即拒绝写入，不是警告。

两条规则，都是**在写入时**拦，不是在下发时拦 —— 下发时拦等于「库里已经有一条
放宽安全的 flag，只是恰好没发出去」，下一次改下发逻辑就漏出去了。

① key 必须匹配 `^[a-z][a-z0-9_]*$`
   理由（规划 §4 M2）：客户端 `feature-flags.ts` 的环境变量覆盖路径是
   `SID_CODE_FLAG_${feature.toUpperCase()}`。key 里出现 `-` / `.` / 空格，
   生成的变量名就不是合法 shell 标识符，环境变量覆盖这条路径**静默失效** ——
   flag 看起来能用，但 CI 里想临时关掉它时关不掉。

② key / description 命中放宽安全的词即失败
   理由（规划 §4 M2「硬约束」+ §6）：`GET /ctl/flags` 是**无认证**端点
   （客户端契约如此，不能改）。无认证通道只接受「施加约束」类开关。
   一个叫 `disable_sandbox` 的 flag 挂在无认证端点后面，等于任何能改这张表的人
   （或任何能中间人这条 HTTP 的人）可以关掉全公司的沙箱。放宽走 M3 policy ——
   那条链有 Bearer 设备凭据，且硬准入是生产 TLS。

词表是**子串**匹配，不是整词匹配：`disable_sandbox_v2`、`allowBypass` 都要拦住。
宁可误伤（改个名就行）也不能漏放 —— 这条门禁的代价不对称。
"""

import re

from fastapi import HTTPException

# 客户端 `SID_CODE_FLAG_${key.toUpperCase()}` 必须是合法 shell 标识符
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

KEY_MAX_LENGTH = 128

# 放宽安全限制的词。命中即拒。新增词只能加不能减 —— 减词要能说出「为什么这个词
# 不再意味着放宽」，说不出就不减。
BANNED_SUBSTRINGS = (
    "bypass",
    "disable_sandbox",
    "disable_hook",
    "disable_all_hook",
    "disable_permission",
    "disable_guardrail",
    "disable_policy",
    "disable_audit",
    "disable_security",
    "skip_permission",
    "skip_sandbox",
    "dangerously",
    "unsafe",
    "no_sandbox",
    "allow_all",
    "unrestricted",
    "god_mode",
)


def validate_key(key: str) -> str:
    """校验 flag key。返回原值，便于 `key = validate_key(key)` 串写。"""
    if not key:
        raise HTTPException(status_code=422, detail="flag key 不能为空")
    if len(key) > KEY_MAX_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"flag key 超长（上限 {KEY_MAX_LENGTH}）",
        )
    if not KEY_PATTERN.match(key):
        raise HTTPException(
            status_code=422,
            detail=(
                f"flag key 必须匹配 ^[a-z][a-z0-9_]*$，当前值 {key!r}。"
                "客户端用 SID_CODE_FLAG_<KEY> 做环境变量覆盖，非法标识符会让这条路径静默失效。"
            ),
        )
    _reject_banned(key, field="key")
    return key


def validate_description(description: str) -> str:
    """描述也要过词表 —— 否则 `feature_x` + 描述「关掉沙箱」能绕过 key 检查。"""
    _reject_banned(description or "", field="description")
    return description or ""


def _reject_banned(text: str, field: str) -> None:
    # 归一化：去掉分隔符再比，`disable-sandbox` / `disableSandbox` 都要命中
    normalized = re.sub(r"[^a-z0-9]", "_", text.lower())
    collapsed = normalized.replace("_", "")
    for word in BANNED_SUBSTRINGS:
        if word in normalized or word.replace("_", "") in collapsed:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"flag {field} 命中禁用词 {word!r}：flag 不得用于放宽安全限制"
                    "（规划 §4 M2 硬约束）。GET /ctl/flags 是无认证端点，"
                    "只接受「施加约束」类开关；放宽类走 M3 policy（Bearer + TLS）。"
                ),
            )

"""policy 写入门禁。校验失败即拒绝写入，不是警告。

两条纪律都是**在写入时**拦，不是在下发时拦 —— 下发时拦等于「库里已经有一份
空策略 / 未知字段，只是恰好没发出去」，下一次改下发逻辑就漏出去了。

settings 必须是客户端 `PolicySettings` 的子集（契约 01-契约.md）：
  - 顶层键闭集，出现 `source` / `policyEndpoint` / 其它键 → 422
  - `policyLimits` 只允许客户端真正有 gate 的四个 feature
  - 至少一项约束，否则 200 + `{"source":"remote"}` 会作为 remote 盖掉本地 managed

不要复用 flag 的 `BANNED_SUBSTRINGS`。policy **允许** `disableAllHooks` 这类
字段——这正是放宽/收紧走 policy 的原因。门禁方向相反：flag 禁这些词，
policy 用这些**字段**。
"""

from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException

# 客户端 PolicySettings 允许的顶层键（不含 source：下发时写死 remote）
ALLOWED_TOP_LEVEL = frozenset(
    {
        "permissions",
        "policyLimits",
        "allowManagedPermissionRulesOnly",
        "disableAllHooks",
        "allowManagedHooksOnly",
        "disabledModes",
        "disableBypassPermissionsMode",
        "strictPluginOnlyCustomization",
        "bridgeEnabled",
        "strictKnownMarketplaces",
    }
)

FORBIDDEN_TOP_LEVEL = frozenset({"source", "policyEndpoint", "SID_CODE_POLICY_ENDPOINT", "endpoint"})

PERMISSION_KEYS = frozenset({"allow", "deny", "ask"})

# 客户端 policy-limits.ts 真正接了线的四个 feature。其余六个写入即 422：
# hooks / bypass_permissions / auto_mode 已由别的字段把关；
# sandbox_bypass / file_upload / network_access 客户端没有 gate，配了也不会拦。
ALLOWED_POLICY_LIMITS = frozenset({"mcp", "sub_agent", "custom_commands", "extensions"})

REJECTED_POLICY_LIMITS = {
    "hooks": "hooks 由 disableAllHooks / allowManagedHooksOnly 把关，不要再用 policyLimits.hooks",
    "bypass_permissions": "bypass 由 disabledModes / disableBypassPermissionsMode 把关",
    "auto_mode": "auto_mode 由 disabledModes 把关",
    "sandbox_bypass": "sandbox_bypass 语义反了（沙箱默认关），客户端没有这个 gate",
    "file_upload": "file_upload 无对应功能，客户端没有这个 gate",
    "network_access": "network_access 无单一咽喉，客户端没有这个 gate",
}

VALID_PERMISSION_MODES = frozenset(
    {
        "default",
        "manual",
        "always-allow",
        "deny-write",
        "acceptEdits",
        "plan",
        "dontAsk",
        "auto",
        "dangerously-skip-permissions",
    }
)

VALID_SURFACES = frozenset({"commands", "skills", "agents", "hooks", "mcp-servers"})
VALID_BYPASS = frozenset({"disable", "allow"})

# strictKnownMarketplaces 的上限与 http 例外。
# 32 项：企业里市场是个位数，32 是「配错了」的信号而不是正常规模。
MAX_KNOWN_MARKETPLACES = 32
MAX_MARKETPLACE_URL_CHARS = 2048
# 只有本机回环可以用 http（本地起一个 index 做验收）。别的主机走 http
# = index 可被中间人替换，白名单就形同虚设。
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def validate_settings(settings: Any) -> dict[str, Any]:
    """校验并归一化 settings。返回可入库的 dict（不含 source，丢掉 allowed=true 的 limit）。"""
    if not isinstance(settings, dict):
        raise HTTPException(status_code=422, detail="settings 必须是 object")

    unknown = [k for k in settings if k not in ALLOWED_TOP_LEVEL]
    forbidden = [k for k in unknown if k in FORBIDDEN_TOP_LEVEL]
    if forbidden:
        raise HTTPException(
            status_code=422,
            detail=(
                f"settings 不得含 {forbidden}：source 由服务端下发时写死 remote，"
                "端点地址只来自 SID_CODE_POLICY_ENDPOINT，远程策略自己改不了。"
            ),
        )
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"settings 含未知字段 {unknown}。只能是客户端 PolicySettings 子集。",
        )

    out: dict[str, Any] = {}
    if "permissions" in settings:
        out["permissions"] = _validate_permissions(settings["permissions"])
    if "policyLimits" in settings:
        limits = _validate_policy_limits(settings["policyLimits"])
        if limits:
            out["policyLimits"] = limits
    for key in (
        "allowManagedPermissionRulesOnly",
        "disableAllHooks",
        "allowManagedHooksOnly",
    ):
        if key in settings:
            if not isinstance(settings[key], bool):
                raise HTTPException(status_code=422, detail=f"{key} 必须是 bool")
            out[key] = settings[key]
    if "disabledModes" in settings:
        out["disabledModes"] = _validate_disabled_modes(settings["disabledModes"])
    if "disableBypassPermissionsMode" in settings:
        value = settings["disableBypassPermissionsMode"]
        if value not in VALID_BYPASS:
            raise HTTPException(
                status_code=422,
                detail="disableBypassPermissionsMode 只允许 'disable' | 'allow'",
            )
        out["disableBypassPermissionsMode"] = value
    if "strictPluginOnlyCustomization" in settings:
        out["strictPluginOnlyCustomization"] = _validate_strict(
            settings["strictPluginOnlyCustomization"]
        )
    if "strictKnownMarketplaces" in settings:
        out["strictKnownMarketplaces"] = _validate_known_marketplaces(
            settings["strictKnownMarketplaces"]
        )
    if "bridgeEnabled" in settings:
        # 只有 false 是约束。true 与省略同义（不关），写入时丢掉，避免空欢喜。
        if not isinstance(settings["bridgeEnabled"], bool):
            raise HTTPException(status_code=422, detail="bridgeEnabled 必须是 bool")
        if settings["bridgeEnabled"] is False:
            out["bridgeEnabled"] = False

    if not _has_constraint(out):
        raise HTTPException(
            status_code=422,
            detail="空策略会作为 remote 盖掉本地 managed。至少配一项约束（deny / 关掉的 feature / 收紧方向的布尔 / disabledModes / bypass=disable / strictPluginOnly / strictKnownMarketplaces）。",
        )
    return out


def _validate_permissions(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="permissions 必须是 object")
    unknown = [k for k in value if k not in PERMISSION_KEYS]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"permissions 只允许 allow / deny / ask，出现了 {unknown}",
        )
    out: dict[str, list[str]] = {}
    for key, items in value.items():
        if not isinstance(items, list) or any(not isinstance(x, str) or not x for x in items):
            raise HTTPException(status_code=422, detail=f"permissions.{key} 必须是非空字符串数组")
        # 丢掉空数组，省略 = 未配置
        if items:
            out[key] = list(items)
    return out


def _validate_policy_limits(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="policyLimits 必须是 object")
    out: dict[str, dict[str, Any]] = {}
    for feature, spec in value.items():
        if feature in REJECTED_POLICY_LIMITS:
            raise HTTPException(status_code=422, detail=REJECTED_POLICY_LIMITS[feature])
        if feature not in ALLOWED_POLICY_LIMITS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"policyLimits.{feature} 客户端没有 gate，配了也不会拦。"
                    f"允许的 key：{sorted(ALLOWED_POLICY_LIMITS)}"
                ),
            )
        if not isinstance(spec, dict) or "allowed" not in spec:
            raise HTTPException(
                status_code=422,
                detail=f"policyLimits.{feature} 必须是 {{allowed: bool, reason?: str}}",
            )
        extra = [k for k in spec if k not in {"allowed", "reason"}]
        if extra:
            raise HTTPException(
                status_code=422,
                detail=f"policyLimits.{feature} 含未知字段 {extra}",
            )
        if not isinstance(spec["allowed"], bool):
            raise HTTPException(status_code=422, detail=f"policyLimits.{feature}.allowed 必须是 bool")
        # allowed=true 等于没配（客户端未配置当允许）。写入时丢掉，避免空欢喜。
        if spec["allowed"] is True:
            continue
        reason = spec.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise HTTPException(
                status_code=422,
                detail=f"policyLimits.{feature}.allowed=false 时 reason 必填（会原样展示给用户）",
            )
        out[feature] = {"allowed": False, "reason": reason.strip()}
    return out


def _validate_disabled_modes(value: Any) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise HTTPException(status_code=422, detail="disabledModes 必须是 string[]")
    unknown = [m for m in value if m not in VALID_PERMISSION_MODES]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"disabledModes 含未知 mode {unknown}。有效值：{sorted(VALID_PERMISSION_MODES)}",
        )
    return list(value)


def _validate_strict(value: Any) -> bool | list[str]:
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        unknown = [s for s in value if s not in VALID_SURFACES]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"strictPluginOnlyCustomization 含未知 surface {unknown}。有效值：{sorted(VALID_SURFACES)}",
            )
        if any(not isinstance(s, str) for s in value):
            raise HTTPException(status_code=422, detail="strictPluginOnlyCustomization 数组元素必须是 string")
        return list(value)
    raise HTTPException(
        status_code=422,
        detail="strictPluginOnlyCustomization 只允许 bool 或 customization surface 数组",
    )


def _normalize_marketplace_url(raw: Any) -> str:
    """校验一个市场 index URL 并去掉尾斜杠。不合法 422。

    客户端按「去尾斜杠后的字符串」做相等比较，所以服务端存归一化后的值，
    两端才不会因为一个 `/` 对不上。不做大小写 / 端口等其它归一化：
    多做一步就多一处与客户端不一致的可能。
    """
    if not isinstance(raw, str) or not raw:
        raise HTTPException(status_code=422, detail="strictKnownMarketplaces[].url 必须是非空字符串")
    if len(raw) > MAX_MARKETPLACE_URL_CHARS:
        raise HTTPException(status_code=422, detail="strictKnownMarketplaces[].url 过长")
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw):
        raise HTTPException(status_code=422, detail=f"市场 URL 含空白或控制字符：{raw!r}")
    # 用原串判 query / fragment：urlsplit 对空 query（结尾一个 ?）给出 ""，会漏掉
    if "?" in raw or "#" in raw:
        raise HTTPException(status_code=422, detail=f"市场 URL 不允许 query / fragment：{raw}")
    if raw.startswith("https://"):
        scheme = "https"
    elif raw.startswith("http://"):
        scheme = "http"
    else:
        # 只认小写 scheme：客户端是字符串比较，HTTPS:// 会被当成另一个市场
        raise HTTPException(status_code=422, detail=f"市场 URL 必须是 https（本机回环可用 http）：{raw}")
    try:
        parts = urlsplit(raw)
        _ = parts.port  # 非法端口在这里抛 ValueError
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"市场 URL 无法解析：{raw}") from exc
    if "@" in parts.netloc:
        raise HTTPException(status_code=422, detail=f"市场 URL 不允许带 userinfo：{raw}")
    host = parts.hostname or ""
    if not host:
        raise HTTPException(status_code=422, detail=f"市场 URL 缺少主机名：{raw}")
    if scheme == "http" and host not in LOOPBACK_HOSTS:
        raise HTTPException(
            status_code=422,
            detail=f"http 只允许 127.0.0.1 / localhost / [::1]，其余必须 https：{raw}",
        )
    return raw.rstrip("/")


def _validate_known_marketplaces(value: Any) -> list[dict[str, str]]:
    """校验并归一化 strictKnownMarketplaces。保留顺序去重；空数组原样保留（= 禁一切插件）。"""
    if not isinstance(value, list):
        raise HTTPException(status_code=422, detail="strictKnownMarketplaces 必须是数组")
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail="strictKnownMarketplaces[] 必须是 {source, url}")
        extra = [k for k in item if k not in {"source", "url"}]
        if extra:
            raise HTTPException(status_code=422, detail=f"strictKnownMarketplaces[] 含未知字段 {extra}")
        if item.get("source") != "url":
            raise HTTPException(status_code=422, detail="strictKnownMarketplaces[].source 只允许 'url'")
        url = _normalize_marketplace_url(item.get("url"))
        if url in seen:
            continue
        seen.add(url)
        out.append({"source": "url", "url": url})
    # 上限按去重后计：重复项不该让一份合理配置被拒
    if len(out) > MAX_KNOWN_MARKETPLACES:
        raise HTTPException(
            status_code=422,
            detail=f"strictKnownMarketplaces 最多 {MAX_KNOWN_MARKETPLACES} 项（去重后 {len(out)} 项）",
        )
    return out


def _has_constraint(settings: dict[str, Any]) -> bool:
    """至少一项约束，避免「空 remote」盖掉本地护栏。"""
    perms = settings.get("permissions") or {}
    if any(perms.get(k) for k in PERMISSION_KEYS):
        return True
    if settings.get("policyLimits"):
        return True
    if settings.get("disableAllHooks") is True:
        return True
    if settings.get("allowManagedHooksOnly") is True:
        return True
    if settings.get("allowManagedPermissionRulesOnly") is True:
        return True
    if settings.get("disabledModes"):
        return True
    if settings.get("disableBypassPermissionsMode") == "disable":
        return True
    strict = settings.get("strictPluginOnlyCustomization")
    if strict is True:
        return True
    if isinstance(strict, list) and strict:
        return True
    # false 是「禁止遥控」这一项约束。只关 Bridge 的策略因此不是空策略。
    if settings.get("bridgeEnabled") is False:
        return True
    # 数组（含空数组）都是约束：空数组 = 除内置外禁一切插件，是最严的一档。
    if isinstance(settings.get("strictKnownMarketplaces"), list):
        return True
    return False

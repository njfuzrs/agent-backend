"""飞书 user token 的落库、取用与刷新（方案 §6.3 / §6.4）。

三条纪律：
- 明文 token 只在内存里活一次调用；库里只有 Fernet 密文。
- refresh_token 一次性（飞书刷新后旧的立即作废），并发刷新必须串行：
  PG 用 SELECT ... FOR UPDATE 行锁跨 worker 串行，SQLite（本地 / 测试）用进程内锁。
  **拿到锁之后再检查一次是否已被别人刷新过** —— 这是关键，少了它第二个请求会拿着
  已作废的 refresh_token 去刷新，用户被迫重新授权。
- 刷新失败一律 DelegationError，**绝不改用 tenant_access_token**（方案 §1.2 不变量）。

取用走独立短会话，不占用请求级 get_db：轮换后的 refresh_token 必须立刻提交，
不能因为后面调飞书文档失败、请求回滚而丢掉（丢了就等于用户被登出）。
"""

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import db as db_mod
from app.core.timeutil import is_expired, parse_iso, utc_now, utc_now_iso
from app.modules.auth.service import feishu as feishu_oauth
from app.modules.feishu.model import FeishuToken
from app.modules.feishu.service import crypto

# access 剩余不足这么久就先刷新：留出一次文档读取的时间，不在半路过期。
REFRESH_MARGIN = timedelta(minutes=5)
# 授权满 365 天必须重新授权（飞书 20037）。提前 7 天在授权状态里提示。
GRANT_MAX_DAYS = 365
GRANT_WARN_DAYS = 7

# DelegationError.reason 的取值
REASON_NOT_AUTHORIZED = "not_authorized"  # 库里没有这个人的 token（没配密钥时登录过 / 已被删）
REASON_REAUTH = "reauth_required"  # refresh 失败 / 没有 refresh_token / 解密失败 / 满 365 天
REASON_UNAVAILABLE = "unavailable"  # 飞书刷新接口暂时不可用，可重试

# 键带上事件循环：asyncio.Lock 绑定首次争用它的循环，跨循环复用会抛 RuntimeError。
_process_locks: dict[tuple[int, int], asyncio.Lock] = {}


def _refresh_lock(user_id: int) -> asyncio.Lock:
    """同进程内同一个人的刷新串行。跨 worker 靠 PG 的 FOR UPDATE。"""
    return _process_locks.setdefault((id(asyncio.get_running_loop()), user_id), asyncio.Lock())


class DelegationError(Exception):
    """拿不到可用的 user_access_token。code 是飞书错误码（有的话），只用于审计。"""

    def __init__(self, reason: str, code: str = ""):
        super().__init__(f"delegation failed: {reason}")
        self.reason = reason
        self.code = code


@dataclass(frozen=True)
class GrantStatus:
    """管理台展示用。不含任何 token。"""

    user_id: int
    scope: str
    access_expires_at: str
    refresh_expires_at: Optional[str]
    granted_at: str
    updated_at: str
    version: int
    reauth_soon: bool


def _expires_at(seconds: int) -> str:
    return (utc_now() + timedelta(seconds=seconds)).isoformat()


async def store_grant(db: AsyncSession, user_id: int, tokens: feishu_oauth.FeishuTokenSet) -> None:
    """登录回调里调用：新的一次授权覆盖旧的（含 granted_at）。不 commit，与登录审计同一个事务。

    expires_in 缺失按 0 处理（立刻视为过期、下次取用先刷新），不猜默认时长。
    """
    now = utc_now_iso()
    row = await db.get(FeishuToken, user_id)
    if row is None:
        row = FeishuToken(user_id=user_id, version=0)
        db.add(row)
    row.access_enc = crypto.encrypt(tokens.access_token)
    row.access_expires_at = _expires_at(tokens.expires_in)
    row.refresh_enc = crypto.encrypt(tokens.refresh_token) if tokens.refresh_token else None
    row.refresh_expires_at = _expires_at(tokens.refresh_expires_in) if tokens.refresh_token else None
    row.scope = tokens.scope
    row.granted_at = now
    row.updated_at = now
    row.version = (row.version or 0) + 1
    await db.flush()


async def delete_for_user(db: AsyncSession, user_id: int) -> bool:
    """吊销用户时调用（与 users.status=revoked 同一个事务）。返回是否删掉了一行。不 commit。"""
    result = await db.execute(delete(FeishuToken).where(FeishuToken.user_id == user_id))
    return bool(result.rowcount)


async def grant_status(db: AsyncSession) -> list[GrantStatus]:
    rows = await db.execute(select(FeishuToken).order_by(FeishuToken.user_id))
    out = []
    warn_after = timedelta(days=GRANT_MAX_DAYS - GRANT_WARN_DAYS)
    for r in rows.scalars():
        out.append(
            GrantStatus(
                user_id=r.user_id,
                scope=r.scope,
                access_expires_at=r.access_expires_at,
                refresh_expires_at=r.refresh_expires_at,
                granted_at=r.granted_at,
                updated_at=r.updated_at,
                version=r.version,
                reauth_soon=utc_now() >= parse_iso(r.granted_at) + warn_after,
            )
        )
    return out


def _fresh(row: FeishuToken) -> bool:
    return not is_expired(row.access_expires_at, utc_now() + REFRESH_MARGIN)


async def get_access_token(user_id: int, *, force_refresh: bool = False) -> tuple[str, str]:
    """返回 (user_access_token, 实际授予的 scope)。必要时刷新。

    force_refresh：飞书说 access_token 无效时用（例如被提前作废）。进锁后若发现别人已经
    刷新过（version 变了），直接用别人刷出来的，不再刷第二次。
    """
    async with db_mod.async_session() as db:
        # 先无锁看一眼：绝大多数调用 token 都新鲜，不必排队。
        row = await db.get(FeishuToken, user_id)
        if row is None:
            raise DelegationError(REASON_NOT_AUTHORIZED)
        seen_version = row.version
        if not force_refresh and _fresh(row):
            return _decrypt_or_reauth(row.access_enc), row.scope

    async with _refresh_lock(user_id), db_mod.async_session() as db:
        # PG：行锁，跨 worker 串行，事务结束（commit / 关会话回滚）释放。
        # SQLite 不支持 FOR UPDATE，方言会省略它，靠上面的进程锁（本地单进程）。
        row = (
            await db.execute(select(FeishuToken).where(FeishuToken.user_id == user_id).with_for_update())
        ).scalar_one_or_none()
        if row is None:
            raise DelegationError(REASON_NOT_AUTHORIZED)
        # 二次检查（方案 §6.4 的关键）：排队期间别人已经刷新过，直接用别人刷出来的。
        if _fresh(row) and (row.version != seen_version or not force_refresh):
            return _decrypt_or_reauth(row.access_enc), row.scope
        if not row.refresh_enc or (row.refresh_expires_at and is_expired(row.refresh_expires_at)):
            raise DelegationError(REASON_REAUTH, "no_refresh_token")
        refresh_token = _decrypt_or_reauth(row.refresh_enc)
        try:
            tokens = await feishu_oauth.refresh_user_token(refresh_token)
        except feishu_oauth.FeishuError as exc:
            if exc.code in feishu_oauth.TRANSIENT_CODES:
                raise DelegationError(REASON_UNAVAILABLE, exc.code) from None
            # refresh_token 已不可用（过期 / 被轮换 / 20037 满 365 天 / 用户离职）。
            # 删掉这一行：后续调用直接提示重新登录，不再拿死 token 反复打飞书。
            await db.delete(row)
            await db.commit()
            raise DelegationError(REASON_REAUTH, exc.code) from None
        del refresh_token
        row.access_enc = crypto.encrypt(tokens.access_token)
        row.access_expires_at = _expires_at(tokens.expires_in)
        if tokens.refresh_token:
            row.refresh_enc = crypto.encrypt(tokens.refresh_token)
            row.refresh_expires_at = _expires_at(tokens.refresh_expires_in)
        # 刷新响应里的 scope 是实际授予的；没给就沿用旧的。granted_at 不变（365 天从授权算起）。
        row.scope = tokens.scope or row.scope
        row.updated_at = utc_now_iso()
        row.version = row.version + 1
        scope = row.scope
        # 旧 refresh_token 在飞书侧已作废：新的必须先提交成功，才把 access 交出去。
        await db.commit()
        return tokens.access_token, scope


def _decrypt_or_reauth(ciphertext: str) -> str:
    try:
        return crypto.decrypt(ciphertext)
    except crypto.TokenDecryptError:
        raise DelegationError(REASON_REAUTH, "decrypt_failed") from None

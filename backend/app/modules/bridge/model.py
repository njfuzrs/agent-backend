"""bridge 模块 ORM：bridge_sessions + bridge_session_tokens + bridge_audit。

列类型跟本仓惯例：时间用 Text 存 ISO，秘密只存 sha256 hex。

为什么三张表而不是一张：
    session 是「一次遥控」的事实，给列表和强制断开关联用。
    token 是凭证，每个角色一张，重签要能吊销旧的而不丢掉 session。
    audit 是管理动作。中继转发的帧正文**不入库**——那是 BI，不是审计。

为什么不建 FK 到 devices：
    设备删了，审计仍要能回答「这台机器被遥控过」。与 policy / events 同款理由。
    device_id / org_id / team_id 是签发当时从 DeviceContext 抄下来的文本副本。

为什么 token 不建 FK 到 session：
    session 行先于 token 存在，但强制断开后 session 还要留着给审计。逻辑关联
    用 session_id 等值，不靠数据库级联。
"""

from sqlalchemy import Column, Index, Integer, Text

from app.core.db import Base


class BridgeSession(Base):
    """一次遥控会话。state 是给列表看的，配对的真相在 sidecar 内存里。

    `device_id` / `org_id` / `team_id` 一律来自 `DeviceContext`，**不从 body 取**。
    body 里的同名字段是攻击面（与 events 同款）。
    """

    __tablename__ = "bridge_sessions"

    # br_ 前缀 + ulid。既是主键也是配对键，CLI 与 controller 靠它找到彼此。
    id = Column(Text, primary_key=True)
    device_id = Column(Text, nullable=False)
    org_id = Column(Text, nullable=False)
    team_id = Column(Text, nullable=False, default="")
    # 客户端自报，只展示。不要拿它做任何判定。
    ver = Column(Text, nullable=True)
    # 只存目录名。绝对路径是 PII，列表上不需要。
    cwd_basename = Column(Text, nullable=True)
    # waiting / paired / disconnected / expired
    state = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False)
    expires_at = Column(Text, nullable=False)
    # 管理台强制断开时的理由。正常断开留空。
    disconnect_reason = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_bridge_sessions_device", "device_id"),
        Index("idx_bridge_sessions_org_state", "org_id", "state"),
    )


class BridgeSessionToken(Base):
    """一张 session token 的 hash。明文只在签发响应里出现一次，库里没有。

    每个 (session_id, role) 一行。重签先把旧行 revoked_at 填上再插新行——
    唯一索引只管「当前这一张」，所以重签是「更新旧行 + 插入」，不是两条并存。
    """

    __tablename__ = "bridge_session_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Text, nullable=False)
    # cli / controller。role 不匹配的 token 即使 hash 对上也拒绝。
    role = Column(Text, nullable=False)
    token_hash = Column(Text, nullable=False)
    expires_at = Column(Text, nullable=False)
    # 非空即拒。不区分「谁吊销的」——那在 bridge_audit 里。
    revoked_at = Column(Text, nullable=True)
    created_at = Column(Text, nullable=False)

    __table_args__ = (
        Index("uq_bridge_token_session_role", "session_id", "role", unique=True),
        Index("uq_bridge_token_hash", "token_hash", unique=True),
    )


class BridgeAudit(Base):
    """中继的管理动作。只追加，不更新，不删除。

    action 闭集：issue_cli / issue_controller / disconnect / auth_fail。
    **不记帧正文，不记 token 原文，不记「过期 vs 吊销」的细因**——
    细因写进 reason 就给了枚举 token 的人一个信号。
    """

    __tablename__ = "bridge_audit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Text, nullable=True)
    # device:<id> 或 web:<username>
    actor = Column(Text, nullable=False)
    action = Column(Text, nullable=False)
    reason = Column(Text, nullable=True)
    created_at = Column(Text, nullable=False)
    # 跳回同一次请求的日志用。可空：没有请求上下文的写入留空，不编造。
    # 不建索引——审计按时间查，这个值是跳转用的，不是检索用的（方案 §3.9）。
    request_id = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_bridge_audit_session", "session_id"),
        Index("idx_bridge_audit_created_at", "created_at"),
    )

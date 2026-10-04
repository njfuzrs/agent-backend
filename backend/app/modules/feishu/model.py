"""委托授权 ORM：feishu_tokens / feishu_call_audit。

- feishu_tokens：一人一行。access / refresh 都用 Fernet 加密存（方案 §6.3），
  过期时间存时间戳不存 TTL（以飞书响应里的 expires_in 为准）。scope 存飞书**实际授予**的，
  服务端可能裁剪。version 每刷新一次 +1，用来在日志 / 测试里看出「是否真的刷新过」。
  user_id 外键 CASCADE：用户行删了 token 跟着删。吊销用户时 service 显式删（同一事务）。
- feishu_call_audit：远程 MCP 每次 tools/call 一行，只追加。只记文档 token，
  **不记**文档内容、搜索词、搜索结果。user_id / device_id 不建外键：人和设备删了，
  审计仍要能回答「谁读过什么」。
"""

from sqlalchemy import Column, ForeignKey, Index, Integer, Text

from app.core.db import Base


class FeishuToken(Base):
    __tablename__ = "feishu_tokens"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE", name="fk_feishu_tokens_user_id"), primary_key=True
    )
    access_enc = Column(Text, nullable=False)
    access_expires_at = Column(Text, nullable=False)
    # 授权时没带 offline_access 就没有 refresh_token：access 过期后只能重新登录。
    refresh_enc = Column(Text, nullable=True)
    refresh_expires_at = Column(Text, nullable=True)
    scope = Column(Text, nullable=False, default="")
    # 用户本次授权的时间。刷新不改它：飞书要求授权满 365 天必须重新授权（错误码 20037）。
    granted_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, default=1)


class FeishuCallAudit(Base):
    """一次远程 MCP 工具调用。只追加，不更新，不删除。"""

    __tablename__ = "feishu_call_audit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(Text, nullable=False)
    # 未登录设备调用时为空（工具直接拒绝，也要留痕）。
    user_id = Column(Integer, nullable=True)
    device_id = Column(Text, nullable=False)
    tool = Column(Text, nullable=False)
    # 文档 / 知识库节点 token。搜索没有目标，为空。
    target_token = Column(Text, nullable=False, default="")
    # ok / forbidden / not_found / scope_missing / reauth_required / not_logged_in /
    # unavailable / bad_request / unsupported。取值见 service/mcp.py 的 OUTCOMES。
    outcome = Column(Text, nullable=False)
    # 飞书错误码（有就记）。不记飞书的错误消息原文。
    error_code = Column(Text, nullable=True)
    latency_ms = Column(Integer, nullable=False, default=0)
    request_id = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_feishu_call_audit_created_at", "created_at"),
        Index("idx_feishu_call_audit_user_id", "user_id"),
    )

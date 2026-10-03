"""人员身份 ORM：users / auth_states / auth_audit。

- users：一人一行。主键之外的唯一键是 (provider, tenant_key, union_id)。
  email 只作展示，不当身份（飞书侧可以改、可以没有）。
- auth_states：OAuth state 只存 sha256，一次性使用（UPDATE ... WHERE used_at IS NULL）。
  同时存浏览器 nonce 的 hash，防登录 CSRF（方案 §5.7）。PKCE verifier 不落库，
  由签名密钥从 state 派生（见 service/states.py）。
- auth_audit：只追加。user_id 不建外键：用户行删了，审计仍要能回答「谁登录过」。
"""

from sqlalchemy import Column, Index, Integer, Text, UniqueConstraint

from app.core.db import Base

ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
ROLES = (ROLE_ADMIN, ROLE_MEMBER)

STATUS_ACTIVE = "active"
STATUS_REVOKED = "revoked"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(Text, nullable=False)  # 现在只有 feishu
    tenant_key = Column(Text, nullable=False)
    union_id = Column(Text, nullable=False)
    open_id = Column(Text, nullable=False, default="")
    name = Column(Text, nullable=False, default="")
    email = Column(Text, nullable=False, default="")
    role = Column(Text, nullable=False)  # admin / member
    status = Column(Text, nullable=False)  # active / revoked
    created_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)
    last_login_at = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("provider", "tenant_key", "union_id", name="uq_users_provider_identity"),
    )


class AuthState(Base):
    """一次 OAuth 往返的 state。kind=web 是管理台；P2 的 CLI 流程会加 kind=cli 与相关列。"""

    __tablename__ = "auth_states"

    state_hash = Column(Text, primary_key=True)
    kind = Column(Text, nullable=False)
    nonce_hash = Column(Text, nullable=False)
    # 登录完成后跳回的管理台路径。只存以 / 开头的站内路径，入库前已校验。
    redirect_to = Column(Text, nullable=False, default="/")
    created_at = Column(Text, nullable=False)
    expires_at = Column(Text, nullable=False)
    used_at = Column(Text, nullable=True)

    __table_args__ = (Index("idx_auth_states_expires_at", "expires_at"),)


class AuthAudit(Base):
    """登录 / 登出 / 口令应急登录 / 吊销 / 改角色。只追加，不更新，不删除。"""

    __tablename__ = "auth_audit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(Text, nullable=False)
    # 被操作的人。口令应急登录没有对应用户，为空。
    user_id = Column(Integer, nullable=True)
    # 做这件事的人：user:<union_id> 或 password:<用户名>。登录时 actor 就是本人。
    actor = Column(Text, nullable=False, default="")
    event = Column(Text, nullable=False)
    detail_json = Column(Text, nullable=True)
    request_id = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_auth_audit_created_at", "created_at"),
        Index("idx_auth_audit_user_id", "user_id"),
    )

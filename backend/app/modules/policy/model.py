"""policy 模块 ORM：policies + policy_audit。

为什么 settings 存 Text 而不是 JSON 列：与 trajectory / identity / flag 同一套
列类型（Text 存 ISO 时间与结构化字段），PG 与 SQLite 行为一致。下发时
`json.loads` 还原成客户端 PolicySettings 的原生类型（`true` 不是 `"true"`）。

为什么不建 FK 到 devices / teams / organizations：设备删了策略行仍在，审计才
讲得清；scope_id 填错是管理台 404/提示问题，不是 DB 级联问题。org_id 是文本
副本，求值收窄用，不 join identity 的表（跨模块直接查表会被边界测试打红）。

为什么 team 匹配要带 org_id：`teams.team_id` 只在组织内唯一
（`uq_teams_org_slug`）。不带 org 会让「上海/infra」命中「北京/infra」。

审计表只追加不更新：一份能盖掉员工本地 managed-settings 的远程策略，
「谁在什么时候把它改成了什么」必须是不可变记录。真删也写一条
（action='delete'），policy_id 走 SET NULL，scope 文本副本保住「删了哪一层」。
"""

from sqlalchemy import Column, ForeignKey, Index, Integer, Text, text

from app.core.db import Base


class Policy(Base):
    """一条策略。同一 (scope_type, scope_id) 只允许一份生效（部分唯一索引）。"""

    __tablename__ = "policies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # device / team / org。对外 slug，不是内部整型 FK
    scope_type = Column(Text, nullable=False)
    scope_id = Column(Text, nullable=False)
    # 文本副本。device/team 行也填所属 org，求值收窄 + 列表筛选
    org_id = Column(Text, nullable=False)
    # PolicySettings **不含 source** 的 JSON 文本
    settings_json = Column(Text, nullable=False)
    # 非空 = 不进下发（对标 flag.disabled_at）
    disabled_at = Column(Text, nullable=True)
    created_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)
    updated_by = Column(Text, nullable=False, default="")

    __table_args__ = (
        Index("idx_policies_scope", "scope_type", "scope_id"),
        Index("idx_policies_org_id", "org_id"),
        Index("idx_policies_disabled_at", "disabled_at"),
        # 同层只能一份生效。SQLite 3.8+ / PG 都支持部分索引。
        # 带 org_id：team_id 只在组织内唯一，两家公司都可以有生效的 infra 策略。
        Index(
            "uq_policies_scope_enabled",
            "scope_type",
            "scope_id",
            "org_id",
            unique=True,
            sqlite_where=text("disabled_at IS NULL"),
            postgresql_where=text("disabled_at IS NULL"),
        ),
    )


class PolicyAudit(Base):
    """策略变更审计。只追加，不更新，不删除。

    policy_id 走 SET NULL 而不是 CASCADE：真删一条策略时审计必须留下来。
    scope_type / scope_id 另存文本副本，policy_id 变 NULL 后仍能知道改的是哪一层。
    """

    __tablename__ = "policy_audit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    policy_id = Column(Integer, ForeignKey("policies.id", ondelete="SET NULL"), nullable=True)
    scope_type = Column(Text, nullable=False)
    scope_id = Column(Text, nullable=False)
    action = Column(Text, nullable=False)  # create / update / delete / disable / enable
    old_settings_json = Column(Text, nullable=True)
    new_settings_json = Column(Text, nullable=True)
    reason = Column(Text, nullable=False)
    actor = Column(Text, nullable=False, default="")
    created_at = Column(Text, nullable=False)

    __table_args__ = (
        Index("idx_policy_audit_scope", "scope_type", "scope_id"),
        Index("idx_policy_audit_created_at", "created_at"),
        Index("idx_policy_audit_policy_id", "policy_id"),
    )

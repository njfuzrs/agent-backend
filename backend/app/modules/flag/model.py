"""flag 模块 ORM：feature_flags + feature_flag_audit。

规划 §4 M2 PR-2.2 点名这两张表。

为什么值存 Text 而不是 JSON 列：客户端契约（`feature-flags.ts`）的 `FlagValue` 是
`string | number | boolean | Record<string, unknown>` 四种之一，下发时必须是**原生 JSON
类型**（`true` 不是 `"true"`）—— 客户端 `getFeatureValue_CACHED_MAY_BE_STALE` 直接把值
当泛型 T 返回，不做二次解析。所以库里存 JSON 文本，出库时 `json.loads` 还原类型。
用 Text 而不是 sa.JSON 是为了与本仓既有列类型一致（trajectory / identity 全用 Text 存
ISO 时间与结构化字段），PG 与 SQLite 行为也完全一致，不必为一张小表引入方言差异。

审计表只追加不更新：一个能改全体客户端行为的开关，「谁在什么时候把它改成了什么」
必须是不可变记录。删除 flag 也写一条（action='delete'），否则「远程删除 → 客户端
回落默认」这条出口发生过但查不到。
"""

from sqlalchemy import Column, ForeignKey, Index, Integer, Text

from app.core.db import Base


class FeatureFlag(Base):
    """一条 flag。key 是对外契约（会变成客户端的 `SID_CODE_FLAG_<KEY>` 环境变量名）。"""

    __tablename__ = "feature_flags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # snake_case，由 service 层的门禁校验（规划 PR-2.3）
    key = Column(Text, nullable=False, unique=True, index=True)
    # JSON 文本。出库 json.loads 还原成 bool / number / string / object
    value_json = Column(Text, nullable=False)
    description = Column(Text, nullable=False, default="")
    # 关掉但不删：下发时跳过，客户端按「远程已删除」回落默认值。
    # 用 Text 存 ISO 时间而不是 Boolean —— 与 trajectory.deleted_at 同一套软删除语义。
    disabled_at = Column(Text, nullable=True)
    created_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)
    updated_by = Column(Text, nullable=False, default="")

    __table_args__ = (
        Index("idx_feature_flags_disabled_at", "disabled_at"),
    )


class FeatureFlagAudit(Base):
    """flag 变更审计。只追加，不更新，不删除。

    flag_id 走 SET NULL 而不是 CASCADE：真删一条 flag 时审计必须留下来，
    否则「谁删的」这件事跟着被删的行一起消失。key 另存一份文本副本，
    flag_id 变 NULL 后仍能知道改的是哪个 key。
    """

    __tablename__ = "feature_flag_audit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    flag_id = Column(Integer, ForeignKey("feature_flags.id", ondelete="SET NULL"), nullable=True)
    key = Column(Text, nullable=False)
    action = Column(Text, nullable=False)  # create / update / delete / disable / enable
    old_value_json = Column(Text, nullable=True)
    new_value_json = Column(Text, nullable=True)
    reason = Column(Text, nullable=False, default="")
    actor = Column(Text, nullable=False, default="")
    created_at = Column(Text, nullable=False)

    __table_args__ = (
        Index("idx_feature_flag_audit_key", "key"),
        Index("idx_feature_flag_audit_created_at", "created_at"),
    )

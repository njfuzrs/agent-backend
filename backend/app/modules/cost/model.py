"""cost 模块 ORM：usage_ledger + budgets + budget_audit。

列类型跟本仓惯例：时间用 Text 存 ISO。金额用 Double：Postgres 上是
`double precision`（float8）。`0010` 写成 REAL，在 Postgres 上落成 `real`
（float4），`0.01` 存成 `0.009999999776482582`，下发后被客户端 `toFixed(2)`
吃成 `$0.00`（验收 F3）。`0011` 把金额列改过来。

不要用 Numeric：SQLite 本地开发会漂。也不要退回 REAL：SQLite 上 REAL 与
DOUBLE 都是 8 字节，精度问题只在 Postgres 的 float4。轨迹
`total_cost_usd` 仍是 REAL，那是另一张表，不在本次范围。

为什么 `ts` 用 Integer 不用 Text：它来自 Unix **秒** epoch（~1.7e9，PG int4
上限内）。存 Text 会让按时间排序变成字典序。`received_at` 用 Text ISO
（字典序=时序）。两列故意不同类型。不要学 events 用 BigInteger（那是毫秒）。

为什么不建 FK：与 events 同款。设备删了账本仍在，成本归属才讲得清；会话进行中
就 upsert，轨迹还没到。建 FK 会让进行中的会话插不进去。

为什么 side_* 可空：客户端「不落三个恒零字段」。服务端存 0 会让「无影子」与
「影子恰好为 0」不可分，而后者几乎不出现、前者是默认。

`used_usd` **不存表**。每次 GET 从 usage_ledger 现算。存一份会与 upsert 双写，
对不上时没有单一事实源。

账本本身没有审计表：upsert 是设备上报不是管理动作。管理台对 usage_ledger 只读。
预算变更走 budget_audit，对标 policy_audit。
"""

from sqlalchemy import Column, Double, ForeignKey, Index, Integer, Text, text

from app.core.db import Base


class UsageLedger(Base):
    """一条会话用量。按 (device_id, session_id) upsert，latest-wins 整行覆盖。

    `device_id` / `org_id` / `team_id` 一律来自 `DeviceContext`，**不从 body 取** ——
    body 里的同名字段是攻击面（契约 §1）。
    """

    __tablename__ = "usage_ledger"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(Text, nullable=False)  # 从凭据
    org_id = Column(Text, nullable=False)  # 从凭据。文本副本
    team_id = Column(Text, nullable=False, default="")  # 从凭据。可空语义用空串
    session_id = Column(Text, nullable=False)  # upsert 键的另一半
    ts = Column(Integer, nullable=False)  # 客户端秒 epoch，原样存
    received_at = Column(Text, nullable=False)  # 服务端 ISO。每次 upsert 刷新
    model = Column(Text, nullable=False)
    provider = Column(Text, nullable=False)
    prompt_total = Column(Integer, nullable=False, default=0)  # 主循环。不含影子
    cache_hit = Column(Integer, nullable=False, default=0)
    cache_write = Column(Integer, nullable=False, default=0)
    uncached_input = Column(Integer, nullable=False, default=0)
    output = Column(Integer, nullable=False, default=0)
    cost_usd = Column(Double, nullable=False)  # **含**影子。对账用
    savings_usd = Column(Double, nullable=False, default=0)
    duration_ms = Column(Integer, nullable=False, default=0)
    # 缺 = 无影子，存 NULL 不存 0（0 与「旧数据没有这个字段」在读侧不可区分）
    side_input_tokens = Column(Integer, nullable=True)
    side_output_tokens = Column(Integer, nullable=True)
    side_cost_usd = Column(Double, nullable=True)
    endpoint_host = Column(Text, nullable=True)
    app_version = Column(Text, nullable=True)
    peak_ratio = Column(Double, nullable=True)

    __table_args__ = (
        Index("uq_usage_ledger_device_session", "device_id", "session_id", unique=True),
        Index("idx_usage_ledger_org_id", "org_id"),
        Index("idx_usage_ledger_session_id", "session_id"),
        Index("idx_usage_ledger_received_at", "received_at"),
        Index("idx_usage_ledger_org_received", "org_id", "received_at"),
    )


class Budget(Base):
    """一条预算。同一 (scope_type, scope_id, org_id, period) 只允许一份生效。

    enforcement 默认 alert（告警放行），写死在 server_default，不要做成部署配置 ——
    能配 = 生产上可能被改成 block 而管理台还显示「默认告警」。
    """

    __tablename__ = "budgets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # device / team / org。对外 slug，不是内部整型 FK
    scope_type = Column(Text, nullable=False)
    scope_id = Column(Text, nullable=False)
    # 文本副本。device/team 行也填所属 org，求值收窄 + 列表筛选
    org_id = Column(Text, nullable=False)
    # session / daily / weekly / monthly
    period = Column(Text, nullable=False)
    limit_usd = Column(Double, nullable=False)
    # alert / block。没有 downgrade（T1 没接到 loop）
    enforcement = Column(Text, nullable=False, server_default="alert", default="alert")
    # 非空 = 不进下发（对标 policy.disabled_at）
    disabled_at = Column(Text, nullable=True)
    created_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)
    updated_by = Column(Text, nullable=False, default="")

    __table_args__ = (
        Index("idx_budgets_scope", "scope_type", "scope_id"),
        Index("idx_budgets_org_id", "org_id"),
        # 同层同周期只能一份生效。SQLite 3.8+ / PG 都支持部分索引。
        # 带 org_id：team_id 只在组织内唯一，两家公司都可以有生效的 monthly org 预算。
        Index(
            "uq_budgets_scope_period_enabled",
            "scope_type",
            "scope_id",
            "org_id",
            "period",
            unique=True,
            sqlite_where=text("disabled_at IS NULL"),
            postgresql_where=text("disabled_at IS NULL"),
        ),
    )


class BudgetAudit(Base):
    """预算变更审计。只追加，不更新，不删除。

    budget_id 走 SET NULL 而不是 CASCADE：真删一条预算时审计必须留下来。
    scope_type / scope_id 另存文本副本，budget_id 变 NULL 后仍能知道改的是哪一层。
    """

    __tablename__ = "budget_audit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    budget_id = Column(Integer, ForeignKey("budgets.id", ondelete="SET NULL"), nullable=True)
    scope_type = Column(Text, nullable=False)
    scope_id = Column(Text, nullable=False)
    action = Column(Text, nullable=False)  # create / update / delete / disable / enable
    old_json = Column(Text, nullable=True)
    new_json = Column(Text, nullable=True)
    reason = Column(Text, nullable=False)
    actor = Column(Text, nullable=False, default="")
    created_at = Column(Text, nullable=False)

    __table_args__ = (
        Index("idx_budget_audit_scope", "scope_type", "scope_id"),
        Index("idx_budget_audit_created_at", "created_at"),
        Index("idx_budget_audit_budget_id", "budget_id"),
    )

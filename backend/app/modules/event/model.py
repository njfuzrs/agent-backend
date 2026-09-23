"""event 模块 ORM：events + event_rejects。

列类型跟本仓惯例：时间用 Text 存 ISO，结构化字段用 Text 存 JSON。

为什么 `metadata_json` 存 Text 不用 JSONB：与 trajectory / identity / flag / policy
同一套列类型，PG 与 SQLite 行为一致（本地开发是 SQLite）。代价是**不能按 metadata
内部字段做索引查询** —— 而这正好与契约 §8「metadata 内部字段筛选是 BI，不做」一致。
哪天真要做，那是另一个里程碑的迁移，不是在这里加一列 JSONB。

为什么 `client_ts` 用 BigInteger 而 `received_at` 用 Text —— 这两列**故意不同类型**：
    client_ts 来自客户端 `Date.now()`，是毫秒数字（~1.7e12）。存 Text 会让排序变成
              字典序（"9999" > "10000"）；存 Integer 会撑爆 PG int4（上限 2.1e9）。
    received_at 是服务端 ISO 字符串，ISO 的字典序等于时序，无此问题。
两个都要留：磁盘重放会让一条 2 天前的事件今天才到。只存 client_ts 看不出「这批是
补传的」；只存 received_at 则重放批次会插到最新，事件顺序错乱（契约 §4）。

为什么不建 FK：
    devices —— 设备删了事件仍在，审计才讲得清（与 policy 同款理由）。
    trajectories —— **事件几乎总是先到**。事件在会话进行中每 15s flush，轨迹在会话
                    结束才上传。建 FK 会让绝大多数插入失败。`session_id` 是值 join，
                    不是外键。

不建审计表：flag / policy 有 `*_audit`，因为它们的写入是**管理动作**。events 本身
就是审计事实，对它再建审计表是套娃。**管理台对 events 只读**，没有写入动作可审计。
推论：events 表**没有** UPDATE / DELETE 路径，软删除与编辑一概不做。
"""

from sqlalchemy import BigInteger, Column, Index, Integer, Text

from app.core.db import Base


class Event(Base):
    """一条 analytics 事件。只追加，不更新，不删除。

    `device_id` / `org_id` / `team_id` 一律来自 `DeviceContext`，**不从 body 取** ——
    body 里的同名字段是攻击面（契约 §1）。
    """

    __tablename__ = "events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 幂等键：sha256(device_id\0event_name\0client_ts\0canonical_json(metadata))[:32]
    # 唯一索引 + ON CONFLICT DO NOTHING 就是全部去重机制（契约 §3）
    fingerprint = Column(Text, nullable=False)
    event_name = Column(Text, nullable=False)  # 白名单内，见 service/guard.py
    device_id = Column(Text, nullable=False)  # 从凭据
    org_id = Column(Text, nullable=False)  # 从凭据。文本副本，筛选/隔离用
    team_id = Column(Text, nullable=False, default="")  # 从凭据。可空语义用空串
    # join key。**可空** —— metadata 缺 `_ctx_session_id` 时仍入库：
    # 事件有 device 归属就有审计价值，只是 join 不上。缺失率在管理台可见。
    session_id = Column(Text, nullable=True)
    # 设计文档写 Integer，但毫秒 epoch（~1.7e12）超过 PG int4 上限 2.1e9，
    # 必须用 BigInteger。SQLite INTEGER 本身是 64 位，两边行为一致。
    client_ts = Column(BigInteger, nullable=False)  # 客户端毫秒 epoch，原样存
    received_at = Column(Text, nullable=False)  # 服务端 ISO
    metadata_json = Column(Text, nullable=False)  # 扁平 KV 的 JSON 文本

    __table_args__ = (
        Index("uq_events_fingerprint", "fingerprint", unique=True),
        Index("idx_events_session_id", "session_id"),
        Index("idx_events_device_id", "device_id"),
        Index("idx_events_org_id", "org_id"),
        Index("idx_events_name_received", "event_name", "received_at"),
    )


class EventReject(Base):
    """被拒事件名的计数。按名字一行，只 upsert 计数，**不存事件内容**。

    为什么要这张表：`rejected` 是每次请求的瞬时返回值，客户端拿到就丢了。而
    「客户端加了新事件名、服务端白名单没同步 → 新事件被静默丢」是本模块的已知
    风险（契约 §2）。如果它只能靠翻服务器日志发现，就等于没有出口 —— 主规划 §7
    「防线全在、调用全 0」的同款形态。有这张表，被拒的名字能在管理台首屏直接看见。

    为什么按名字聚合一行就够：被拒的事件名是一个**闭集且极小**的集合（拼错的名字
    就那几个）。存事件内容会让这张表变成「白名单外事件的影子 events 表」，那是另一
    个功能。
    """

    __tablename__ = "event_rejects"

    # 事件名做主键：upsert 的冲突目标。被拒的名字可能是任意字符串（客户端拼错），
    # 所以这里**不能**假设它在白名单里。
    event_name = Column(Text, primary_key=True)
    reason = Column(Text, nullable=False, default="")  # 最近一次被拒的原因分类
    count = Column(Integer, nullable=False, default=0)
    first_seen_at = Column(Text, nullable=False)
    last_seen_at = Column(Text, nullable=False)

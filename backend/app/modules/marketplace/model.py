"""插件市场 ORM：market_items / market_versions / market_audit。

- market_items：一个插件一行。name 全局唯一，就是 plugin.json 的 name，也是客户端
  `<name>@company` 里的那个 name。可见范围是 org（team_id 为空）或 org 内某个 team。
  org_id / team_id 存对外 slug 的文本副本，不建 FK、不 join identity 的表（跨模块查表会被门禁打红），
  理由同 policies。
- market_versions：一个版本一行。上传即 draft，发布后才进设备端 index；yanked 不再下发，
  已装的客户端照常用（那是客户端本地的文件）。manifest / components 存上传时的快照，
  制品只认 sha256：下发前再算一遍，对不上就拒绝。
- market_downloads：设备每拉一次制品一行，只追加。设备凭据鉴权，user_ref 从凭据取 —— 这是
  「谁装了什么」的强归因（下载 ≠ 装成功，但客户端校验失败会重下，次数只多不少，如实标注）。
- market_audit：只追加。item_id 走 SET NULL，name / version 另存文本副本 ——
  理由同 policy_audit：「谁在什么时候上架了什么」必须是不可变记录。
"""

from sqlalchemy import Column, ForeignKey, Index, Integer, Text, UniqueConstraint

from app.core.db import Base

# 版本状态。draft → published → yanked，单向。
STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
STATUS_YANKED = "yanked"


class MarketItem(Base):
    __tablename__ = "market_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False, unique=True)
    # plugin / skill / mcp。目录展示用的标签；分发的永远是一个插件包。
    kind = Column(Text, nullable=False)
    description = Column(Text, nullable=False, default="")
    maintainer = Column(Text, nullable=False, default="")
    # 可见范围：org_id 必填；team_id 空串 = 整个组织可见
    org_id = Column(Text, nullable=False)
    team_id = Column(Text, nullable=False, default="")
    created_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)
    created_by = Column(Text, nullable=False, default="")

    __table_args__ = (Index("idx_market_items_org_id", "org_id"),)


class MarketVersion(Base):
    __tablename__ = "market_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 版本不删，条目也就不删：RESTRICT
    item_id = Column(
        Integer, ForeignKey("market_items.id", ondelete="RESTRICT", name="fk_market_versions_item_id"), nullable=False
    )
    version = Column(Text, nullable=False)
    # 上传时 plugin.json 的原样快照（JSON 文本）
    manifest_json = Column(Text, nullable=False)
    # 组件清单（skills / commands / agents / hooks / mcpServers），客户端安装前展示
    components_json = Column(Text, nullable=False)
    sha256 = Column(Text, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    storage_key = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default=STATUS_DRAFT)
    created_at = Column(Text, nullable=False)
    created_by = Column(Text, nullable=False, default="")
    published_at = Column(Text, nullable=True)
    published_by = Column(Text, nullable=True)
    yanked_at = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("item_id", "version", name="uq_market_versions_item_version"),
        Index("idx_market_versions_status", "status"),
    )


class MarketAudit(Base):
    """市场变更审计。只追加，不更新，不删除。"""

    __tablename__ = "market_audit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(
        Integer, ForeignKey("market_items.id", ondelete="SET NULL", name="fk_market_audit_item_id"), nullable=True
    )
    item_name = Column(Text, nullable=False)
    version = Column(Text, nullable=False, default="")
    # create / update / upload / publish / yank
    action = Column(Text, nullable=False)
    # 发布 / 下架必填；上传与改元数据可空
    reason = Column(Text, nullable=False, default="")
    actor = Column(Text, nullable=False, default="")
    # 结构化补充（上传的 sha256 / 大小、元数据改了哪些字段）。JSON 文本
    detail_json = Column(Text, nullable=False, default="{}")
    created_at = Column(Text, nullable=False)
    request_id = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_market_audit_created_at", "created_at"),
        Index("idx_market_audit_item_id", "item_id"),
    )


class MarketDownload(Base):
    """一次制品下载。只追加。device / user 不建外键：人和设备删了记录仍要在。"""

    __tablename__ = "market_downloads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(Text, nullable=False)
    item_name = Column(Text, nullable=False)
    version = Column(Text, nullable=False)
    device_id = Column(Text, nullable=False)
    org_id = Column(Text, nullable=False)
    # 下载那一刻设备绑定的人。NULL = 未登录设备（注册码签发）
    user_ref = Column(Integer, nullable=True)
    request_id = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_market_downloads_created_at", "created_at"),
        Index("idx_market_downloads_item_name", "item_name"),
    )

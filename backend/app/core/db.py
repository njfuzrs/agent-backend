"""SQLAlchemy 引擎 + session，兼容 SQLite（WAL）和 PostgreSQL

schema 演进由 Alembic 接管（见 backend/migrations/）。本文件不再建表、不再加列。

历史说明：原 `init_db()` 里有 `Base.metadata.create_all` + `_migrate_sqlite_columns()`，
两者都已删除，原因见规划 §1.3 ①：

- `create_all` 只建新表，不改已有表的列；
- `_migrate_sqlite_columns()` 被 `if settings.is_sqlite` 挡住，**生产是 PG，所以生产环境
  的加列路径实际上是「不存在」的**，每次上线都要手动登机器执行 SQL。

保留两条路会让人以为「加列有两条路」，而其中一条在生产不生效 —— 这正是那个 bug 的成因。
现在只有一条路：`alembic upgrade head`。
"""

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)

# SQLite 专用：开启 WAL 模式（PostgreSQL 不需要）
if settings.is_sqlite:
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with async_session() as session:
        yield session

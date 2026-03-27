"""SQLAlchemy 引擎 + session，兼容 SQLite（WAL）和 PostgreSQL"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import event
from app.config import settings

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


async def init_db():
    from app.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # SQLite 不会自动给已有表加新列，需要手动 ALTER TABLE
        if settings.is_sqlite:
            await conn.run_sync(_migrate_sqlite_columns)


def _migrate_sqlite_columns(conn):
    """SQLite 增量迁移：给已有的 trajectories 表添加缺失的列"""
    cursor = conn.connection.cursor()
    cursor.execute("PRAGMA table_info(trajectories)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    # 新增列定义：(列名, SQL 类型, 默认值)
    new_columns = [
        ("oss_key", "TEXT", None),
        ("sha256", "TEXT", None),
        ("file_size", "INTEGER", None),
        ("user_id", "TEXT", None),
        ("device_id", "TEXT", None),
        ("deleted_at", "TEXT", None),
        # AI 评分相关列
        ("ai_score", "INTEGER", None),
        ("ai_quality_status", "TEXT", "'pending'"),
        ("ai_grade", "TEXT", "''"),
        ("rule_score", "INTEGER", None),
        ("rule_details", "TEXT", "'{}'"),
        ("rule_flags", "TEXT", "'[]'"),
        ("heuristic_score", "INTEGER", None),
        ("heuristic_details", "TEXT", "'{}'"),
        ("heuristic_patterns", "TEXT", "'[]'"),
        ("llm_score", "INTEGER", None),
        ("llm_details", "TEXT", "'{}'"),
        ("llm_reasoning", "TEXT", "''"),
        ("llm_suggested_task_type", "TEXT", "''"),
        ("llm_eval_model", "TEXT", "''"),
        ("scored_at", "TEXT", None),
        ("score_version", "INTEGER", "0"),
    ]

    for col_name, col_type, default in new_columns:
        if col_name not in existing_cols:
            default_clause = f" DEFAULT {default}" if default is not None else ""
            cursor.execute(f"ALTER TABLE trajectories ADD COLUMN {col_name} {col_type}{default_clause}")

    cursor.close()

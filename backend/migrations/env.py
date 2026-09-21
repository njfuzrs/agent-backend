"""Alembic 运行环境。

两条纪律：
1. DATABASE_URL 只从 app.core.config 读，不在 alembic.ini 里另写一份 —— 否则
   「服务连的库」和「迁移改的库」会漂移。
2. async 驱动（aiosqlite / asyncpg）下必须走 async_engine + run_sync，
   不能直接把 async URL 交给同步 engine。
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# 导入所有模块的 model，使 Base.metadata 完整
# 新增模块时必须在这里补一行 import，否则 autogenerate 会把它的表当成「待删除」
import app.modules.identity.model  # noqa: F401
import app.modules.trajectory.model  # noqa: F401
from app.core.config import settings
from app.core.db import Base  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _configure_opts(connection=None, url=None):
    """离线/在线共用的 configure 参数。

    render_as_batch: SQLite 不支持大部分 ALTER TABLE，需要 batch 模式
    （建临时表 → 拷数据 → 换名）。PG 下该选项无副作用。
    """
    return dict(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=settings.is_sqlite,
    )


def run_migrations_offline() -> None:
    """离线模式：只输出 SQL，不连库。"""
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    opts = _configure_opts(url=settings.DATABASE_URL)
    opts.pop("connection")
    context.configure(literal_binds=True, dialect_opts={"paramstyle": "named"}, **opts)
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    opts = _configure_opts(connection=connection)
    opts.pop("url")
    context.configure(**opts)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    # URL 每次运行时从 settings 读，避免 env.py 被 import 一次后钉死旧库。
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

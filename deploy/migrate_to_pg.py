#!/usr/bin/env python3
"""SQLite → PostgreSQL 一次性数据迁移脚本

用法（在服务器上执行）：
    pip3 install psycopg2-binary
    python3 migrate_to_pg.py \
        --sqlite /opt/trajectory-platform/data/trajectories.db \
        --pg "postgresql://trajuser:traj_pg_password@localhost/trajdb"
"""

import argparse
import sqlite3
import sys

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("请先安装 psycopg2: pip3 install psycopg2-binary")
    sys.exit(1)


# trajectories 表的列（与 models.py 对应）
TRAJ_COLUMNS = [
    "id", "session_id", "tool_source", "model",
    "start_time", "end_time", "duration_ms",
    "tokens_sent", "tokens_received", "cache_read_tokens", "cache_creation_tokens",
    "total_tokens", "total_cost_usd",
    "total_steps", "total_api_calls", "exit_status", "tools_used", "files_edited",
    "working_directory",
    "task_type", "project_name", "tags",
    "quality_rating", "quality_status", "quality_notes",
    "has_thinking", "has_sub_agent", "first_prompt",
    "traj_file_path", "traj_file_size",
    "uploaded_at", "updated_at",
]

# 新增列（SQLite 中没有，PG 中需要设默认值）
NEW_COLUMNS = ["oss_key", "sha256", "file_size", "user_id", "device_id", "deleted_at"]

COMPARE_GROUP_COLUMNS = ["id", "name", "description", "task_prompt", "created_at"]
COMPARE_ITEM_COLUMNS = ["id", "group_id", "trajectory_id", "notes"]


def create_pg_tables(pg_conn):
    """在 PG 中创建表结构"""
    cur = pg_conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trajectories (
            id SERIAL PRIMARY KEY,
            session_id TEXT NOT NULL UNIQUE,
            tool_source TEXT NOT NULL DEFAULT 'claude-code',
            model TEXT NOT NULL DEFAULT '',
            start_time TEXT,
            end_time TEXT,
            duration_ms INTEGER,
            tokens_sent INTEGER DEFAULT 0,
            tokens_received INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_creation_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            total_cost_usd REAL DEFAULT 0.0,
            total_steps INTEGER DEFAULT 0,
            total_api_calls INTEGER DEFAULT 0,
            exit_status TEXT DEFAULT '',
            tools_used TEXT DEFAULT '[]',
            files_edited TEXT DEFAULT '[]',
            working_directory TEXT DEFAULT '',
            task_type TEXT DEFAULT '',
            project_name TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            quality_rating INTEGER,
            quality_status TEXT DEFAULT 'unreviewed',
            quality_notes TEXT DEFAULT '',
            has_thinking BOOLEAN DEFAULT FALSE,
            has_sub_agent BOOLEAN DEFAULT FALSE,
            first_prompt TEXT DEFAULT '',
            traj_file_path TEXT NOT NULL,
            traj_file_size INTEGER DEFAULT 0,
            oss_key TEXT,
            sha256 TEXT,
            file_size INTEGER,
            user_id TEXT,
            device_id TEXT,
            deleted_at TEXT,
            uploaded_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)

    # 创建索引
    indexes = [
        ("idx_traj_session_id", "session_id"),
        ("idx_traj_tool_source", "tool_source"),
        ("idx_traj_model", "model"),
        ("idx_traj_start_time", "start_time"),
        ("idx_traj_task_type", "task_type"),
        ("idx_traj_quality_status", "quality_status"),
        ("idx_traj_exit_status", "exit_status"),
        ("idx_traj_project_name", "project_name"),
        ("idx_traj_user_id", "user_id"),
        ("idx_traj_device_id", "device_id"),
        ("idx_traj_deleted_at", "deleted_at"),
    ]
    for idx_name, col in indexes:
        cur.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON trajectories ({col});")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS compare_groups (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            task_prompt TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS compare_group_items (
            id SERIAL PRIMARY KEY,
            group_id INTEGER NOT NULL REFERENCES compare_groups(id) ON DELETE CASCADE,
            trajectory_id INTEGER NOT NULL REFERENCES trajectories(id) ON DELETE CASCADE,
            notes TEXT DEFAULT '',
            UNIQUE(group_id, trajectory_id)
        );
    """)

    pg_conn.commit()
    print("PG 表结构创建完成")


def migrate_trajectories(src: sqlite3.Connection, dst):
    """迁移 trajectories 表"""
    cur_src = src.cursor()
    cur_dst = dst.cursor()

    # 读取 SQLite 中的列名（可能不包含新增列）
    cur_src.execute("PRAGMA table_info(trajectories)")
    sqlite_cols = [row[1] for row in cur_src.fetchall()]

    # 只迁移 SQLite 中存在的列
    cols_to_migrate = [c for c in TRAJ_COLUMNS if c in sqlite_cols]

    cur_src.execute(f"SELECT {', '.join(cols_to_migrate)} FROM trajectories")
    rows = cur_src.fetchall()

    if not rows:
        print("trajectories 表为空，跳过")
        return 0

    # 构建 INSERT 语句（包含新增列的默认值）
    all_cols = cols_to_migrate + NEW_COLUMNS
    placeholders = ["%s"] * len(cols_to_migrate) + ["NULL"] * len(NEW_COLUMNS)

    insert_sql = f"""
        INSERT INTO trajectories ({', '.join(all_cols)})
        VALUES ({', '.join(placeholders)})
        ON CONFLICT (session_id) DO NOTHING
    """

    # boolean 列在 SQLite 中存为 0/1，PG 需要转为 True/False
    bool_cols = {"has_thinking", "has_sub_agent"}
    bool_indexes = [i for i, c in enumerate(cols_to_migrate) if c in bool_cols]

    count = 0
    for row in rows:
        try:
            # 转换 boolean 列
            row_list = list(row)
            for idx in bool_indexes:
                row_list[idx] = bool(row_list[idx]) if row_list[idx] is not None else False
            cur_dst.execute(insert_sql, tuple(row_list))
            dst.commit()
            count += 1
        except Exception as e:
            dst.rollback()
            print(f"  跳过记录: {e}")

    # 重置序列（让 PG 的 id 自增从最大值继续）
    cur_dst.execute("SELECT MAX(id) FROM trajectories")
    max_id = cur_dst.fetchone()[0]
    if max_id:
        cur_dst.execute(f"SELECT setval('trajectories_id_seq', {max_id})")
    dst.commit()

    print(f"迁移 trajectories: {count}/{len(rows)} 条")
    return count


def migrate_compare_groups(src: sqlite3.Connection, dst):
    """迁移 compare_groups 和 compare_group_items 表"""
    cur_src = src.cursor()
    cur_dst = dst.cursor()

    # compare_groups
    try:
        cur_src.execute(f"SELECT {', '.join(COMPARE_GROUP_COLUMNS)} FROM compare_groups")
        groups = cur_src.fetchall()
    except sqlite3.OperationalError:
        print("compare_groups 表不存在，跳过")
        return

    for row in groups:
        try:
            cur_dst.execute(
                f"INSERT INTO compare_groups ({', '.join(COMPARE_GROUP_COLUMNS)}) "
                f"VALUES ({', '.join(['%s'] * len(COMPARE_GROUP_COLUMNS))}) "
                f"ON CONFLICT DO NOTHING",
                row,
            )
        except Exception as e:
            print(f"  跳过 compare_group: {e}")

    # compare_group_items
    try:
        cur_src.execute(f"SELECT {', '.join(COMPARE_ITEM_COLUMNS)} FROM compare_group_items")
        items = cur_src.fetchall()
    except sqlite3.OperationalError:
        print("compare_group_items 表不存在，跳过")
        dst.commit()
        return

    for row in items:
        try:
            cur_dst.execute(
                f"INSERT INTO compare_group_items ({', '.join(COMPARE_ITEM_COLUMNS)}) "
                f"VALUES ({', '.join(['%s'] * len(COMPARE_ITEM_COLUMNS))}) "
                f"ON CONFLICT DO NOTHING",
                row,
            )
        except Exception as e:
            print(f"  跳过 compare_group_item: {e}")

    # 重置序列
    for table in ["compare_groups", "compare_group_items"]:
        cur_dst.execute(f"SELECT MAX(id) FROM {table}")
        max_id = cur_dst.fetchone()[0]
        if max_id:
            cur_dst.execute(f"SELECT setval('{table}_id_seq', {max_id})")

    dst.commit()
    print(f"迁移 compare_groups: {len(groups)} 条, items: {len(items)} 条")


def main():
    parser = argparse.ArgumentParser(description="SQLite → PostgreSQL 数据迁移")
    parser.add_argument("--sqlite", required=True, help="SQLite 数据库文件路径")
    parser.add_argument("--pg", required=True, help="PostgreSQL 连接字符串")
    parser.add_argument("--dry-run", action="store_true", help="只检查不执行")
    args = parser.parse_args()

    # 连接 SQLite
    print(f"连接 SQLite: {args.sqlite}")
    src = sqlite3.connect(args.sqlite)
    src.row_factory = sqlite3.Row

    # 统计源数据
    cur = src.cursor()
    cur.execute("SELECT COUNT(*) FROM trajectories")
    src_count = cur.fetchone()[0]
    print(f"SQLite 中有 {src_count} 条轨迹记录")

    if args.dry_run:
        print("dry-run 模式，不执行迁移")
        return

    # 连接 PostgreSQL
    print(f"连接 PostgreSQL...")
    dst = psycopg2.connect(args.pg)

    # 创建表
    create_pg_tables(dst)

    # 迁移数据
    traj_count = migrate_trajectories(src, dst)
    migrate_compare_groups(src, dst)

    # 验证
    cur_dst = dst.cursor()
    cur_dst.execute("SELECT COUNT(*) FROM trajectories")
    dst_count = cur_dst.fetchone()[0]
    print(f"\n=== 迁移完成 ===")
    print(f"SQLite: {src_count} 条")
    print(f"PostgreSQL: {dst_count} 条")
    if src_count == dst_count:
        print("数量一致 ✓")
    else:
        print(f"差异: {src_count - dst_count} 条（可能是重复记录被跳过）")

    src.close()
    dst.close()


if __name__ == "__main__":
    main()

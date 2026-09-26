"""bridge 三张表：sessions / session_tokens / audit

Revision ID: 0013
Revises: 0012

M6 遥控的接缝。REST 在主进程（--workers 2）签发，WS 在独立 sidecar
（--workers 1）握手。两边不共享内存，只共享这三张表：

- bridge_sessions 是配对的事实（谁、哪个组织、什么状态）。
- bridge_session_tokens 只存 token 的 sha256。明文只在签发响应里出现一次。
- bridge_audit 只记管理动作（签发 / 强制断开 / 鉴权失败），不记帧正文。

不建 FK 到 devices：设备删了，审计仍要能回答「这台机器被遥控过」。
与 policy / events 同款理由。

生产不跑 downgrade（退代码不退 DDL）。三张新表对旧代码不可见，回滚时
空置无害。downgrade 留着只为迁移链可逆、本地能测。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bridge_sessions",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("team_id", sa.Text(), nullable=False),
        sa.Column("ver", sa.Text(), nullable=True),
        sa.Column("cwd_basename", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("disconnect_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_bridge_sessions_device", "bridge_sessions", ["device_id"])
    op.create_index(
        "idx_bridge_sessions_org_state", "bridge_sessions", ["org_id", "state"]
    )

    op.create_table(
        "bridge_session_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # 每个角色一张有效 token。重签先把旧行的 revoked_at 填上，再插新行。
    op.create_index(
        "uq_bridge_token_session_role",
        "bridge_session_tokens",
        ["session_id", "role"],
        unique=True,
    )
    op.create_index(
        "uq_bridge_token_hash", "bridge_session_tokens", ["token_hash"], unique=True
    )

    op.create_table(
        "bridge_audit",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        # 跳回同一次请求的日志用。可空：握手失败可能发生在没有请求编号的路径上。
        # 不建索引——按时间查，这个值是跳转用的，不是检索用的（方案 §3.9）。
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_bridge_audit_session", "bridge_audit", ["session_id"])
    op.create_index("idx_bridge_audit_created_at", "bridge_audit", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_bridge_audit_created_at", table_name="bridge_audit")
    op.drop_index("idx_bridge_audit_session", table_name="bridge_audit")
    op.drop_table("bridge_audit")
    op.drop_index("uq_bridge_token_hash", table_name="bridge_session_tokens")
    op.drop_index("uq_bridge_token_session_role", table_name="bridge_session_tokens")
    op.drop_table("bridge_session_tokens")
    op.drop_index("idx_bridge_sessions_org_state", table_name="bridge_sessions")
    op.drop_index("idx_bridge_sessions_device", table_name="bridge_sessions")
    op.drop_table("bridge_sessions")

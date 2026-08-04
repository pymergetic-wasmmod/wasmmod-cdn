"""Shell sessions + hit events for browser REPL.

Revision ID: 20260804_0002
Revises: 20260804_0001
Create Date: 2026-08-04

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260804_0002"
down_revision: Union[str, None] = "20260804_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shell_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("anon_id", sa.Uuid(), nullable=True),
        sa.Column("cdn_base", sa.String(length=512), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("driver", sa.String(length=64), nullable=False),
        sa.Column("hook_on", sa.Boolean(), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shell_sessions_user_id", "shell_sessions", ["user_id"])
    op.create_index("ix_shell_sessions_anon_id", "shell_sessions", ["anon_id"])
    op.create_index(
        "ix_shell_sessions_last_activity_at", "shell_sessions", ["last_activity_at"]
    )

    op.create_table(
        "shell_session_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("package", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["shell_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shell_session_events_session_id", "shell_session_events", ["session_id"])
    op.create_index("ix_shell_session_events_kind", "shell_session_events", ["kind"])
    op.create_index(
        "ix_shell_session_events_created_at", "shell_session_events", ["created_at"]
    )


def downgrade() -> None:
    op.drop_table("shell_session_events")
    op.drop_table("shell_sessions")

"""Fix missing columns from migration 0044.

Revision ID: 0046_fix_missing_columns
Revises: 0045_fix_agent_sessions_project_id_nullable
Create Date: 2026-01-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0046_fix_missing_columns"
down_revision = "0045_fix_agent_sessions_project_id_nullable"
branch_labels = None
depends_on = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    conn = op.get_bind()
    inspector = inspect(conn)
    columns = [c["name"] for c in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    # Add missing columns to agent_sessions if they don't exist
    if not column_exists("agent_sessions", "initial_prompt"):
        op.add_column("agent_sessions", sa.Column("initial_prompt", sa.Text(), nullable=True))

    if not column_exists("agent_sessions", "workspace_path"):
        op.add_column("agent_sessions", sa.Column("workspace_path", sa.String(), nullable=True))

    # Add missing columns to comments if they don't exist
    if not column_exists("comments", "agent_session_id"):
        op.add_column("comments", sa.Column("agent_session_id", sa.String(), nullable=True))

    if not column_exists("comments", "origin"):
        op.add_column("comments", sa.Column("origin", sa.String(), nullable=True))

    # Add missing column to agents if it doesn't exist
    if not column_exists("agents", "session_file_config_id"):
        op.add_column("agents", sa.Column("session_file_config_id", sa.String(), nullable=True))


def downgrade() -> None:
    # Don't drop columns in downgrade - they may have been added by 0044
    pass

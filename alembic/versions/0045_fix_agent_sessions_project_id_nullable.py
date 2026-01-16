"""Fix project_id to be nullable on agent_sessions for standalone sessions.

Revision ID: 0045_fix_agent_sessions_project_id_nullable
Revises: 0044_chat_channels_session_files
Create Date: 2026-01-15
"""

from alembic import op
import sqlalchemy as sa

revision = "0045_fix_agent_sessions_project_id_nullable"
down_revision = "0044_chat_channels_session_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite doesn't support ALTER COLUMN, so we use batch_alter_table
    with op.batch_alter_table("agent_sessions") as batch_op:
        batch_op.alter_column("project_id", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("agent_sessions") as batch_op:
        batch_op.alter_column("project_id", existing_type=sa.String(), nullable=False)

"""Add queued user messages to agent sessions.

Revision ID: 0024_session_queued_user_messages
Revises: 0023_session_last_user_message
Create Date: 2025-12-30 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0024_session_queued_user_messages"
down_revision = "0023_session_last_user_message"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column("queued_user_messages", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "queued_user_messages")

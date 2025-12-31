"""Add awaiting response offset to agent sessions.

Revision ID: 0025_session_awaiting_response_offset
Revises: 0024_session_queued_user_messages
Create Date: 2025-12-30 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0025_session_awaiting_response_offset"
down_revision = "0024_session_queued_user_messages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column("awaiting_response_offset", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "awaiting_response_offset")

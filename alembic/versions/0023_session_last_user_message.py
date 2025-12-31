"""Add last user message to agent sessions.

Revision ID: 0023_session_last_user_message
Revises: 0022_session_awaiting_response
Create Date: 2025-12-30 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0023_session_last_user_message"
down_revision = "0022_session_awaiting_response"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_sessions", sa.Column("last_user_message", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_sessions", "last_user_message")

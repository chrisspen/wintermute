"""Add awaiting response to agent sessions.

Revision ID: 0022_session_awaiting_response
Revises: 0021_ticket_github_comment_cache
Create Date: 2025-12-30 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0022_session_awaiting_response"
down_revision = "0021_ticket_github_comment_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column("awaiting_response", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "awaiting_response")

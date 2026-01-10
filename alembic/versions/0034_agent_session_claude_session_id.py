"""Add claude_session_id to agent_sessions for Claude CLI session persistence.

Revision ID: 0034_agent_session_claude_session_id
Revises: 0033_issue_sources_unify
Create Date: 2026-01-10
"""

from alembic import op
import sqlalchemy as sa

revision = "0034_agent_session_claude_session_id"
down_revision = "0033_issue_sources_unify"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column("claude_session_id", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "claude_session_id")

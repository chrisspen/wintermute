"""Add poll_interval_seconds to issue_sources.

Revision ID: 0036_issue_source_poll_interval
Revises: 0035_remote_tokens_unify
Create Date: 2025-02-16
"""

from alembic import op
import sqlalchemy as sa

revision = "0036_issue_source_poll_interval"
down_revision = "0035_remote_tokens_unify"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "issue_sources",
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=False, server_default="60"),
    )


def downgrade() -> None:
    op.drop_column("issue_sources", "poll_interval_seconds")

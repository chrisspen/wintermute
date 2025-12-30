"""Add GitHub comment cache to tickets.

Revision ID: 0021_ticket_github_comment_cache
Revises: 0020_agent_response_prefix
Create Date: 2025-12-30
"""

from alembic import op
import sqlalchemy as sa


revision = "0021_ticket_github_comment_cache"
down_revision = "0020_agent_response_prefix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("github_comments_json", sa.Text(), nullable=True))
    op.add_column("tickets", sa.Column("github_comments_fetched_at", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "github_comments_fetched_at")
    op.drop_column("tickets", "github_comments_json")

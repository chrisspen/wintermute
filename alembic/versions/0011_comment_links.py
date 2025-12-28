"""Add linkage fields to comments.

Revision ID: 0011_comment_links
Revises: 0010_comments
Create Date: 2025-12-28 04:40:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0011_comment_links"
down_revision = "0010_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("comments", sa.Column("project_id", sa.String(), nullable=True))
    op.add_column("comments", sa.Column("agent_id", sa.String(), nullable=True))
    op.add_column("comments", sa.Column("source_id", sa.String(), nullable=True))
    op.add_column("comments", sa.Column("issue_number", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("comments", "issue_number")
    op.drop_column("comments", "source_id")
    op.drop_column("comments", "agent_id")
    op.drop_column("comments", "project_id")

"""Add source_url to tickets.

Revision ID: 0012_ticket_source_url
Revises: 0011_comment_links
Create Date: 2025-12-28 06:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0012_ticket_source_url"
down_revision = "0011_comment_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("source_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "source_url")

"""Add created_by_id to tickets for tracking ticket creator.

Revision ID: 0042_ticket_created_by
Revises: 0041_remote_token_base_url
Create Date: 2026-01-12
"""

from alembic import op
import sqlalchemy as sa

revision = "0042_ticket_created_by"
down_revision = "0041_remote_token_base_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("created_by_id", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tickets", "created_by_id")

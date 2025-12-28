"""Add internal notes to tickets.

Revision ID: 0009_ticket_internal_notes
Revises: 0008_work_item_traceback
Create Date: 2025-12-28 04:26:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0009_ticket_internal_notes"
down_revision = "0008_work_item_traceback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("internal_notes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "internal_notes")

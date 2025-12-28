"""Add work item traceback storage.

Revision ID: 0008_work_item_traceback
Revises: 0007_api_tokens
Create Date: 2025-02-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_work_item_traceback"
down_revision = "0007_api_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("work_items", sa.Column("last_traceback", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("work_items", "last_traceback")

"""Make comments.ticket_id nullable for standalone sessions.

Revision ID: 0047_comments_ticket_id_nullable
Revises: 0046_fix_missing_columns
Create Date: 2026-01-15
"""

from alembic import op
import sqlalchemy as sa

revision = "0047_comments_ticket_id_nullable"
down_revision = "0046_fix_missing_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite doesn't support ALTER COLUMN, so use batch mode
    with op.batch_alter_table("comments") as batch_op:
        batch_op.alter_column(
            "ticket_id",
            existing_type=sa.String(),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("comments") as batch_op:
        batch_op.alter_column(
            "ticket_id",
            existing_type=sa.String(),
            nullable=False,
        )

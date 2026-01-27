"""Add ticket_history table

Revision ID: 0059
Revises: 0058
Create Date: 2026-01-27

"""
from alembic import op
import sqlalchemy as sa

revision = "0059_ticket_history"
down_revision = "0058_project_symbol_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticket_history",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("ticket_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("field_name", sa.String(), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ticket_history_ticket_id", "ticket_history", ["ticket_id"])


def downgrade() -> None:
    op.drop_index("ix_ticket_history_ticket_id", table_name="ticket_history")
    op.drop_table("ticket_history")

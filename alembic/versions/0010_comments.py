"""Add comments table.

Revision ID: 0010_comments
Revises: 0009_ticket_internal_notes
Create Date: 2025-12-28 04:32:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0010_comments"
down_revision = "0009_ticket_internal_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "comments",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("ticket_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("public", sa.Integer(), nullable=False),
        sa.Column("approved", sa.Integer(), nullable=False),
        sa.Column("sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("comments")

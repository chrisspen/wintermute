"""Add ticket_sprints junction table for many-to-many relationship.

Revision ID: 0040_ticket_sprints_junction
Revises: 0039_sprints_ticket_fields
Create Date: 2026-01-11
"""

from alembic import op
import sqlalchemy as sa


revision = "0040_ticket_sprints_junction"
down_revision = "0039_sprints_ticket_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticket_sprints",
        sa.Column("ticket_id", sa.String(), nullable=False),
        sa.Column("sprint_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("ticket_id", "sprint_id"),
    )
    # Migrate existing sprint_id data to the junction table
    op.execute("""
        INSERT INTO ticket_sprints (ticket_id, sprint_id, created_at)
        SELECT id, sprint_id, datetime('now')
        FROM tickets
        WHERE sprint_id IS NOT NULL AND sprint_id != ''
    """)


def downgrade() -> None:
    op.drop_table("ticket_sprints")

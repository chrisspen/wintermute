"""Backfill project symbols and ticket counts.

Revision ID: 0056_backfill_project_symbol_ticket_count
Revises: 0055_project_symbol_ticket_count
Create Date: 2026-01-27
"""
from alembic import op # pylint: disable=no-name-in-module

revision = "0056_backfill_project_symbol_ticket_count"
down_revision = "0055_project_symbol_ticket_count"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ensure all projects have symbols (uppercase slug)
    op.execute("UPDATE projects SET symbol = UPPER(slug) WHERE symbol IS NULL OR symbol = ''")

    # Reset and recalculate counts for ALL tickets by project and creation order
    # First, clear any existing counts
    op.execute("UPDATE tickets SET count = NULL")

    # Then assign counts based on creation order within each project
    # SQLite uses a different approach - we need a correlated subquery
    op.execute(
        """
        UPDATE tickets SET count = (
            SELECT COUNT(*)
            FROM tickets AS t2
            WHERE t2.project_id = tickets.project_id
            AND (t2.created_at < tickets.created_at
                 OR (t2.created_at = tickets.created_at AND t2.id <= tickets.id))
        )
        """
    )


def downgrade() -> None:
    # No downgrade needed - this is a data fix
    pass

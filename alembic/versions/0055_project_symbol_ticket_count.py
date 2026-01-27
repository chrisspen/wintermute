"""Add symbol field to projects and count field to tickets.

Revision ID: 0055_project_symbol_ticket_count
Revises: 0054_ticket_vm_target_id
Create Date: 2026-01-27
"""
from alembic import op # pylint: disable=no-name-in-module
import sqlalchemy as sa

revision = "0055_project_symbol_ticket_count"
down_revision = "0054_ticket_vm_target_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add symbol field to projects (defaults to uppercase slug)
    op.add_column("projects", sa.Column("symbol", sa.String(), nullable=True))

    # Add count field to tickets (sequence number within project)
    op.add_column("tickets", sa.Column("count", sa.Integer(), nullable=True))

    # Populate symbol from uppercase slug for existing projects
    op.execute("UPDATE projects SET symbol = UPPER(slug) WHERE symbol IS NULL")

    # Populate count for existing tickets based on creation order within each project
    op.execute(
        """
        UPDATE tickets SET count = (
            SELECT COUNT(*) FROM tickets t2
            WHERE t2.project_id = tickets.project_id
            AND t2.created_at <= tickets.created_at
            AND t2.id <= tickets.id
        )
    """
    )


def downgrade() -> None:
    op.drop_column("tickets", "count")
    op.drop_column("projects", "symbol")

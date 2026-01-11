"""Add sprints table and new ticket fields.

This migration:
1. Creates sprints table with id, name, start_date, end_date, enabled, status
2. Adds sprint_id, hours, story_points, priority to tickets table

Revision ID: 0039_sprints_ticket_fields
Revises: 0038_agent_vm_project_repo
Create Date: 2026-01-11
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "0039_sprints_ticket_fields"
down_revision = "0038_agent_vm_project_repo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create sprints table
    op.create_table(
        "sprints",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("start_date", sa.String(), nullable=False),
        sa.Column("end_date", sa.String(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False, default=1),
        sa.Column("status", sa.String(), nullable=False, default="active"),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )

    # Add new columns to tickets table
    op.add_column("tickets", sa.Column("sprint_id", sa.String(), nullable=True))
    op.add_column("tickets", sa.Column("hours", sa.String(), nullable=True))
    op.add_column("tickets", sa.Column("story_points", sa.String(), nullable=True))
    op.add_column("tickets", sa.Column("priority", sa.String(), nullable=True))


def downgrade() -> None:
    # Remove columns from tickets
    op.drop_column("tickets", "priority")
    op.drop_column("tickets", "story_points")
    op.drop_column("tickets", "hours")
    op.drop_column("tickets", "sprint_id")

    # Drop sprints table
    op.drop_table("sprints")

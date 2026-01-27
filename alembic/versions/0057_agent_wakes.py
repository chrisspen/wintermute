"""Add agent_wakes table for scheduled agent wake-ups.

Revision ID: 0057_agent_wakes
Revises: 0056_backfill_project_symbol_ticket_count
Create Date: 2026-01-27
"""
from alembic import op # pylint: disable=no-name-in-module
import sqlalchemy as sa

revision = "0057_agent_wakes"
down_revision = "0056_backfill_project_symbol_ticket_count"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_wakes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("agent_session_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("wake_at", sa.String(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False), # pending, fired, cancelled
        sa.Column("fired_at", sa.String(), nullable=True),
        sa.Column("cancelled_at", sa.String(), nullable=True),
        sa.Column("cancelled_by", sa.String(), nullable=True), # user, agent, system
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_index("ix_agent_wakes_agent_session_id", "agent_wakes", ["agent_session_id"])
    op.create_index("ix_agent_wakes_status_wake_at", "agent_wakes", ["status", "wake_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_wakes_status_wake_at", table_name="agent_wakes")
    op.drop_index("ix_agent_wakes_agent_session_id", table_name="agent_wakes")
    op.drop_table("agent_wakes")

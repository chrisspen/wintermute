"""Add working_directory and session_directory fields to agents table.

Revision ID: 0052_agent_working_directory
Revises: 0051_agent_initial_prompt
Create Date: 2026-01-23
"""
from alembic import op  # pylint: disable=no-name-in-module
import sqlalchemy as sa

revision = "0052_agent_working_directory"
down_revision = "0051_agent_initial_prompt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("working_directory", sa.String(), nullable=True))
    op.add_column("agents", sa.Column("session_directory", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "session_directory")
    op.drop_column("agents", "working_directory")

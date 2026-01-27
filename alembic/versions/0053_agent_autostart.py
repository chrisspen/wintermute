"""Add autostart field to agents table.

Revision ID: 0053_agent_autostart
Revises: 0052_agent_working_directory
Create Date: 2026-01-23
"""
from alembic import op  # pylint: disable=no-name-in-module
import sqlalchemy as sa

revision = "0053_agent_autostart"
down_revision = "0052_agent_working_directory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("autostart", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("agents", "autostart")

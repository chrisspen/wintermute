"""Add vm_target_id field to tickets table.

Revision ID: 0054_ticket_vm_target_id
Revises: 0053_agent_autostart
Create Date: 2026-01-25
"""
from alembic import op # pylint: disable=no-name-in-module
import sqlalchemy as sa

revision = "0054_ticket_vm_target_id"
down_revision = "0053_agent_autostart"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("vm_target_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "vm_target_id")

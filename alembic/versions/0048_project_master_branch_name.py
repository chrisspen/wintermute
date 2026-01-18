"""Add master_branch_name to projects table.

Revision ID: 0048_project_master_branch_name
Revises: 0047_comments_ticket_id_nullable
Create Date: 2026-01-18
"""
# pylint: disable=no-name-in-module
from alembic import op
import sqlalchemy as sa

revision = "0048_project_master_branch_name"
down_revision = "0047_comments_ticket_id_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(sa.Column("master_branch_name", sa.String(), nullable=False, server_default="master"))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("master_branch_name")

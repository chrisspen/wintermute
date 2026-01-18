"""Add build_status_image_url to projects table.

Revision ID: 0049_project_build_status_image_url
Revises: 0048_project_master_branch_name
Create Date: 2026-01-18
"""
# pylint: disable=no-name-in-module
from alembic import op
import sqlalchemy as sa

revision = "0049_project_build_status_image_url"
down_revision = "0048_project_master_branch_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(sa.Column("build_status_image_url", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("build_status_image_url")

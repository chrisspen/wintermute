"""Add unique constraint to project symbol

Revision ID: 0058
Revises: 0057
Create Date: 2026-01-27

"""
from alembic import op

revision = "0058_project_symbol_unique"
down_revision = "0057_agent_wakes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_projects_symbol", "projects", ["symbol"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_projects_symbol", table_name="projects")

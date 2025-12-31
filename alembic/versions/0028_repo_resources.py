"""Add repo resources and project limits.

Revision ID: 0028_repo_resources
Revises: 0027_project_prompt_template
Create Date: 2025-12-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0028_repo_resources"
down_revision = "0027_project_prompt_template"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("max_repo_resources", sa.Integer(), nullable=False, server_default="3"),
    )
    op.create_table(
        "repo_resources",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("project_vm_id", sa.String(), nullable=False),
        sa.Column("repo_mode", sa.String(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("agent_id", sa.String(), nullable=True),
        sa.Column("last_used_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("repo_resources")
    op.drop_column("projects", "max_repo_resources")

"""Add prompt_template to projects.

Revision ID: 0027_project_prompt_template
Revises: 0026_agent_session_mode_mcp
Create Date: 2025-12-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0027_project_prompt_template"
down_revision = "0026_agent_session_mode_mcp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("prompt_template", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "prompt_template")

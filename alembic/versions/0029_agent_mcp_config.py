"""Add MCP config overrides to agents.

Revision ID: 0029_agent_mcp_config
Revises: 0028_repo_resources
Create Date: 2025-12-31
"""

from alembic import op
import sqlalchemy as sa

revision = "0029_agent_mcp_config"
down_revision = "0028_repo_resources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("mcp_config", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "mcp_config")

"""Add trust level to agents.

Revision ID: 0030_agent_trust_level
Revises: 0029_agent_mcp_config
Create Date: 2025-12-31
"""

from alembic import op
import sqlalchemy as sa

revision = "0030_agent_trust_level"
down_revision = "0029_agent_mcp_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("trust_level", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "trust_level")

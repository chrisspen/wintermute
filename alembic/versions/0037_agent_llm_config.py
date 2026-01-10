"""Add LLM API configuration fields to agents.

Revision ID: 0037_agent_llm_config
Revises: 0036_issue_source_poll_interval
Create Date: 2025-02-16
"""

from alembic import op
import sqlalchemy as sa

revision = "0037_agent_llm_config"
down_revision = "0036_issue_source_poll_interval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("llm_base_url", sa.String(), nullable=True))
    op.add_column("agents", sa.Column("llm_api_key", sa.String(), nullable=True))
    op.add_column("agents", sa.Column("llm_model", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "llm_model")
    op.drop_column("agents", "llm_api_key")
    op.drop_column("agents", "llm_base_url")

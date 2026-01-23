"""Add initial_prompt field to agents table.

Revision ID: 0051_agent_initial_prompt
Revises: 0050_agent_metrics_memory
Create Date: 2026-01-20
"""
from alembic import op # pylint: disable=no-name-in-module
import sqlalchemy as sa

revision = "0051_agent_initial_prompt"
down_revision = "0050_agent_metrics_memory"
branch_labels = None
depends_on = None

DEFAULT_INITIAL_PROMPT = "Read your AGENTS.md file and then wait for further instructions."


def upgrade() -> None:
    op.add_column("agents", sa.Column("initial_prompt", sa.Text(), nullable=True))
    # Set default value for existing rows
    op.execute(f"UPDATE agents SET initial_prompt = '{DEFAULT_INITIAL_PROMPT}' WHERE initial_prompt IS NULL")


def downgrade() -> None:
    op.drop_column("agents", "initial_prompt")

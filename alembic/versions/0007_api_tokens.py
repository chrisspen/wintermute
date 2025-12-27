"""Add API tokens table.

Revision ID: 0007_api_tokens
Revises: 0006_github_sources_agent_autostart
Create Date: 2025-02-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_api_tokens"
down_revision = "0006_github_sources_agent_autostart"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("permissions_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("api_tokens")

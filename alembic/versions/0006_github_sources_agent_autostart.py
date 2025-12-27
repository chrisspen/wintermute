"""Add agent assignment and auto-start to GitHub sources.

Revision ID: 0006_github_sources_agent_autostart
Revises: 0005_github_tokens
Create Date: 2025-02-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_github_sources_agent_autostart"
down_revision = "0005_github_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {row[1] for row in bind.execute(sa.text("PRAGMA table_info('github_sources')"))}
    if "agent_id" not in existing:
        op.add_column("github_sources", sa.Column("agent_id", sa.String(), nullable=True))
    if "auto_start" not in existing:
        op.add_column(
            "github_sources",
            sa.Column("auto_start", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = {row[1] for row in bind.execute(sa.text("PRAGMA table_info('github_sources')"))}
    if "auto_start" in existing:
        op.drop_column("github_sources", "auto_start")
    if "agent_id" in existing:
        op.drop_column("github_sources", "agent_id")

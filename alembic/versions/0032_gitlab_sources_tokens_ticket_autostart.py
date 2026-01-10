"""Add GitLab sources/tokens and ticket auto-start.

Revision ID: 0032_gitlab_sources_tokens_ticket_autostart
Revises: 0031_ui_column_preferences
Create Date: 2025-02-15
"""

from alembic import op
import sqlalchemy as sa

revision = "0032_gitlab_sources_tokens_ticket_autostart"
down_revision = "0031_ui_column_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gitlab_tokens",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("user_login", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "gitlab_sources",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("token_id", sa.String(), nullable=True),
        sa.Column("agent_id", sa.String(), nullable=True),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("project_path", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("labels_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("auto_start", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column("tickets", sa.Column("auto_start", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("tickets", "auto_start")
    op.drop_table("gitlab_sources")
    op.drop_table("gitlab_tokens")

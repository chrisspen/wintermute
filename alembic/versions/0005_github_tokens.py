"""Add GitHub tokens and link sources to tokens.

Revision ID: 0005_github_tokens
Revises: 0004_github_sources
Create Date: 2025-02-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_github_tokens"
down_revision = "0004_github_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_tokens",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("user_login", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.add_column("github_sources", sa.Column("token_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("github_sources", "token_id")
    op.drop_table("github_tokens")

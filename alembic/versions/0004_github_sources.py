"""Add GitHub sources table.

Revision ID: 0004_github_sources
Revises: 0003_credentials_note
Create Date: 2025-02-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_github_sources"
down_revision = "0003_credentials_note"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_sources",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("owner", sa.String(), nullable=False),
        sa.Column("repo", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("labels_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("github_sources")

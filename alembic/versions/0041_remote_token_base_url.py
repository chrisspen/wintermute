"""Add base_url to remote_tokens for self-hosted GitLab/GitHub instances.

Revision ID: 0041_remote_token_base_url
Revises: 0040_ticket_sprints_junction
Create Date: 2026-01-11
"""

from alembic import op
import sqlalchemy as sa


revision = "0041_remote_token_base_url"
down_revision = "0040_ticket_sprints_junction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("remote_tokens", sa.Column("base_url", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("remote_tokens", "base_url")

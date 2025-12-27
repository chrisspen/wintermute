"""Add note to credentials.

Revision ID: 0003_credentials_note
Revises: 0002_projects_agents_sessions
Create Date: 2025-02-14 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_credentials_note"
down_revision = "0002_projects_agents_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("credentials", sa.Column("note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("credentials", "note")

"""Unify GitHub/GitLab tokens into remote_tokens.

Revision ID: 0035_remote_tokens_unify
Revises: 0034_agent_session_claude_session_id
Create Date: 2026-01-10
"""

from alembic import op
import sqlalchemy as sa

revision = "0035_remote_tokens_unify"
down_revision = "0034_agent_session_claude_session_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "remote_tokens",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("user_login", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        """
        INSERT INTO remote_tokens (
            id, provider, note, token, user_id, user_login, created_at, updated_at
        )
        SELECT
            id, 'github', note, token, user_id, user_login, created_at, updated_at
        FROM github_tokens
        """
    )
    op.execute(
        """
        INSERT INTO remote_tokens (
            id, provider, note, token, user_id, user_login, created_at, updated_at
        )
        SELECT
            id, 'gitlab', note, token, user_id, user_login, created_at, updated_at
        FROM gitlab_tokens
        """
    )
    op.drop_table("github_tokens")
    op.drop_table("gitlab_tokens")


def downgrade() -> None:
    op.create_table(
        "github_tokens",
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
    op.execute(
        """
        INSERT INTO github_tokens (
            id, note, token, user_id, user_login, created_at, updated_at
        )
        SELECT
            id, note, token, user_id, user_login, created_at, updated_at
        FROM remote_tokens
        WHERE provider = 'github'
        """
    )
    op.execute(
        """
        INSERT INTO gitlab_tokens (
            id, note, token, user_id, user_login, created_at, updated_at
        )
        SELECT
            id, note, token, user_id, user_login, created_at, updated_at
        FROM remote_tokens
        WHERE provider = 'gitlab'
        """
    )
    op.drop_table("remote_tokens")

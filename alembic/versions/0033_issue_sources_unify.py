"""Unify GitHub/GitLab sources into issue_sources.

Revision ID: 0033_issue_sources_unify
Revises: 0032_gitlab_sources_tokens_ticket_autostart
Create Date: 2025-02-16
"""

from alembic import op
import sqlalchemy as sa

revision = "0033_issue_sources_unify"
down_revision = "0032_gitlab_sources_tokens_ticket_autostart"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "issue_sources",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("token_id", sa.String(), nullable=True),
        sa.Column("agent_id", sa.String(), nullable=True),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("repo", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("labels_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("auto_start", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        """
        INSERT INTO issue_sources (
            id, provider, token_id, agent_id, project_id, repo, state, labels_json,
            enabled, auto_start, created_at, updated_at
        )
        SELECT
            id, 'github', token_id, agent_id, project_id,
            owner || '/' || repo,
            state, labels_json, enabled, auto_start, created_at, updated_at
        FROM github_sources
        """
    )
    op.execute(
        """
        INSERT INTO issue_sources (
            id, provider, token_id, agent_id, project_id, repo, state, labels_json,
            enabled, auto_start, created_at, updated_at
        )
        SELECT
            id, 'gitlab', token_id, agent_id, project_id,
            project_path,
            state, labels_json, enabled, auto_start, created_at, updated_at
        FROM gitlab_sources
        """
    )
    op.drop_table("github_sources")
    op.drop_table("gitlab_sources")


def downgrade() -> None:
    op.create_table(
        "github_sources",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("token_id", sa.String(), nullable=True),
        sa.Column("agent_id", sa.String(), nullable=True),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("owner", sa.String(), nullable=False),
        sa.Column("repo", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("labels_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("auto_start", sa.Integer(), nullable=False),
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
    op.execute(
        """
        INSERT INTO github_sources (
            id, token_id, agent_id, project_id, owner, repo, state, labels_json,
            enabled, auto_start, created_at, updated_at
        )
        SELECT
            id, token_id, agent_id, project_id,
            CASE
                WHEN instr(repo, '/') > 0 THEN substr(repo, 1, instr(repo, '/') - 1)
                ELSE repo
            END AS owner,
            CASE
                WHEN instr(repo, '/') > 0 THEN substr(repo, instr(repo, '/') + 1)
                ELSE repo
            END AS repo,
            state, labels_json, enabled, auto_start, created_at, updated_at
        FROM issue_sources
        WHERE provider = 'github'
        """
    )
    op.execute(
        """
        INSERT INTO gitlab_sources (
            id, token_id, agent_id, project_id, project_path, state, labels_json,
            enabled, auto_start, created_at, updated_at
        )
        SELECT
            id, token_id, agent_id, project_id,
            repo,
            state, labels_json, enabled, auto_start, created_at, updated_at
        FROM issue_sources
        WHERE provider = 'gitlab'
        """
    )
    op.drop_table("issue_sources")

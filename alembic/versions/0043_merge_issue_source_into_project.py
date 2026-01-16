"""Merge issue source fields into project model.

Revision ID: 0043_merge_issue_source_into_project
Revises: 0042_ticket_created_by
Create Date: 2026-01-13
"""

from alembic import op
import sqlalchemy as sa

revision = "0043_merge_issue_source_into_project"
down_revision = "0042_ticket_created_by"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new columns to projects table
    op.add_column(
        "projects",
        sa.Column("provider", sa.String(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("source_token_id", sa.String(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("source_agent_id", sa.String(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("source_repo", sa.String(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("issue_state", sa.String(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("issue_labels_json", sa.Text(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "projects",
        sa.Column("source_enabled", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "projects",
        sa.Column("auto_start", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "projects",
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=False, server_default="300"),
    )

    # Migrate data from issue_sources to projects
    # For each project, copy data from its first (and typically only) issue source
    conn = op.get_bind()
    projects = conn.execute(sa.text("SELECT id FROM projects")).fetchall()
    for (project_id,) in projects:
        source = conn.execute(
            sa.text(
                "SELECT provider, token_id, agent_id, repo, state, labels_json, enabled, auto_start, poll_interval_seconds "
                "FROM issue_sources WHERE project_id = :pid ORDER BY created_at ASC LIMIT 1"
            ),
            {"pid": project_id},
        ).fetchone()
        if source:
            conn.execute(
                sa.text(
                    "UPDATE projects SET "
                    "provider = :provider, "
                    "source_token_id = :token_id, "
                    "source_agent_id = :agent_id, "
                    "source_repo = :repo, "
                    "issue_state = :state, "
                    "issue_labels_json = :labels_json, "
                    "source_enabled = :enabled, "
                    "auto_start = :auto_start, "
                    "poll_interval_seconds = :poll_interval "
                    "WHERE id = :pid"
                ),
                {
                    "provider": source[0],
                    "token_id": source[1],
                    "agent_id": source[2],
                    "repo": source[3],
                    "state": source[4],
                    "labels_json": source[5],
                    "enabled": source[6],
                    "auto_start": source[7],
                    "poll_interval": source[8],
                    "pid": project_id,
                },
            )

    # Note: We keep issue_sources table for now; can be dropped in a future migration


def downgrade() -> None:
    op.drop_column("projects", "poll_interval_seconds")
    op.drop_column("projects", "auto_start")
    op.drop_column("projects", "source_enabled")
    op.drop_column("projects", "issue_labels_json")
    op.drop_column("projects", "issue_state")
    op.drop_column("projects", "source_repo")
    op.drop_column("projects", "source_agent_id")
    op.drop_column("projects", "source_token_id")
    op.drop_column("projects", "provider")

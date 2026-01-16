"""Add chat channels and session file management for standalone agent sessions.

Revision ID: 0044_chat_channels_session_files
Revises: 0043_merge_issue_source_into_project
Create Date: 2026-01-14
"""

from alembic import op
import sqlalchemy as sa

revision = "0044_chat_channels_session_files"
down_revision = "0043_merge_issue_source_into_project"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create session_file_configs table
    op.create_table(
        "session_file_configs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )

    # Create session_file_definitions table
    op.create_table(
        "session_file_definitions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("config_id", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_content", sa.Text(), nullable=False),
        sa.Column("required", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sync_on_exit", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )

    # Create session_files table (actual content per agent)
    op.create_table(
        "session_files",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("definition_id", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )

    # Create channels table for multi-platform chat
    op.create_table(
        "channels",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),  # slack, telegram, discord
        sa.Column("name", sa.String(), nullable=False),  # e.g. claude/boreas
        sa.Column("external_channel_id", sa.String(), nullable=True),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )

    # Add session_file_config_id to agents
    op.add_column(
        "agents",
        sa.Column("session_file_config_id", sa.String(), nullable=True),
    )

    # Make project_id nullable on agent_sessions (for standalone sessions)
    # SQLite doesn't support ALTER COLUMN, so we use batch_alter_table
    with op.batch_alter_table("agent_sessions") as batch_op:
        batch_op.alter_column("project_id", existing_type=sa.String(), nullable=True)
        batch_op.add_column(sa.Column("initial_prompt", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("workspace_path", sa.String(), nullable=True))

    # Make ticket_id nullable on comments and add origin field
    # ticket_id is already nullable in the current schema
    op.add_column(
        "comments",
        sa.Column("agent_session_id", sa.String(), nullable=True),
    )
    op.add_column(
        "comments",
        sa.Column("origin", sa.String(), nullable=True),  # web, slack, telegram, etc.
    )

    # Create default session file config
    conn = op.get_bind()
    import uuid
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    config_id = str(uuid.uuid4())

    conn.execute(
        sa.text(
            "INSERT INTO session_file_configs (id, name, description, created_at, updated_at) "
            "VALUES (:id, :name, :description, :created_at, :updated_at)"
        ),
        {
            "id": config_id,
            "name": "Standard Agent",
            "description": "Default session file configuration for agents",
            "created_at": now,
            "updated_at": now,
        },
    )

    # Create default session file definitions
    default_files = [
        {
            "filename": "AGENTS.md",
            "description": "Instructions and guidelines for this agent",
            "default_content": "# Agent Instructions\n\nStandby for instructions.",
            "required": 1,
            "sync_on_exit": 1,
            "sort_order": 0,
        },
        {
            "filename": "STATE.md",
            "description": "Current goals, constraints, and known issues",
            "default_content": "# State\n\n## Goals\n\n## Constraints\n\n## Known Issues\n",
            "required": 0,
            "sync_on_exit": 1,
            "sort_order": 1,
        },
        {
            "filename": "DECISIONS.md",
            "description": "Log of decisions and rationale",
            "default_content": "# Decisions\n\n",
            "required": 0,
            "sync_on_exit": 1,
            "sort_order": 2,
        },
        {
            "filename": "TODO.md",
            "description": "Pending tasks",
            "default_content": "# TODO\n\n",
            "required": 0,
            "sync_on_exit": 1,
            "sort_order": 3,
        },
        {
            "filename": "CONTEXT.md",
            "description": "Compressed narrative summary for session continuity",
            "default_content": "# Context\n\n",
            "required": 0,
            "sync_on_exit": 1,
            "sort_order": 4,
        },
    ]

    for f in default_files:
        def_id = str(uuid.uuid4())
        conn.execute(
            sa.text(
                "INSERT INTO session_file_definitions "
                "(id, config_id, filename, description, default_content, required, sync_on_exit, sort_order, created_at, updated_at) "
                "VALUES (:id, :config_id, :filename, :description, :default_content, :required, :sync_on_exit, :sort_order, :created_at, :updated_at)"
            ),
            {
                "id": def_id,
                "config_id": config_id,
                "filename": f["filename"],
                "description": f["description"],
                "default_content": f["default_content"],
                "required": f["required"],
                "sync_on_exit": f["sync_on_exit"],
                "sort_order": f["sort_order"],
                "created_at": now,
                "updated_at": now,
            },
        )


def downgrade() -> None:
    op.drop_column("comments", "origin")
    op.drop_column("comments", "agent_session_id")
    op.drop_column("agent_sessions", "workspace_path")
    op.drop_column("agent_sessions", "initial_prompt")
    op.drop_column("agents", "session_file_config_id")
    op.drop_table("channels")
    op.drop_table("session_files")
    op.drop_table("session_file_definitions")
    op.drop_table("session_file_configs")

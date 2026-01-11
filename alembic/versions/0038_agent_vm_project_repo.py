"""Move VM linkage from Project to Agent, move repo config to Project.

This migration:
1. Adds vm_target_id to agents table (required)
2. Adds repo_mode, repo_path, repo_url to projects table
3. Removes project_vm_id from agent_sessions table
4. Updates repo_resources to reference agent_id instead of project_vm_id
5. Drops project_vms table

Revision ID: 0038_agent_vm_project_repo
Revises: 0037_agent_llm_config
Create Date: 2026-01-10
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "0038_agent_vm_project_repo"
down_revision = "0037_agent_llm_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: Add vm_target_id to agents (nullable initially for migration)
    op.add_column("agents", sa.Column("vm_target_id", sa.String(), nullable=True))

    # Step 2: Add repo config to projects
    op.add_column("projects", sa.Column("repo_mode", sa.String(), nullable=True))
    op.add_column("projects", sa.Column("repo_path", sa.String(), nullable=True))
    op.add_column("projects", sa.Column("repo_url", sa.String(), nullable=True))

    # Step 3: Migrate data from project_vms to projects and agents
    # Get connection for raw SQL
    conn = op.get_bind()

    # For each project_vm, update the project with repo config
    # and try to set agent vm_target_id based on existing sessions
    project_vms = conn.execute(
        sa.text("SELECT id, project_id, vm_target_id, repo_mode, repo_path, repo_url FROM project_vms")
    ).fetchall()

    for pvm in project_vms:
        pvm_id, project_id, vm_target_id, repo_mode, repo_path, repo_url = pvm
        # Update project with repo config from first project_vm
        conn.execute(
            sa.text(
                "UPDATE projects SET repo_mode = :repo_mode, repo_path = :repo_path, repo_url = :repo_url "
                "WHERE id = :project_id AND repo_mode IS NULL"
            ),
            {"repo_mode": repo_mode, "repo_path": repo_path, "repo_url": repo_url, "project_id": project_id},
        )

        # Find agents that have sessions using this project_vm and set their vm_target_id
        sessions = conn.execute(
            sa.text("SELECT DISTINCT agent_id FROM agent_sessions WHERE project_vm_id = :pvm_id"),
            {"pvm_id": pvm_id},
        ).fetchall()
        for (agent_id,) in sessions:
            conn.execute(
                sa.text("UPDATE agents SET vm_target_id = :vm_target_id WHERE id = :agent_id AND vm_target_id IS NULL"),
                {"vm_target_id": vm_target_id, "agent_id": agent_id},
            )

    # Step 4: For agents without vm_target_id, assign first available VM
    # (needed for agents that haven't had sessions yet)
    first_vm = conn.execute(sa.text("SELECT id FROM vm_targets LIMIT 1")).fetchone()
    if first_vm:
        conn.execute(
            sa.text("UPDATE agents SET vm_target_id = :vm_id WHERE vm_target_id IS NULL"),
            {"vm_id": first_vm[0]},
        )

    # Step 5: Update repo_resources to use agent_id instead of project_vm_id
    # The agent_id column already exists, we just need to populate it from sessions
    # and then we can drop project_vm_id later

    # Step 6: Remove project_vm_id from agent_sessions
    # SQLite requires table recreation for column removal
    op.execute(
        """
        CREATE TABLE agent_sessions_new (
            id VARCHAR PRIMARY KEY NOT NULL,
            project_id VARCHAR NOT NULL,
            agent_id VARCHAR NOT NULL,
            ticket_id VARCHAR,
            status VARCHAR NOT NULL,
            repo_path VARCHAR NOT NULL,
            thread_ts VARCHAR,
            mcp_conversation_id VARCHAR,
            claude_session_id VARCHAR,
            last_output TEXT,
            last_output_offset INTEGER NOT NULL,
            output_buffer TEXT,
            output_buffer_updated_at VARCHAR,
            prompt_pending TEXT,
            prompt_sent_at VARCHAR,
            last_output_at VARCHAR,
            awaiting_response INTEGER NOT NULL DEFAULT 0,
            last_user_message TEXT,
            queued_user_messages TEXT,
            awaiting_response_offset INTEGER NOT NULL DEFAULT 0,
            created_at VARCHAR NOT NULL,
            updated_at VARCHAR NOT NULL
        )
        """
    )
    op.execute(
        """
        INSERT INTO agent_sessions_new (
            id, project_id, agent_id, ticket_id, status, repo_path, thread_ts,
            mcp_conversation_id, claude_session_id, last_output, last_output_offset,
            output_buffer, output_buffer_updated_at, prompt_pending, prompt_sent_at,
            last_output_at, awaiting_response, last_user_message, queued_user_messages,
            awaiting_response_offset, created_at, updated_at
        )
        SELECT
            id, project_id, agent_id, ticket_id, status, repo_path, thread_ts,
            mcp_conversation_id, claude_session_id, last_output, last_output_offset,
            output_buffer, output_buffer_updated_at, prompt_pending, prompt_sent_at,
            last_output_at, awaiting_response, last_user_message, queued_user_messages,
            awaiting_response_offset, created_at, updated_at
        FROM agent_sessions
        """
    )
    op.drop_table("agent_sessions")
    op.rename_table("agent_sessions_new", "agent_sessions")

    # Step 7: Update repo_resources - remove project_vm_id
    op.execute(
        """
        CREATE TABLE repo_resources_new (
            id VARCHAR PRIMARY KEY NOT NULL,
            project_id VARCHAR NOT NULL,
            agent_id VARCHAR,
            repo_mode VARCHAR NOT NULL,
            path TEXT NOT NULL,
            status VARCHAR NOT NULL,
            session_id VARCHAR,
            last_used_at VARCHAR,
            created_at VARCHAR NOT NULL,
            updated_at VARCHAR NOT NULL
        )
        """
    )
    op.execute(
        """
        INSERT INTO repo_resources_new (
            id, project_id, agent_id, repo_mode, path, status, session_id,
            last_used_at, created_at, updated_at
        )
        SELECT
            id, project_id, agent_id, repo_mode, path, status, session_id,
            last_used_at, created_at, updated_at
        FROM repo_resources
        """
    )
    op.drop_table("repo_resources")
    op.rename_table("repo_resources_new", "repo_resources")

    # Step 8: Drop project_vms table
    op.drop_table("project_vms")


def downgrade() -> None:
    # Recreate project_vms table
    op.create_table(
        "project_vms",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("vm_target_id", sa.String(), nullable=False),
        sa.Column("repo_mode", sa.String(), nullable=False),
        sa.Column("repo_path", sa.String(), nullable=True),
        sa.Column("repo_url", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )

    # Recreate agent_sessions with project_vm_id
    op.execute(
        """
        CREATE TABLE agent_sessions_new (
            id VARCHAR PRIMARY KEY NOT NULL,
            project_id VARCHAR NOT NULL,
            project_vm_id VARCHAR NOT NULL,
            agent_id VARCHAR NOT NULL,
            ticket_id VARCHAR,
            status VARCHAR NOT NULL,
            repo_path VARCHAR NOT NULL,
            thread_ts VARCHAR,
            mcp_conversation_id VARCHAR,
            claude_session_id VARCHAR,
            last_output TEXT,
            last_output_offset INTEGER NOT NULL,
            output_buffer TEXT,
            output_buffer_updated_at VARCHAR,
            prompt_pending TEXT,
            prompt_sent_at VARCHAR,
            last_output_at VARCHAR,
            awaiting_response INTEGER NOT NULL DEFAULT 0,
            last_user_message TEXT,
            queued_user_messages TEXT,
            awaiting_response_offset INTEGER NOT NULL DEFAULT 0,
            created_at VARCHAR NOT NULL,
            updated_at VARCHAR NOT NULL
        )
        """
    )
    op.execute(
        """
        INSERT INTO agent_sessions_new (
            id, project_id, project_vm_id, agent_id, ticket_id, status, repo_path, thread_ts,
            mcp_conversation_id, claude_session_id, last_output, last_output_offset,
            output_buffer, output_buffer_updated_at, prompt_pending, prompt_sent_at,
            last_output_at, awaiting_response, last_user_message, queued_user_messages,
            awaiting_response_offset, created_at, updated_at
        )
        SELECT
            id, project_id, '', agent_id, ticket_id, status, repo_path, thread_ts,
            mcp_conversation_id, claude_session_id, last_output, last_output_offset,
            output_buffer, output_buffer_updated_at, prompt_pending, prompt_sent_at,
            last_output_at, awaiting_response, last_user_message, queued_user_messages,
            awaiting_response_offset, created_at, updated_at
        FROM agent_sessions
        """
    )
    op.drop_table("agent_sessions")
    op.rename_table("agent_sessions_new", "agent_sessions")

    # Recreate repo_resources with project_vm_id
    op.execute(
        """
        CREATE TABLE repo_resources_new (
            id VARCHAR PRIMARY KEY NOT NULL,
            project_id VARCHAR NOT NULL,
            project_vm_id VARCHAR NOT NULL,
            repo_mode VARCHAR NOT NULL,
            path TEXT NOT NULL,
            status VARCHAR NOT NULL,
            session_id VARCHAR,
            agent_id VARCHAR,
            last_used_at VARCHAR,
            created_at VARCHAR NOT NULL,
            updated_at VARCHAR NOT NULL
        )
        """
    )
    op.execute(
        """
        INSERT INTO repo_resources_new (
            id, project_id, project_vm_id, repo_mode, path, status, session_id,
            agent_id, last_used_at, created_at, updated_at
        )
        SELECT
            id, project_id, '', repo_mode, path, status, session_id,
            agent_id, last_used_at, created_at, updated_at
        FROM repo_resources
        """
    )
    op.drop_table("repo_resources")
    op.rename_table("repo_resources_new", "repo_resources")

    # Remove columns from projects
    op.drop_column("projects", "repo_url")
    op.drop_column("projects", "repo_path")
    op.drop_column("projects", "repo_mode")

    # Remove vm_target_id from agents
    op.drop_column("agents", "vm_target_id")

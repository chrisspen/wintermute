"""projects tickets agents sessions

Revision ID: 0002_projects_agents_sessions
Revises: 0001_initial
Create Date: 2024-12-25
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_projects_agents_sessions"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False, unique=True),
        sa.Column("slack_channel_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_table(
        "tickets",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("assigned_to", sa.String(), nullable=True),
        sa.Column("estimate", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_index("ix_tickets_project_id", "tickets", ["project_id"])
    op.create_table(
        "vm_targets",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("host", sa.String(), nullable=False),
        sa.Column("user", sa.String(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_table(
        "agents",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False, unique=True),
        sa.Column("command", sa.String(), nullable=False),
        sa.Column("required_ssh_options", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
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
    op.create_index("ix_project_vms_project", "project_vms", ["project_id"])
    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("project_vm_id", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("ticket_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("repo_path", sa.String(), nullable=False),
        sa.Column("thread_ts", sa.String(), nullable=True),
        sa.Column("last_output", sa.Text(), nullable=True),
        sa.Column("last_output_offset", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_index("ix_sessions_project", "agent_sessions", ["project_id"])
    op.create_index("ix_sessions_project_vm", "agent_sessions", ["project_vm_id"])
    op.create_index("ix_sessions_agent", "agent_sessions", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_agent", table_name="agent_sessions")
    op.drop_index("ix_sessions_project_vm", table_name="agent_sessions")
    op.drop_index("ix_sessions_project", table_name="agent_sessions")
    op.drop_table("agent_sessions")
    op.drop_index("ix_project_vms_project", table_name="project_vms")
    op.drop_table("project_vms")
    op.drop_table("agents")
    op.drop_table("vm_targets")
    op.drop_index("ix_tickets_project_id", table_name="tickets")
    op.drop_table("tickets")
    op.drop_table("projects")

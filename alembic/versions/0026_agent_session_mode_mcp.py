"""Add agent session mode and MCP conversation id."""

from alembic import op
import sqlalchemy as sa


revision = "0026_agent_session_mode_mcp"
down_revision = "0025_session_awaiting_response_offset"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("session_mode", sa.String(), nullable=False, server_default="tmux"),
    )
    op.add_column(
        "agent_sessions",
        sa.Column("mcp_conversation_id", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "mcp_conversation_id")
    op.drop_column("agents", "session_mode")

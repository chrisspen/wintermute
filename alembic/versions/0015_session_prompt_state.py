"""Add session prompt state and last output time."""

from alembic import op
import sqlalchemy as sa

revision = "0015_session_prompt_state"
down_revision = "0014_session_output_buffer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_sessions", sa.Column("prompt_pending", sa.Text(), nullable=True))
    op.add_column("agent_sessions", sa.Column("prompt_sent_at", sa.String(), nullable=True))
    op.add_column("agent_sessions", sa.Column("last_output_at", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_sessions", "last_output_at")
    op.drop_column("agent_sessions", "prompt_sent_at")
    op.drop_column("agent_sessions", "prompt_pending")

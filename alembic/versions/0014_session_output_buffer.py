"""Add session output buffer fields."""

from alembic import op
import sqlalchemy as sa

revision = "0014_session_output_buffer"
down_revision = "0013_agent_responses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_sessions", sa.Column("output_buffer", sa.Text(), nullable=True))
    op.add_column("agent_sessions", sa.Column("output_buffer_updated_at", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_sessions", "output_buffer_updated_at")
    op.drop_column("agent_sessions", "output_buffer")

"""Add agent_id to tickets."""

from alembic import op
import sqlalchemy as sa

revision = "0016_ticket_agent_id"
down_revision = "0015_session_prompt_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("agent_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "agent_id")

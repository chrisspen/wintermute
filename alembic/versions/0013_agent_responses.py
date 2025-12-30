"""Add agent response patterns."""

from alembic import op
import sqlalchemy as sa

revision = "0013_agent_responses"
down_revision = "0012_ticket_source_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_responses",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("response", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("agent_responses")

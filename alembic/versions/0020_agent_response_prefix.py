"""Add response prefix to agents."""

from alembic import op
import sqlalchemy as sa

revision = "0020_agent_response_prefix"
down_revision = "0019_agent_input_echo_prefix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("response_prefix", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "response_prefix")

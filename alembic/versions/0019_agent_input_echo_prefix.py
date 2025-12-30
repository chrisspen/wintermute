"""Add input echo prefix to agents."""

from alembic import op
import sqlalchemy as sa

revision = "0019_agent_input_echo_prefix"
down_revision = "0018_agent_env_vars"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("input_echo_prefix", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "input_echo_prefix")

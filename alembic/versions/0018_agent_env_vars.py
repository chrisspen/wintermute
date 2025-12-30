"""Add env vars to agents."""

from alembic import op
import sqlalchemy as sa

revision = "0018_agent_env_vars"
down_revision = "0017_comment_author"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("env_vars", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "env_vars")

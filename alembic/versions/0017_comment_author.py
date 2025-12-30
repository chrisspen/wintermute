"""Add author to comments."""

from alembic import op
import sqlalchemy as sa

revision = "0017_comment_author"
down_revision = "0016_ticket_agent_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("comments", sa.Column("author", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("comments", "author")

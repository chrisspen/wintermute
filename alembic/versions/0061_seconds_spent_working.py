"""Add seconds_spent_working field to comments

Revision ID: 0061
Revises: 0060
Create Date: 2026-01-28

"""
from alembic import op # pylint: disable=no-name-in-module
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0061_seconds_spent_working'
down_revision = '0060_agent_health_command'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('comments', sa.Column('seconds_spent_working', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('comments', 'seconds_spent_working')

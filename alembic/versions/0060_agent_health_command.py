"""Add health_command field to agents

Revision ID: 0060
Revises: 0059
Create Date: 2026-01-28

"""
from alembic import op # pylint: disable=no-name-in-module
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0060_agent_health_command'
down_revision = '0059_ticket_history'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('agents', sa.Column('health_command', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('agents', 'health_command')

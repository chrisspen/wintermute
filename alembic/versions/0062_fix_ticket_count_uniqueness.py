"""Fix duplicate ticket counts and add unique constraint

Revision ID: 0062
Revises: 0061
Create Date: 2026-01-28

"""
from alembic import op # pylint: disable=no-name-in-module
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0062_fix_ticket_count_uniqueness'
down_revision = '0061_seconds_spent_working'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # First, fix duplicate counts by reassigning unique counts per project
    # Get connection for raw SQL
    conn = op.get_bind()

    # Find all projects that have tickets
    projects = conn.execute(sa.text("SELECT DISTINCT project_id FROM tickets WHERE project_id IS NOT NULL")).fetchall()

    for (project_id,) in projects:
        # Get all tickets for this project ordered by created_at
        tickets = conn.execute(sa.text("SELECT id FROM tickets WHERE project_id = :project_id ORDER BY created_at ASC"), {"project_id": project_id}).fetchall()

        # Reassign counts sequentially
        for i, (ticket_id,) in enumerate(tickets, start=1):
            conn.execute(sa.text("UPDATE tickets SET count = :count WHERE id = :id"), {"count": i, "id": ticket_id})

    # Now add unique constraint on (project_id, count)
    # SQLite requires batch mode for ALTER TABLE constraints
    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_tickets_project_count', ['project_id', 'count'])


def downgrade() -> None:
    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.drop_constraint('uq_tickets_project_count', type_='unique')

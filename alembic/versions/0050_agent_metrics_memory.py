"""Add agent metrics and memory management fields

Revision ID: 0050_agent_metrics_memory
Revises: 0049_project_build_status_image_url
Create Date: 2026-01-18

"""
from alembic import op # pylint: disable=no-name-in-module
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0050_agent_metrics_memory'
down_revision = '0049_project_build_status_image_url'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add average_memory_usage_mb to agents table (default 1000)
    op.add_column('agents', sa.Column('average_memory_usage_mb', sa.Integer(), nullable=False, server_default='1000'))

    # Add required_reserve_memory_gb to vm_targets table (default 0.0)
    op.add_column('vm_targets', sa.Column('required_reserve_memory_gb', sa.Float(), nullable=False, server_default='0.0'))

    # Create metric_definitions table
    op.create_table(
        'metric_definitions', sa.Column('id', sa.String(), nullable=False), sa.Column('metric_type', sa.String(), nullable=False),
        sa.Column('recording_frequency_minutes', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('enabled', sa.Integer(), nullable=False, server_default='1'), sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=False), sa.PrimaryKeyConstraint('id'), sa.UniqueConstraint('metric_type')
    )

    # Create agent_metrics_logs table
    op.create_table(
        'agent_metrics_logs', sa.Column('id', sa.String(), nullable=False), sa.Column('agent_id', sa.String(), nullable=False),
        sa.Column('metric_definition_id', sa.String(), nullable=False), sa.Column('value', sa.Float(), nullable=False),
        sa.Column('recorded_at', sa.String(), nullable=False), sa.Column('created_at', sa.String(), nullable=False), sa.PrimaryKeyConstraint('id')
    )

    # Create indexes for agent_metrics_logs
    op.create_index('ix_agent_metrics_logs_agent_id', 'agent_metrics_logs', ['agent_id'])
    op.create_index('ix_agent_metrics_logs_metric_definition_id', 'agent_metrics_logs', ['metric_definition_id'])
    op.create_index('ix_agent_metrics_logs_recorded_at', 'agent_metrics_logs', ['recorded_at'])
    op.create_index('ix_agent_metrics_logs_agent_metric', 'agent_metrics_logs', ['agent_id', 'metric_definition_id'])


def downgrade() -> None:
    # Drop indexes
    op.drop_index('ix_agent_metrics_logs_agent_metric', 'agent_metrics_logs')
    op.drop_index('ix_agent_metrics_logs_recorded_at', 'agent_metrics_logs')
    op.drop_index('ix_agent_metrics_logs_metric_definition_id', 'agent_metrics_logs')
    op.drop_index('ix_agent_metrics_logs_agent_id', 'agent_metrics_logs')

    # Drop tables
    op.drop_table('agent_metrics_logs')
    op.drop_table('metric_definitions')

    # Drop columns
    op.drop_column('vm_targets', 'required_reserve_memory_gb')
    op.drop_column('agents', 'average_memory_usage_mb')

"""Add UI column preferences.

Revision ID: 0031_ui_column_preferences
Revises: 0030_agent_trust_level
Create Date: 2025-02-14
"""

from alembic import op
import sqlalchemy as sa

revision = "0031_ui_column_preferences"
down_revision = "0030_agent_trust_level"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ui_column_preferences",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("columns_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "model", name="uq_ui_column_preferences_user_model"),
    )


def downgrade() -> None:
    op.drop_table("ui_column_preferences")

"""add metadata to usage_events

Revision ID: a1b2c3d4e5f6
Revises: f823a6b2391d
Create Date: 2026-03-10

"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "f823a6b2391d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("usage_events", sa.Column("extra_data", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("usage_events", "extra_data")

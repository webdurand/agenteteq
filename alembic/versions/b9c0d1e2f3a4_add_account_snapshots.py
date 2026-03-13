"""add_account_snapshots

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-03-13 23:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b9c0d1e2f3a4'
down_revision: Union[str, None] = 'a8b9c0d1e2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'account_snapshots',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tracked_account_id', sa.Integer(), nullable=False),
        sa.Column('followers_count', sa.Integer(), server_default='0'),
        sa.Column('posts_count', sa.Integer(), server_default='0'),
        sa.Column('avg_engagement', sa.Float(), server_default='0.0'),
        sa.Column('fetched_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['tracked_account_id'], ['tracked_accounts.id']),
    )
    op.create_index('idx_snapshots_account_date', 'account_snapshots', ['tracked_account_id', 'fetched_at'])


def downgrade() -> None:
    op.drop_index('idx_snapshots_account_date', table_name='account_snapshots')
    op.drop_table('account_snapshots')

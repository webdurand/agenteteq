"""add_shared_assets_table

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-03-15 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e3f4a5b6c7d8'
down_revision: Union[str, None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'shared_assets',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('tags', sa.String(), nullable=False, server_default=''),
        sa.Column('category', sa.String(), nullable=True, server_default='icon'),
        sa.Column('asset_type', sa.String(), nullable=True, server_default='svg'),
        sa.Column('source', sa.String(), nullable=True, server_default='seed'),
        sa.Column('url', sa.String(), nullable=False),
        sa.Column('thumbnail_url', sa.String(), nullable=True),
        sa.Column('metadata_json', sa.Text(), nullable=True, server_default='{}'),
        sa.Column('created_by', sa.String(), nullable=True),
        sa.Column('usage_count', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_shared_assets_category', 'shared_assets', ['category', 'asset_type'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_shared_assets_category', table_name='shared_assets')
    op.drop_table('shared_assets')

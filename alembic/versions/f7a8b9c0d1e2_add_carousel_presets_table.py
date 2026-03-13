"""add_carousel_presets_table

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-03-13 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, None] = 'e6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'carousel_presets',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('brand_profile_id', sa.Integer(), nullable=True),
        sa.Column('style_anchor', sa.Text(), nullable=True),
        sa.Column('color_palette_json', sa.Text(), server_default='{}'),
        sa.Column('default_format', sa.String(), server_default='1350x1080'),
        sa.Column('default_slide_count', sa.Integer(), server_default='5'),
        sa.Column('sequential_slides', sa.Boolean(), server_default='1'),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.Column('updated_at', sa.String(), nullable=True),
    )
    op.create_index('idx_carousel_presets_user', 'carousel_presets', ['user_id'])


def downgrade() -> None:
    op.drop_index('idx_carousel_presets_user', table_name='carousel_presets')
    op.drop_table('carousel_presets')

"""add_brand_profiles_table

Revision ID: c4d5e6f7g8h9
Revises: b3329ce4e27a
Create Date: 2026-03-13 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7g8h9'
down_revision: Union[str, None] = 'b3329ce4e27a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'brand_profiles',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('is_default', sa.Boolean(), nullable=True),
        sa.Column('primary_color', sa.String(), nullable=True),
        sa.Column('secondary_color', sa.String(), nullable=True),
        sa.Column('accent_color', sa.String(), nullable=True),
        sa.Column('bg_color', sa.String(), nullable=True),
        sa.Column('text_primary_color', sa.String(), nullable=True),
        sa.Column('text_secondary_color', sa.String(), nullable=True),
        sa.Column('font_heading', sa.String(), nullable=True),
        sa.Column('font_body', sa.String(), nullable=True),
        sa.Column('logo_url', sa.String(), nullable=True),
        sa.Column('style_description', sa.Text(), nullable=True),
        sa.Column('tone_of_voice', sa.String(), nullable=True),
        sa.Column('target_audience', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.Column('updated_at', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_brand_profiles_user', 'brand_profiles', ['user_id'])


def downgrade() -> None:
    op.drop_index('idx_brand_profiles_user', table_name='brand_profiles')
    op.drop_table('brand_profiles')

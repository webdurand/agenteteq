"""add_canvas_sessions_table

Revision ID: d2e3f4a5b6c7
Revises: 718889710bf8
Create Date: 2026-03-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, None] = '718889710bf8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'canvas_sessions',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=True, server_default=''),
        sa.Column('canvas_json', sa.Text(), nullable=False),
        sa.Column('thumbnail_url', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=True, server_default='active'),
        sa.Column('format', sa.String(), nullable=True, server_default='1080x1080'),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.Column('updated_at', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_canvas_sessions_user', 'canvas_sessions', ['user_id', 'status'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_canvas_sessions_user', table_name='canvas_sessions')
    op.drop_table('canvas_sessions')

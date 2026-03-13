"""add_content_plans

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-03-13 23:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c0d1e2f3a4b5'
down_revision: Union[str, None] = 'b9c0d1e2f3a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'content_plans',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('content_type', sa.String(), server_default='post'),
        sa.Column('platforms', sa.Text(), server_default='[]'),
        sa.Column('scheduled_at', sa.String(), nullable=True),
        sa.Column('status', sa.String(), server_default='idea'),
        sa.Column('carousel_id', sa.Integer(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_content_plans_user', 'content_plans', ['user_id', 'status'])
    op.create_index('idx_content_plans_schedule', 'content_plans', ['user_id', 'scheduled_at'])


def downgrade() -> None:
    op.drop_index('idx_content_plans_schedule', table_name='content_plans')
    op.drop_index('idx_content_plans_user', table_name='content_plans')
    op.drop_table('content_plans')

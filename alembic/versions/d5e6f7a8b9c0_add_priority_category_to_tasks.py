"""add_priority_category_to_tasks

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7g8h9
Create Date: 2026-03-13 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, None] = 'c4d5e6f7g8h9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tasks', sa.Column('priority', sa.String(), nullable=True))
    op.add_column('tasks', sa.Column('category', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('tasks', 'category')
    op.drop_column('tasks', 'priority')

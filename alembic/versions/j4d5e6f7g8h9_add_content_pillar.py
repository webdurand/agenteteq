"""add_content_pillar_to_content_plans

Revision ID: j4d5e6f7g8h9
Revises: i3c4d5e6f7g8
Create Date: 2026-04-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'j4d5e6f7g8h9'
down_revision: Union[str, Sequence[str], None] = 'i3c4d5e6f7g8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('content_plans', sa.Column('content_pillar', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('content_plans', 'content_pillar')

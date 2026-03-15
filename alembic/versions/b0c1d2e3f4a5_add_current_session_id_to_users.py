"""add_current_session_id_to_users

Revision ID: b0c1d2e3f4a5
Revises: a8b9c0d1e2f3
Create Date: 2026-03-15 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b0c1d2e3f4a5'
down_revision: Union[str, None] = 'a8b9c0d1e2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('current_session_id', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'current_session_id')

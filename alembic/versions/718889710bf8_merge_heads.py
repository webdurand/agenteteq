"""merge_heads

Revision ID: 718889710bf8
Revises: b0c1d2e3f4a5, c0d1e2f3a4b5
Create Date: 2026-03-15 09:46:45.412753

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '718889710bf8'
down_revision: Union[str, None] = ('b0c1d2e3f4a5', 'c0d1e2f3a4b5')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

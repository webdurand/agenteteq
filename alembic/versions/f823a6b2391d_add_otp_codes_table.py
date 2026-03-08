"""add_otp_codes_table

Revision ID: f823a6b2391d
Revises: 3716f0cdbf86
Create Date: 2026-03-08 15:08:00.533472

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f823a6b2391d'
down_revision: Union[str, None] = '3716f0cdbf86'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'otp_codes',
        sa.Column('phone_number', sa.String(), nullable=False),
        sa.Column('code', sa.String(6), nullable=False),
        sa.Column('purpose', sa.String(), nullable=False),
        sa.Column('attempts', sa.Integer(), default=0),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('phone_number'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('otp_codes')

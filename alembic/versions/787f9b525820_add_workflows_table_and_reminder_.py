"""add_workflows_table_and_reminder_workflow_id

Revision ID: 787f9b525820
Revises: a1b2c3d4e5f6
Create Date: 2026-03-12 14:53:19.913879

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '787f9b525820'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'workflows',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=True),
        sa.Column('original_request', sa.Text(), nullable=False),
        sa.Column('steps', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('status', sa.String(), nullable=False, server_default='draft'),
        sa.Column('current_step', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('notification_channel', sa.String(), nullable=True),
        sa.Column('last_run_at', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=True),
        sa.Column('updated_at', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_workflows_user_status', 'workflows', ['user_id', 'status'], unique=False)

    op.add_column('reminders', sa.Column('workflow_id', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('reminders', 'workflow_id')
    op.drop_index('idx_workflows_user_status', table_name='workflows')
    op.drop_table('workflows')

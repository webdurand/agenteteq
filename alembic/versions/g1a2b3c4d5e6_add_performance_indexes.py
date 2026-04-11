"""add_performance_indexes

Revision ID: g1a2b3c4d5e6
Revises: f7a8b9c0d1e2
Create Date: 2026-04-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'g1a2b3c4d5e6'
down_revision: Union[str, None] = 'f7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('idx_tasks_user_id', 'tasks', ['user_id'])
    op.create_index('idx_tasks_user_status', 'tasks', ['user_id', 'status'])
    op.create_index('idx_reminders_user_id', 'reminders', ['user_id'])
    op.create_index('idx_subscriptions_user_id', 'subscriptions', ['user_id'])
    op.create_index('idx_usage_events_user_id', 'usage_events', ['user_id'])
    op.create_index('idx_usage_events_user_type', 'usage_events', ['user_id', 'event_type'])


def downgrade() -> None:
    op.drop_index('idx_usage_events_user_type', 'usage_events')
    op.drop_index('idx_usage_events_user_id', 'usage_events')
    op.drop_index('idx_subscriptions_user_id', 'subscriptions')
    op.drop_index('idx_reminders_user_id', 'reminders')
    op.drop_index('idx_tasks_user_status', 'tasks')
    op.drop_index('idx_tasks_user_id', 'tasks')

"""add_cost_usd_column_and_performance_indexes

Revision ID: i3c4d5e6f7g8
Revises: h2b3c4d5e6f7
Create Date: 2026-04-11 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'i3c4d5e6f7g8'
down_revision: Union[str, Sequence[str], None] = ('e3f4a5b6c7d8', 'h2b3c4d5e6f7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add cost_usd column to usage_events for fast budget aggregation
    op.add_column('usage_events', sa.Column('cost_usd', sa.Float(), nullable=True, server_default='0'))

    # Index for budget queries: SUM(cost_usd) WHERE user_id=X AND event_type IN (...) AND created_at BETWEEN
    op.create_index('idx_usage_events_budget', 'usage_events', ['user_id', 'event_type', 'created_at'])

    # Index for ProcessedMessage cleanup (hourly DELETE WHERE created_at < threshold)
    op.create_index('idx_processed_messages_created', 'processed_messages', ['created_at'])


def downgrade() -> None:
    op.drop_index('idx_processed_messages_created', table_name='processed_messages')
    op.drop_index('idx_usage_events_budget', table_name='usage_events')
    op.drop_column('usage_events', 'cost_usd')

"""add_social_monitoring_tables

Revision ID: b3329ce4e27a
Revises: 787f9b525820
Create Date: 2026-03-13 14:11:20.239073

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b3329ce4e27a'
down_revision: Union[str, None] = '787f9b525820'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tracked_accounts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('platform', sa.String(), nullable=False),
        sa.Column('username', sa.String(), nullable=False),
        sa.Column('display_name', sa.String(), nullable=True),
        sa.Column('profile_url', sa.String(), nullable=True),
        sa.Column('profile_pic_url', sa.String(), nullable=True),
        sa.Column('bio', sa.Text(), nullable=True),
        sa.Column('followers_count', sa.Integer(), nullable=True),
        sa.Column('posts_count', sa.Integer(), nullable=True),
        sa.Column('metadata_json', sa.Text(), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('last_fetched_at', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_tracked_accounts_user', 'tracked_accounts', ['user_id', 'platform'])

    op.create_table(
        'social_content',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tracked_account_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('platform', sa.String(), nullable=False),
        sa.Column('platform_post_id', sa.String(), nullable=False),
        sa.Column('content_type', sa.String(), nullable=True),
        sa.Column('caption', sa.Text(), nullable=True),
        sa.Column('hashtags_json', sa.Text(), nullable=True),
        sa.Column('media_urls_json', sa.Text(), nullable=True),
        sa.Column('thumbnail_url', sa.String(), nullable=True),
        sa.Column('likes_count', sa.Integer(), nullable=True),
        sa.Column('comments_count', sa.Integer(), nullable=True),
        sa.Column('views_count', sa.Integer(), nullable=True),
        sa.Column('engagement_rate', sa.String(), nullable=True),
        sa.Column('posted_at', sa.String(), nullable=True),
        sa.Column('fetched_at', sa.String(), nullable=False),
        sa.Column('analysis_summary', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['tracked_account_id'], ['tracked_accounts.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_social_content_account', 'social_content', ['tracked_account_id', 'posted_at'])
    op.create_index('idx_social_content_platform_id', 'social_content', ['platform', 'platform_post_id'], unique=True)


def downgrade() -> None:
    op.drop_index('idx_social_content_platform_id', table_name='social_content')
    op.drop_index('idx_social_content_account', table_name='social_content')
    op.drop_table('social_content')
    op.drop_index('idx_tracked_accounts_user', table_name='tracked_accounts')
    op.drop_table('tracked_accounts')

"""
Background job that periodically fetches new content from tracked social accounts.

Registered in APScheduler via start_scheduler() in scheduler/engine.py.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


def fetch_all_tracked_accounts():
    """
    Called by APScheduler every N hours.
    Iterates all active tracked accounts, fetches new content, updates metadata.
    """
    from src.models.social import (
        list_all_active_tracked_accounts,
        save_content_batch,
        update_account_metadata,
    )
    from src.social import get_social_provider

    try:
        provider = get_social_provider()
    except Exception as e:
        logger.error("Social provider nao disponivel: %s", e)
        return

    accounts = list_all_active_tracked_accounts()
    if not accounts:
        return

    logger.info("Social fetcher: processando %d conta(s)...", len(accounts))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for account in accounts:
        account_id = account["id"]
        platform = account["platform"]
        username = account["username"]
        user_id = account["user_id"]

        try:
            # Fetch profile update
            profile = loop.run_until_complete(
                provider.get_profile(platform, username)
            )
            update_account_metadata(
                account_id,
                display_name=profile.display_name,
                bio=profile.bio,
                followers_count=profile.followers_count,
                posts_count=profile.posts_count,
                profile_pic_url=profile.profile_pic_url,
            )

            # Fetch recent posts
            posts = loop.run_until_complete(
                provider.get_recent_posts(platform, username, limit=20)
            )

            posts_dicts = [
                {
                    "platform_post_id": p.platform_post_id,
                    "content_type": p.content_type,
                    "caption": p.caption,
                    "hashtags": p.hashtags,
                    "media_urls": p.media_urls,
                    "thumbnail_url": p.thumbnail_url,
                    "likes_count": p.likes_count,
                    "comments_count": p.comments_count,
                    "views_count": p.views_count,
                    "engagement_rate": "",
                    "posted_at": p.posted_at,
                }
                for p in posts
            ]

            new_count = save_content_batch(account_id, user_id, platform, posts_dicts)
            logger.info(
                "Social fetcher: @%s/%s — %d novos posts, %d atualizados",
                platform, username, new_count, len(posts_dicts) - new_count,
            )

        except Exception as e:
            logger.error("Social fetcher: erro em @%s/%s: %s", platform, username, e)

    loop.close()
    logger.info("Social fetcher: ciclo completo.")

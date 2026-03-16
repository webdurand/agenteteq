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
    Iterates all active tracked accounts, fetches new posts, stores them.
    Skips accounts whose owner is on a free plan or doesn't have social_monitoring_enabled.
    """
    from src.models.social import (
        list_all_active_tracked_accounts,
        save_content_batch,
    )
    from src.social import get_social_provider
    from src.config.feature_gates import get_user_plan, is_feature_enabled

    accounts = list_all_active_tracked_accounts()
    if not accounts:
        return

    logger.info("Social fetcher: processando %d conta(s)...", len(accounts))

    # Cache providers per platform to avoid re-instantiation
    providers: dict = {}

    # Collect new posts per user for trend detection
    user_new_posts: dict[str, list[dict]] = {}

    # Track skipped free users to avoid repeated plan lookups
    skipped_users: set[str] = set()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # --- Phase 1: Filter eligible accounts and group by (platform, username) ---
    # This ensures each unique social account is fetched from the API only once,
    # even when multiple users track the same account.
    eligible_accounts: list[dict] = []
    for account in accounts:
        user_id = account["user_id"]

        # Skip free users and users without social_monitoring_enabled
        if user_id in skipped_users:
            continue
        plan = get_user_plan(user_id)
        if plan.get("code") == "free" or not is_feature_enabled(user_id, "social_monitoring_enabled"):
            skipped_users.add(user_id)
            logger.debug(
                "Social fetcher: pulando @%s/%s — usuario %s no plano free ou sem social_monitoring_enabled",
                account["platform"], account["username"], user_id[:8],
            )
            continue

        eligible_accounts.append(account)

    # Group eligible accounts by (platform, username) for deduplication
    grouped: dict[tuple[str, str], list[dict]] = {}
    for account in eligible_accounts:
        key = (account["platform"], account["username"])
        grouped.setdefault(key, []).append(account)

    unique_count = len(grouped)
    total_count = len(eligible_accounts)
    if unique_count < total_count:
        logger.info(
            "Social fetcher: %d conta(s) elegivel(is), %d unica(s) — %d chamada(s) de API economizada(s)",
            total_count, unique_count, total_count - unique_count,
        )

    # --- Phase 2: Fetch each unique (platform, username) once ---
    # Cache fetched results: (platform, username) -> posts_dicts or None on error
    fetch_cache: dict[tuple[str, str], list[dict] | None] = {}

    for (platform, username), user_accounts in grouped.items():
        # Get or create provider for this platform
        if platform not in providers:
            try:
                providers[platform] = get_social_provider(platform)
            except Exception as e:
                logger.error("Social provider nao disponivel para %s: %s", platform, e)
                fetch_cache[(platform, username)] = None
                continue
        provider = providers[platform]

        try:
            # Fetch recent posts only (no profile scraping) — ONE call per unique account
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
                    "posted_at": p.posted_at,
                }
                for p in posts
            ]

            fetch_cache[(platform, username)] = posts_dicts
            logger.info(
                "Social fetcher: @%s/%s — %d posts obtidos (compartilhado com %d usuario(s))",
                platform, username, len(posts_dicts), len(user_accounts),
            )

        except Exception as e:
            logger.error("Social fetcher: erro ao buscar @%s/%s: %s", platform, username, e)
            fetch_cache[(platform, username)] = None

    # --- Phase 3: Save posts per user-account pair ---
    for (platform, username), user_accounts in grouped.items():
        posts_dicts = fetch_cache.get((platform, username))
        if posts_dicts is None:
            continue

        for account in user_accounts:
            account_id = account["id"]
            user_id = account["user_id"]

            try:
                new_posts = save_content_batch(account_id, user_id, platform, posts_dicts)
                logger.info(
                    "Social fetcher: @%s/%s [user %s] — %d novos posts, %d atualizados",
                    platform, username, user_id[:8],
                    len(new_posts), len(posts_dicts) - len(new_posts),
                )

                # Collect for trend detection
                if new_posts:
                    if user_id not in user_new_posts:
                        user_new_posts[user_id] = []
                    user_new_posts[user_id].append({
                        "username": username,
                        "platform": platform,
                        "posts": new_posts,
                    })

            except Exception as e:
                logger.error(
                    "Social fetcher: erro ao salvar @%s/%s para user %s: %s",
                    platform, username, user_id[:8], e,
                )

    # Cross-account trend detection
    if user_new_posts:
        try:
            from src.social.trend_detector import detect_and_send_trend_alerts
            detect_and_send_trend_alerts(user_new_posts, loop)
        except Exception as e:
            logger.error("Social fetcher: erro na deteccao de tendencias: %s", e)

    loop.close()
    logger.info("Social fetcher: ciclo completo.")

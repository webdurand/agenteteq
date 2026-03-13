"""
Background job that periodically fetches new content from tracked social accounts.

Registered in APScheduler via start_scheduler() in scheduler/engine.py.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

# Engagement spike threshold: post must have >= SPIKE_MULTIPLIER * avg to trigger alert
SPIKE_MULTIPLIER = 2.0


def fetch_all_tracked_accounts():
    """
    Called by APScheduler every N hours.
    Iterates all active tracked accounts, fetches new content, updates metadata.
    If a new post spikes above average engagement and alerts are enabled, notifies the user.
    """
    from src.models.social import (
        list_all_active_tracked_accounts,
        save_content_batch,
        update_account_metadata,
        get_avg_engagement,
    )
    from src.social import get_social_provider

    accounts = list_all_active_tracked_accounts()
    if not accounts:
        return

    logger.info("Social fetcher: processando %d conta(s)...", len(accounts))

    # Cache providers per platform to avoid re-instantiation
    providers: dict = {}

    # Collect new posts per user for trend detection
    user_new_posts: dict[str, list[dict]] = {}

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for account in accounts:
        account_id = account["id"]
        platform = account["platform"]
        username = account["username"]
        user_id = account["user_id"]
        alerts_on = account.get("alerts_enabled", False)

        # Get or create provider for this platform
        if platform not in providers:
            try:
                providers[platform] = get_social_provider(platform)
            except Exception as e:
                logger.error("Social provider nao disponivel para %s: %s", platform, e)
                continue
        provider = providers[platform]

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

            new_posts = save_content_batch(account_id, user_id, platform, posts_dicts)
            logger.info(
                "Social fetcher: @%s/%s — %d novos posts, %d atualizados",
                platform, username, len(new_posts), len(posts_dicts) - len(new_posts),
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

            # Spike detection — only if alerts enabled and there are new posts
            if alerts_on and new_posts:
                try:
                    avg = get_avg_engagement(account_id)
                    if avg > 0:
                        for post in new_posts:
                            # For YouTube, use views as primary metric; for others, likes
                            engagement = post.get("views_count", 0) if platform == "youtube" else post.get("likes_count", 0)
                            if engagement >= avg * SPIKE_MULTIPLIER:
                                _send_spike_alert(
                                    loop, user_id, username, platform,
                                    post, engagement, avg,
                                )
                except Exception as e:
                    logger.error("Social fetcher: erro ao checar spikes de @%s: %s", username, e)

        except Exception as e:
            logger.error("Social fetcher: erro em @%s/%s: %s", platform, username, e)

    # Cross-account trend detection
    if user_new_posts:
        try:
            from src.social.trend_detector import detect_and_send_trend_alerts
            detect_and_send_trend_alerts(user_new_posts, loop)
        except Exception as e:
            logger.error("Social fetcher: erro na deteccao de tendencias: %s", e)

    loop.close()
    logger.info("Social fetcher: ciclo completo.")


def _send_spike_alert(
    loop: asyncio.AbstractEventLoop,
    user_id: str,
    username: str,
    platform: str,
    post: dict,
    likes: int,
    avg: float,
):
    """Send a proactive WhatsApp alert about a high-engagement post."""
    from src.integrations.whatsapp import whatsapp_client

    multiplier = likes / avg if avg > 0 else 0
    caption_preview = (post.get("caption", "") or "")[:120]
    if len(post.get("caption", "") or "") > 120:
        caption_preview += "..."

    if platform == "youtube":
        metric_label = "Views"
    else:
        metric_label = "Likes"

    body = (
        f"Alerta: @{username} postou algo que ta bombando!\n\n"
        f'"{caption_preview}"\n\n'
        f"{metric_label}: {likes:,} ({multiplier:.0f}x acima da media)\n"
        f"Comentarios: {post.get('comments_count', 0):,}"
    )

    buttons = [
        {"id": "btn_script", "title": "Criar roteiro"},
        {"id": "btn_carousel", "title": "Criar carrossel"},
        {"id": "btn_view", "title": "Ver detalhes"},
    ]

    try:
        loop.run_until_complete(
            whatsapp_client.send_button_message(user_id, body, buttons)
        )
        logger.info(
            "Social alert enviado para %s: @%s post com %d likes (%.0fx avg)",
            user_id[:8], username, likes, multiplier,
        )
    except Exception as e:
        # Fallback to plain text if buttons fail
        logger.warning("Falha ao enviar alert com botoes, tentando texto: %s", e)
        try:
            fallback = (
                f"{body}\n\n"
                "Quer que eu crie conteudo inspirado nesse post? "
                "Responda: roteiro, carrossel ou ver detalhes."
            )
            loop.run_until_complete(
                whatsapp_client.send_text_message(user_id, fallback)
            )
        except Exception as e2:
            logger.error("Social alert: falha total para %s: %s", user_id[:8], e2)

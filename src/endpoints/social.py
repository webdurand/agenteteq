"""
REST API endpoints for social media monitoring.

Allows the web frontend to list/add/remove tracked accounts
and view content without going through the agent chat.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from typing import Optional

from src.auth.deps import get_current_user, require_active_plan
from src.models.social import (
    track_account as db_track_account,
    untrack_account as db_untrack_account,
    list_tracked_accounts,
    get_tracked_account,
    get_top_content,
    get_recent_content,
    save_content_batch,
    update_account_metadata,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/social", tags=["social"])


class AccountCreate(BaseModel):
    platform: str = "instagram"
    username: str


# --- List tracked accounts ---
@router.get("/accounts")
async def api_list_accounts(
    platform: str = Query("", description="Filter by platform"),
    user=Depends(get_current_user),
):
    accounts = list_tracked_accounts(user["phone_number"], platform=platform or None)
    return {"accounts": accounts}


# --- Track a new account ---
@router.post("/accounts")
async def api_track_account(
    body: AccountCreate,
    background_tasks: BackgroundTasks,
    user=Depends(require_active_plan),
):
    from src.social import get_social_provider

    platform = body.platform.lower().strip()
    username = body.username.lstrip("@").strip()
    if platform != "youtube":
        username = username.lower()

    if not username:
        raise HTTPException(status_code=400, detail="Username e obrigatorio.")

    SUPPORTED_PLATFORMS = ["instagram", "youtube"]
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"Plataforma '{platform}' nao suportada. Opcoes: {', '.join(SUPPORTED_PLATFORMS)}",
        )

    try:
        provider = get_social_provider(platform)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Plataforma '{platform}' nao configurada: {e}")

    try:
        profile = await provider.get_profile(platform, username)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

    if profile.metadata.get("is_private"):
        raise HTTPException(status_code=400, detail="Conta privada. So contas publicas podem ser monitoradas.")

    account_id = db_track_account(
        user_id=user["phone_number"],
        platform=platform,
        username=profile.username,
        display_name=profile.display_name,
        profile_url=profile.profile_url,
        profile_pic_url=profile.profile_pic_url,
        bio=profile.bio,
        followers_count=profile.followers_count,
        posts_count=profile.posts_count,
        metadata=profile.metadata,
    )

    # Fetch initial posts in background
    background_tasks.add_task(_fetch_posts_background, account_id, user["phone_number"], platform, username)

    account = get_tracked_account(account_id)
    return {"account": account}


# --- Untrack account ---
@router.delete("/accounts/{account_id}")
async def api_untrack_account(account_id: int, user=Depends(get_current_user)):
    ok = db_untrack_account(account_id, user["phone_number"])
    if not ok:
        raise HTTPException(status_code=404, detail="Conta nao encontrada.")
    return {"ok": True}


# --- Get account content ---
@router.get("/accounts/{account_id}/content")
async def api_get_content(
    account_id: int,
    sort: str = Query("posted_at", description="Sort by: posted_at or likes_count"),
    limit: int = Query(20, ge=1, le=50),
    user=Depends(get_current_user),
):
    account = get_tracked_account(account_id)
    if not account or account["user_id"] != user["phone_number"]:
        raise HTTPException(status_code=404, detail="Conta nao encontrada.")

    if sort == "likes_count":
        posts = get_top_content(account_id, sort_by="likes_count", limit=limit)
    else:
        posts = get_recent_content(account_id, limit=limit)

    return {"posts": posts}


# --- Force refresh ---
@router.post("/accounts/{account_id}/refresh")
async def api_refresh_account(
    account_id: int,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
):
    account = get_tracked_account(account_id)
    if not account or account["user_id"] != user["phone_number"]:
        raise HTTPException(status_code=404, detail="Conta nao encontrada.")

    background_tasks.add_task(
        _fetch_posts_background,
        account_id,
        user["phone_number"],
        account["platform"],
        account["username"],
    )

    return {"ok": True, "message": "Atualizacao iniciada em background."}


# --- helpers ---

def _fetch_posts_background(account_id: int, user_id: str, platform: str, username: str):
    """Fetch posts for an account in background."""
    from src.social import get_social_provider

    try:
        provider = get_social_provider(platform)

        profile = asyncio.run(provider.get_profile(platform, username))
        update_account_metadata(
            account_id,
            display_name=profile.display_name,
            bio=profile.bio,
            followers_count=profile.followers_count,
            posts_count=profile.posts_count,
            profile_pic_url=profile.profile_pic_url,
        )

        posts = asyncio.run(provider.get_recent_posts(platform, username, limit=20))
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
        save_content_batch(account_id, user_id, platform, posts_dicts)
        logger.info("Background fetch concluido para @%s/%s", platform, username)
    except Exception as e:
        logger.error("Erro no background fetch de @%s/%s: %s", platform, username, e)
        try:
            from src.models.social import update_account_status
            update_account_status(account_id, "error", str(e)[:200])
        except Exception:
            pass

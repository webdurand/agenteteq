"""TikTok social provider via Apify scraper."""

import os
import re
import logging
from datetime import datetime, timezone

import httpx

from .base import SocialProvider, SocialProfile, SocialPost

logger = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"
SYNC_TIMEOUT = 120

# Apify actor for TikTok scraping
TIKTOK_SCRAPER_ACTOR = "clockworks~tiktok-scraper"


class TikTokProvider(SocialProvider):

    def __init__(self):
        self.token = os.getenv("APIFY_API_TOKEN", "")
        if not self.token:
            raise ValueError("APIFY_API_TOKEN nao configurado")

    def supported_platforms(self) -> list[str]:
        return ["tiktok"]

    async def get_profile(self, platform: str, username: str) -> SocialProfile:
        """Fetch TikTok profile info via Apify."""
        username = username.lstrip("@").strip()
        url = f"{APIFY_BASE}/acts/{TIKTOK_SCRAPER_ACTOR}/run-sync-get-dataset-items"
        payload = {
            "profiles": [username],
            "resultsPerPage": 1,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
        }

        async with httpx.AsyncClient(timeout=SYNC_TIMEOUT) as client:
            resp = await client.post(
                url,
                params={"token": self.token},
                json=payload,
            )
            resp.raise_for_status()
            items = resp.json()

        if not items:
            raise ValueError(f"Perfil @{username} nao encontrado no TikTok")

        item = items[0]
        author = item.get("authorMeta", {})

        return SocialProfile(
            platform="tiktok",
            username=author.get("name", username),
            display_name=author.get("nickName", ""),
            followers_count=author.get("fans", 0),
            following_count=author.get("following", 0),
            posts_count=author.get("video", 0),
            bio=author.get("signature", ""),
            profile_pic_url=author.get("avatar", ""),
            verified=author.get("verified", False),
        )

    async def get_recent_posts(self, platform: str, username: str, limit: int = 30) -> list[SocialPost]:
        """Fetch recent TikTok posts via Apify."""
        username = username.lstrip("@").strip()
        url = f"{APIFY_BASE}/acts/{TIKTOK_SCRAPER_ACTOR}/run-sync-get-dataset-items"
        payload = {
            "profiles": [username],
            "resultsPerPage": min(limit, 50),
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
        }

        async with httpx.AsyncClient(timeout=SYNC_TIMEOUT) as client:
            resp = await client.post(
                url,
                params={"token": self.token},
                json=payload,
            )
            resp.raise_for_status()
            items = resp.json()

        posts = []
        for item in items[:limit]:
            caption = item.get("text", "")
            hashtags = re.findall(r"#(\w+)", caption)

            posted_at = ""
            ts = item.get("createTimeISO") or item.get("createTime")
            if ts:
                try:
                    if isinstance(ts, (int, float)):
                        posted_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                    else:
                        posted_at = str(ts)
                except Exception:
                    pass

            cover = item.get("covers", {})
            thumb = cover.get("default", "") if isinstance(cover, dict) else ""

            posts.append(SocialPost(
                platform_post_id=str(item.get("id", "")),
                content_type="video",
                caption=caption,
                hashtags=hashtags,
                media_urls=[item.get("videoUrl", "")] if item.get("videoUrl") else [],
                thumbnail_url=thumb,
                likes_count=item.get("diggCount", 0),
                comments_count=item.get("commentCount", 0),
                shares_count=item.get("shareCount", 0),
                saves_count=item.get("collectCount", 0),
                views_count=item.get("playCount", 0),
                posted_at=posted_at,
                video_url=item.get("videoUrl", ""),
                owner_username=username,
                metadata={
                    "music": item.get("musicMeta", {}).get("musicName", ""),
                    "duration": item.get("videoMeta", {}).get("duration", 0),
                },
            ))

        return posts

"""
Apify-based social media provider for Instagram.

Uses the synchronous run endpoint to fetch profile and post data.
Actors used:
  - apify/instagram-profile-scraper (profile info)
  - apify/instagram-scraper (posts)
"""

import logging
import os
import re
from datetime import datetime, timezone

import httpx

from .base import SocialPost, SocialProfile, SocialProvider

logger = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"
SYNC_TIMEOUT = 120  # seconds — Apify sync endpoint limit is ~5 min


class ApifyProvider(SocialProvider):

    def __init__(self):
        self.token = os.getenv("APIFY_API_TOKEN", "")
        if not self.token:
            raise ValueError("APIFY_API_TOKEN nao configurado")

    def supported_platforms(self) -> list[str]:
        return ["instagram"]

    # ──────────────── profile ────────────────

    async def get_profile(self, platform: str, username: str) -> SocialProfile:
        if platform != "instagram":
            raise ValueError(f"Plataforma nao suportada pelo Apify provider: {platform}")

        username = username.lstrip("@").lower()
        data = await self._run_actor(
            "apify/instagram-profile-scraper",
            {"usernames": [username]},
        )

        if not data:
            raise ValueError(f"Perfil @{username} nao encontrado ou conta privada.")

        profile = data[0]
        return SocialProfile(
            username=profile.get("username", username),
            display_name=profile.get("fullName", "") or profile.get("name", ""),
            bio=profile.get("biography", "") or profile.get("bio", ""),
            followers_count=profile.get("followersCount", 0) or profile.get("subscribersCount", 0) or 0,
            following_count=profile.get("followsCount", 0) or profile.get("followingCount", 0) or 0,
            posts_count=profile.get("postsCount", 0) or 0,
            profile_pic_url=profile.get("profilePicUrl", "") or profile.get("profilePicUrlHD", ""),
            profile_url=profile.get("url", f"https://www.instagram.com/{username}/"),
            metadata={
                "is_verified": profile.get("verified", False),
                "is_private": profile.get("private", False),
                "external_url": profile.get("externalUrl", ""),
                "category": profile.get("businessCategoryName", ""),
            },
        )

    # ──────────────── posts ────────────────

    async def get_recent_posts(self, platform: str, username: str, limit: int = 20) -> list[SocialPost]:
        if platform != "instagram":
            raise ValueError(f"Plataforma nao suportada pelo Apify provider: {platform}")

        username = username.lstrip("@").lower()
        data = await self._run_actor(
            "apify/instagram-scraper",
            {
                "directUrls": [f"https://www.instagram.com/{username}/"],
                "resultsType": "posts",
                "resultsLimit": limit,
            },
        )

        posts: list[SocialPost] = []
        for item in (data or []):
            post = self._parse_post(item)
            if post:
                posts.append(post)

        return posts

    async def get_post_by_url(self, post_url: str) -> SocialPost:
        """Fetch a single post by its direct URL via Apify instagram-scraper."""
        data = await self._run_actor(
            "apify/instagram-scraper",
            {
                "directUrls": [post_url],
                "resultsType": "posts",
                "resultsLimit": 1,
            },
        )
        if not data:
            raise ValueError(f"Post nao encontrado: {post_url}")
        post = self._parse_post(data[0])
        if not post:
            raise ValueError(f"Nao foi possivel parsear o post: {post_url}")
        return post

    # ──────────────── parsing ────────────────

    def _parse_post(self, item: dict) -> SocialPost | None:
        """Parse a single Apify post item into a SocialPost dataclass."""
        post_id = item.get("id", "") or item.get("shortCode", "") or str(item.get("pk", ""))
        if not post_id:
            return None

        # Determine content type
        item_type = (item.get("type", "") or "").lower()
        if item_type in ("video", "reel"):
            content_type = "video"
        elif item_type == "carousel" or item.get("childPosts"):
            content_type = "carousel"
        else:
            content_type = "image"

        # Extract caption
        caption = item.get("caption", "") or ""
        if isinstance(caption, dict):
            caption = caption.get("text", "")

        # Extract hashtags from caption
        hashtags = re.findall(r"#(\w+)", caption)

        # Extract media URLs
        media_urls = []
        display_url = item.get("displayUrl", "") or item.get("imageUrl", "")
        if display_url:
            media_urls.append(display_url)
        for child in item.get("childPosts", []) or []:
            child_url = child.get("displayUrl", "") or child.get("imageUrl", "")
            if child_url:
                media_urls.append(child_url)

        # Parse timestamp
        timestamp = item.get("timestamp", "") or item.get("takenAtTimestamp", "")
        if isinstance(timestamp, (int, float)):
            posted_at = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
        elif isinstance(timestamp, str):
            posted_at = timestamp
        else:
            posted_at = ""

        # Extract video URL for reels/videos
        video_url = item.get("videoUrl", "") or item.get("videoPlaybackUrl", "") or ""

        return SocialPost(
            platform_post_id=str(post_id),
            content_type=content_type,
            caption=caption,
            hashtags=hashtags,
            media_urls=media_urls,
            thumbnail_url=display_url,
            likes_count=item.get("likesCount", 0) or 0,
            comments_count=item.get("commentsCount", 0) or 0,
            shares_count=0,
            views_count=item.get("videoViewCount", 0) or item.get("viewCount", 0) or 0,
            posted_at=posted_at,
            video_url=video_url,
            metadata={
                "shortcode": item.get("shortCode", ""),
                "url": item.get("url", ""),
                "location": item.get("locationName", ""),
                "duration": item.get("videoDuration", 0) or 0,
            },
        )

    # ──────────────── internal ────────────────

    async def _run_actor(self, actor_id: str, input_data: dict) -> list[dict]:
        """Run an Apify actor synchronously and return dataset items."""
        url = f"{APIFY_BASE}/acts/{actor_id.replace('/', '~')}/run-sync-get-dataset-items"
        params = {"token": self.token}
        headers = {"Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=SYNC_TIMEOUT) as client:
            resp = await client.post(url, json=input_data, params=params, headers=headers)

            if resp.status_code == 402:
                raise ValueError("Creditos Apify insuficientes. Verifique seu plano em apify.com.")

            if resp.status_code >= 400:
                logger.error("Apify actor %s retornou %s: %s", actor_id, resp.status_code, resp.text[:500])
                raise ValueError(f"Erro ao executar scraper ({resp.status_code}). Tente novamente mais tarde.")

            try:
                return resp.json()
            except Exception:
                logger.error("Apify retornou resposta invalida para %s", actor_id)
                raise ValueError("Resposta invalida do scraper. Tente novamente mais tarde.")

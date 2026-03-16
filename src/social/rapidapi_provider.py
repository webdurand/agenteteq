"""
RapidAPI-based social media provider for Instagram with Apify fallback.

Uses a RapidAPI Instagram scraper as the primary source and falls back
to ApifyProvider when the RapidAPI call fails or returns no data.

Cost tracking:
  - RapidAPI call: ~$0.003 per request
  - Apify fallback: ~$0.10 per request (posts), ~$0.05 (profile)
"""

import logging
import os
import re
from datetime import datetime, timezone

import httpx

from .base import SocialPost, SocialProfile, SocialProvider

logger = logging.getLogger(__name__)

DEFAULT_HOST = "instagram-scraper.p.rapidapi.com"
DEFAULT_POSTS_ENDPOINT = "/v2/posts"
DEFAULT_PROFILE_ENDPOINT = "/v2/profile"
REQUEST_TIMEOUT = 30  # seconds


class RapidAPIProvider(SocialProvider):
    """Instagram provider that queries RapidAPI first and falls back to Apify."""

    def __init__(self):
        self.api_key = os.getenv("RAPIDAPI_KEY", "")
        if not self.api_key:
            raise ValueError("RAPIDAPI_KEY nao configurado")

        self.host = os.getenv("RAPIDAPI_INSTAGRAM_HOST", DEFAULT_HOST)
        self.posts_endpoint = os.getenv(
            "RAPIDAPI_INSTAGRAM_POSTS_ENDPOINT", DEFAULT_POSTS_ENDPOINT
        )
        self.profile_endpoint = os.getenv(
            "RAPIDAPI_INSTAGRAM_PROFILE_ENDPOINT", DEFAULT_PROFILE_ENDPOINT
        )
        self.base_url = f"https://{self.host}"

        # Lazy-initialised Apify fallback
        self._apify: SocialProvider | None = None

    # ──────────────── helpers ────────────────

    def _get_apify(self) -> SocialProvider:
        """Return (and cache) the Apify fallback provider."""
        if self._apify is None:
            from .apify_provider import ApifyProvider

            self._apify = ApifyProvider()
        return self._apify

    def _headers(self) -> dict[str, str]:
        return {
            "X-RapidAPI-Key": self.api_key,
            "X-RapidAPI-Host": self.host,
        }

    def supported_platforms(self) -> list[str]:
        return ["instagram"]

    # ──────────────── cost logging ────────────────

    @staticmethod
    def _log_cost(
        *,
        user_id: str,
        provider: str,
        event_type: str,
        cost_usd: float,
        tool_name: str,
        items_count: int,
        status: str = "success",
        extra: dict | None = None,
    ) -> None:
        try:
            from src.memory.analytics import log_event

            extra_data = {
                "provider": provider,
                "items_count": items_count,
                "cost_usd": cost_usd,
            }
            if extra:
                extra_data.update(extra)
            log_event(
                user_id=user_id,
                channel="api",
                event_type=event_type,
                tool_name=tool_name,
                status=status,
                extra_data=extra_data,
            )
        except Exception as e:
            logger.error("Erro ao logar custo %s: %s", provider, e)

    # ──────────────── profile ────────────────

    async def get_profile(
        self, platform: str, username: str, *, user_id: str | None = None
    ) -> SocialProfile:
        if platform != "instagram":
            raise ValueError(
                f"Plataforma nao suportada pelo RapidAPI provider: {platform}"
            )

        username = username.lstrip("@").lower()

        # --- attempt RapidAPI ---
        try:
            profile = await self._rapidapi_get_profile(username, user_id=user_id)
            return profile
        except Exception as exc:
            logger.warning(
                "RapidAPI profile falhou para @%s (%s), usando Apify fallback",
                username,
                exc,
            )

        # --- Apify fallback ---
        apify = self._get_apify()
        return await apify.get_profile(platform, username)

    async def _rapidapi_get_profile(
        self, username: str, *, user_id: str | None = None
    ) -> SocialProfile:
        url = f"{self.base_url}{self.profile_endpoint}"
        params = {"username_or_id_or_url": username}

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(url, headers=self._headers(), params=params)

        if resp.status_code >= 400:
            logger.error(
                "RapidAPI profile retornou %s: %s",
                resp.status_code,
                resp.text[:500],
            )
            raise ValueError(f"RapidAPI profile error ({resp.status_code})")

        data = resp.json()
        # The top-level payload may wrap the profile in a "data" key.
        profile = data.get("data", data)

        self._log_cost(
            user_id=user_id or "system",
            provider="rapidapi",
            event_type="rapidapi_call",
            cost_usd=0.003,
            tool_name="rapidapi-instagram-profile",
            items_count=1,
        )

        return SocialProfile(
            username=profile.get("username", username),
            display_name=profile.get("full_name", "")
            or profile.get("fullName", ""),
            bio=profile.get("biography", "") or profile.get("bio", ""),
            followers_count=profile.get("follower_count", 0)
            or profile.get("followersCount", 0)
            or 0,
            following_count=profile.get("following_count", 0)
            or profile.get("followsCount", 0)
            or 0,
            posts_count=profile.get("media_count", 0)
            or profile.get("postsCount", 0)
            or 0,
            profile_pic_url=profile.get("profile_pic_url", "")
            or profile.get("profile_pic_url_hd", ""),
            profile_url=f"https://www.instagram.com/{username}/",
            metadata={
                "is_verified": profile.get("is_verified", False),
                "is_private": profile.get("is_private", False),
                "external_url": profile.get("external_url", ""),
                "category": profile.get("category_name", "")
                or profile.get("category", ""),
                "provider": "rapidapi",
            },
        )

    # ──────────────── posts ────────────────

    async def get_recent_posts(
        self,
        platform: str,
        username: str,
        limit: int = 20,
        *,
        user_id: str | None = None,
    ) -> list[SocialPost]:
        if platform != "instagram":
            raise ValueError(
                f"Plataforma nao suportada pelo RapidAPI provider: {platform}"
            )

        username = username.lstrip("@").lower()

        # --- attempt RapidAPI ---
        try:
            posts = await self._rapidapi_get_posts(
                username, limit=limit, user_id=user_id
            )
            if posts:
                return posts
            logger.warning(
                "RapidAPI retornou 0 posts para @%s, tentando Apify fallback",
                username,
            )
        except Exception as exc:
            logger.warning(
                "RapidAPI posts falhou para @%s (%s), usando Apify fallback",
                username,
                exc,
            )

        # --- Apify fallback ---
        return await self._apify_get_posts(platform, username, limit, user_id=user_id)

    async def _rapidapi_get_posts(
        self,
        username: str,
        *,
        limit: int = 20,
        user_id: str | None = None,
    ) -> list[SocialPost]:
        url = f"{self.base_url}{self.posts_endpoint}"
        params: dict = {"username_or_id_or_url": username}
        if limit:
            params["count"] = str(limit)

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(url, headers=self._headers(), params=params)

        if resp.status_code >= 400:
            logger.error(
                "RapidAPI posts retornou %s: %s",
                resp.status_code,
                resp.text[:500],
            )
            raise ValueError(f"RapidAPI posts error ({resp.status_code})")

        body = resp.json()
        # Normalize: response may be a list or {"data": {"items": [...]}}
        items = body
        if isinstance(body, dict):
            items = (
                body.get("data", {}).get("items", [])
                or body.get("data", {}).get("edges", [])
                or body.get("items", [])
                or body.get("edges", [])
                or body.get("data", [])
            )
            if isinstance(items, dict):
                items = items.get("items", []) or items.get("edges", [])

        posts: list[SocialPost] = []
        for item in items or []:
            # Some APIs wrap each item in a "node" key
            node = item.get("node", item) if isinstance(item, dict) else item
            post = self._parse_rapidapi_post(node, owner_username=username)
            if post:
                posts.append(post)

        self._log_cost(
            user_id=user_id or "system",
            provider="rapidapi",
            event_type="rapidapi_call",
            cost_usd=0.003,
            tool_name="rapidapi-instagram-posts",
            items_count=len(posts),
        )

        return posts

    async def _apify_get_posts(
        self,
        platform: str,
        username: str,
        limit: int,
        *,
        user_id: str | None = None,
    ) -> list[SocialPost]:
        """Fallback to Apify for posts. Logs cost with provider=apify."""
        apify = self._get_apify()
        posts = await apify.get_recent_posts(platform, username, limit)

        self._log_cost(
            user_id=user_id or "system",
            provider="apify",
            event_type="apify_call",
            cost_usd=0.10,
            tool_name="apify/instagram-scraper",
            items_count=len(posts),
            extra={"fallback": True},
        )

        return posts

    # ──────────────── post by URL ────────────────

    async def get_post_by_url(self, post_url: str) -> SocialPost:
        """Delegate single-post fetching to Apify (RapidAPI typically lacks this)."""
        apify = self._get_apify()
        return await apify.get_post_by_url(post_url)

    # ──────────────── parsing ────────────────

    def _parse_rapidapi_post(
        self, item: dict, *, owner_username: str = ""
    ) -> SocialPost | None:
        """Map a RapidAPI Instagram post item to the SocialPost dataclass.

        Because different RapidAPI scrapers use slightly different schemas, we
        try multiple field names for each attribute.
        """
        post_id = (
            item.get("id", "")
            or item.get("pk", "")
            or item.get("code", "")
            or item.get("shortcode", "")
        )
        if not post_id:
            return None

        # Content type
        media_type = item.get("media_type", 0) or item.get("type", "")
        if isinstance(media_type, int):
            # Instagram media_type convention: 1=image, 2=video, 8=carousel
            if media_type == 2:
                content_type = "video"
            elif media_type == 8:
                content_type = "carousel"
            else:
                content_type = "image"
        else:
            media_type_str = str(media_type).lower()
            if media_type_str in ("video", "reel"):
                content_type = "video"
            elif media_type_str in ("carousel", "sidecar"):
                content_type = "carousel"
            else:
                content_type = "image"

        # Caption
        caption_obj = item.get("caption", "") or ""
        if isinstance(caption_obj, dict):
            caption = caption_obj.get("text", "")
        else:
            caption = str(caption_obj)

        hashtags = re.findall(r"#(\w+)", caption)

        # Media URLs
        media_urls: list[str] = []
        display_url = (
            item.get("image_versions2", {}).get("candidates", [{}])[0].get("url", "")
            if isinstance(item.get("image_versions2"), dict)
            else ""
        ) or item.get("display_url", "") or item.get("thumbnail_url", "") or item.get("image_url", "")
        if display_url:
            media_urls.append(display_url)

        # Carousel children
        carousel_media = item.get("carousel_media", []) or item.get("edge_sidecar_to_children", {}).get("edges", []) or []
        for child in carousel_media:
            child_node = child.get("node", child) if isinstance(child, dict) else child
            child_url = (
                child_node.get("image_versions2", {}).get("candidates", [{}])[0].get("url", "")
                if isinstance(child_node.get("image_versions2"), dict)
                else ""
            ) or child_node.get("display_url", "") or child_node.get("image_url", "")
            if child_url and child_url not in media_urls:
                media_urls.append(child_url)

        # Timestamp
        taken_at = item.get("taken_at", "") or item.get("taken_at_timestamp", "") or item.get("timestamp", "")
        if isinstance(taken_at, (int, float)):
            posted_at = datetime.fromtimestamp(taken_at, tz=timezone.utc).isoformat()
        elif isinstance(taken_at, str):
            posted_at = taken_at
        else:
            posted_at = ""

        # Video URL
        video_url = (
            item.get("video_url", "")
            or item.get("video_versions", [{}])[0].get("url", "")
            if isinstance(item.get("video_versions"), list) and item.get("video_versions")
            else item.get("video_url", "") or ""
        )

        # Engagement metrics
        likes = (
            item.get("like_count", 0)
            or item.get("edge_media_preview_like", {}).get("count", 0)
            or item.get("likesCount", 0)
            or 0
        )
        comments = (
            item.get("comment_count", 0)
            or item.get("edge_media_to_comment", {}).get("count", 0)
            or item.get("commentsCount", 0)
            or 0
        )
        views = (
            item.get("view_count", 0)
            or item.get("video_view_count", 0)
            or item.get("play_count", 0)
            or item.get("viewCount", 0)
            or 0
        )

        # Owner
        user = item.get("user", {}) or item.get("owner", {}) or {}
        post_owner = user.get("username", "") or owner_username

        # Shortcode / URL
        shortcode = item.get("code", "") or item.get("shortcode", "")
        post_url = (
            item.get("permalink", "")
            or item.get("url", "")
            or (f"https://www.instagram.com/p/{shortcode}/" if shortcode else "")
        )

        return SocialPost(
            platform_post_id=str(post_id),
            content_type=content_type,
            caption=caption,
            hashtags=hashtags,
            media_urls=media_urls,
            thumbnail_url=display_url,
            likes_count=likes,
            comments_count=comments,
            shares_count=0,
            views_count=views,
            posted_at=posted_at,
            video_url=video_url,
            owner_username=post_owner,
            metadata={
                "shortcode": shortcode,
                "url": post_url,
                "location": item.get("location", {}).get("name", "")
                if isinstance(item.get("location"), dict)
                else item.get("location", "") or "",
                "duration": item.get("video_duration", 0)
                or item.get("videoDuration", 0)
                or 0,
                "provider": "rapidapi",
            },
        )

"""
YouTube Data API v3 provider for social monitoring.

Uses the YouTube Data API to fetch channel info and recent videos.
Requires YOUTUBE_API_KEY environment variable.
"""

import logging
import os
from datetime import datetime, timezone

import httpx

from .base import SocialPost, SocialProfile, SocialProvider

logger = logging.getLogger(__name__)

YT_API_BASE = "https://www.googleapis.com/youtube/v3"
REQUEST_TIMEOUT = 30


class YouTubeProvider(SocialProvider):

    def __init__(self):
        self.api_key = os.getenv("YOUTUBE_API_KEY", "")
        if not self.api_key:
            raise ValueError("YOUTUBE_API_KEY nao configurado")

    def supported_platforms(self) -> list[str]:
        return ["youtube"]

    # ──────────────── profile ────────────────

    async def get_profile(self, platform: str, username: str) -> SocialProfile:
        if platform != "youtube":
            raise ValueError(f"Plataforma nao suportada pelo YouTube provider: {platform}")

        username = username.strip().lstrip("@")

        # Try to resolve channel: handle (@username), custom URL, or channel ID
        channel = await self._resolve_channel(username)
        if not channel:
            raise ValueError(f"Canal @{username} nao encontrado no YouTube.")

        snippet = channel.get("snippet", {})
        stats = channel.get("statistics", {})
        thumbnails = snippet.get("thumbnails", {})
        thumb_url = (
            thumbnails.get("medium", {}).get("url")
            or thumbnails.get("default", {}).get("url")
            or ""
        )

        channel_id = channel["id"]
        custom_url = snippet.get("customUrl", "")

        return SocialProfile(
            username=custom_url.lstrip("@") if custom_url else channel_id,
            display_name=snippet.get("title", ""),
            bio=snippet.get("description", "")[:500],
            followers_count=int(stats.get("subscriberCount", 0)),
            following_count=0,  # YouTube doesn't have "following"
            posts_count=int(stats.get("videoCount", 0)),
            profile_pic_url=thumb_url,
            profile_url=f"https://www.youtube.com/channel/{channel_id}",
            metadata={
                "channel_id": channel_id,
                "custom_url": custom_url,
                "country": snippet.get("country", ""),
                "published_at": snippet.get("publishedAt", ""),
                "view_count": int(stats.get("viewCount", 0)),
                "hidden_subscriber_count": stats.get("hiddenSubscriberCount", False),
            },
        )

    # ──────────────── posts (videos) ────────────────

    async def get_recent_posts(self, platform: str, username: str, limit: int = 20) -> list[SocialPost]:
        if platform != "youtube":
            raise ValueError(f"Plataforma nao suportada pelo YouTube provider: {platform}")

        username = username.strip().lstrip("@")

        # Resolve channel to get uploads playlist
        channel = await self._resolve_channel(username)
        if not channel:
            raise ValueError(f"Canal @{username} nao encontrado no YouTube.")

        channel_id = channel["id"]
        uploads_playlist = (
            channel.get("contentDetails", {})
            .get("relatedPlaylists", {})
            .get("uploads")
        )

        if not uploads_playlist:
            return []

        # Get recent video IDs from uploads playlist
        video_ids = await self._get_playlist_video_ids(uploads_playlist, limit)
        if not video_ids:
            return []

        # Get detailed video stats
        videos = await self._get_videos_details(video_ids)

        posts: list[SocialPost] = []
        for video in videos:
            video_id = video["id"]
            snippet = video.get("snippet", {})
            stats = video.get("statistics", {})
            content_details = video.get("contentDetails", {})

            caption = snippet.get("title", "")
            description = snippet.get("description", "")
            if description:
                caption = f"{caption}\n\n{description[:300]}"

            # Extract hashtags from title + description
            import re
            full_text = f"{snippet.get('title', '')} {snippet.get('description', '')}"
            hashtags = re.findall(r"#(\w+)", full_text)

            # Thumbnail
            thumbnails = snippet.get("thumbnails", {})
            thumb_url = (
                thumbnails.get("maxres", {}).get("url")
                or thumbnails.get("high", {}).get("url")
                or thumbnails.get("medium", {}).get("url")
                or thumbnails.get("default", {}).get("url")
                or ""
            )

            # Parse published date
            published = snippet.get("publishedAt", "")
            if published:
                try:
                    dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                    posted_at = dt.isoformat()
                except Exception:
                    posted_at = published
            else:
                posted_at = ""

            # Determine content type from duration
            duration = content_details.get("duration", "")
            content_type = "short" if _is_short(duration) else "video"

            posts.append(SocialPost(
                platform_post_id=video_id,
                content_type=content_type,
                caption=caption,
                hashtags=hashtags,
                media_urls=[thumb_url] if thumb_url else [],
                thumbnail_url=thumb_url,
                likes_count=int(stats.get("likeCount", 0)),
                comments_count=int(stats.get("commentCount", 0)),
                shares_count=0,  # YouTube API doesn't expose shares
                views_count=int(stats.get("viewCount", 0)),
                posted_at=posted_at,
                metadata={
                    "video_id": video_id,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "duration": duration,
                    "tags": snippet.get("tags", [])[:10],
                    "category_id": snippet.get("categoryId", ""),
                },
            ))

        return posts

    # ──────────────── internal ────────────────

    async def _resolve_channel(self, identifier: str) -> dict | None:
        """
        Resolve a YouTube channel by handle, custom URL, or channel ID.
        Returns the channel resource with snippet, statistics, and contentDetails.
        """
        parts = "snippet,statistics,contentDetails"

        # 1. Try by handle (@username) — YouTube Data API v3 supports forHandle
        if not identifier.startswith("UC"):
            data = await self._api_get("channels", {
                "part": parts,
                "forHandle": identifier,
                "maxResults": 1,
            })
            items = data.get("items", [])
            if items:
                return items[0]

        # 2. Try by channel ID (starts with UC)
        if identifier.startswith("UC"):
            data = await self._api_get("channels", {
                "part": parts,
                "id": identifier,
                "maxResults": 1,
            })
            items = data.get("items", [])
            if items:
                return items[0]

        # 3. Fallback: search for the channel
        search_data = await self._api_get("search", {
            "part": "snippet",
            "q": identifier,
            "type": "channel",
            "maxResults": 1,
        })
        search_items = search_data.get("items", [])
        if search_items:
            channel_id = search_items[0].get("snippet", {}).get("channelId") or \
                         search_items[0].get("id", {}).get("channelId")
            if channel_id:
                data = await self._api_get("channels", {
                    "part": parts,
                    "id": channel_id,
                    "maxResults": 1,
                })
                items = data.get("items", [])
                if items:
                    return items[0]

        return None

    async def _get_playlist_video_ids(self, playlist_id: str, limit: int) -> list[str]:
        """Get video IDs from a playlist (typically the uploads playlist)."""
        data = await self._api_get("playlistItems", {
            "part": "contentDetails",
            "playlistId": playlist_id,
            "maxResults": min(limit, 50),
        })

        return [
            item["contentDetails"]["videoId"]
            for item in data.get("items", [])
            if item.get("contentDetails", {}).get("videoId")
        ]

    async def _get_videos_details(self, video_ids: list[str]) -> list[dict]:
        """Get detailed info for a batch of videos."""
        # YouTube API accepts up to 50 IDs per request
        data = await self._api_get("videos", {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(video_ids),
        })
        return data.get("items", [])

    async def _api_get(self, endpoint: str, params: dict) -> dict:
        """Make a GET request to the YouTube Data API."""
        params["key"] = self.api_key
        url = f"{YT_API_BASE}/{endpoint}"

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(url, params=params)

            if resp.status_code == 403:
                error_reason = ""
                try:
                    error_data = resp.json()
                    error_reason = error_data.get("error", {}).get("errors", [{}])[0].get("reason", "")
                except Exception:
                    pass
                if error_reason == "quotaExceeded":
                    raise ValueError("Cota diaria da YouTube API excedida. Tente novamente amanha.")
                raise ValueError(f"Acesso negado pela YouTube API: {error_reason or resp.text[:200]}")

            if resp.status_code >= 400:
                logger.error("YouTube API %s retornou %s: %s", endpoint, resp.status_code, resp.text[:500])
                raise ValueError(f"Erro na YouTube API ({resp.status_code}). Tente novamente mais tarde.")

            return resp.json()


def _is_short(duration: str) -> bool:
    """Check if an ISO 8601 duration represents a YouTube Short (<=60s)."""
    if not duration:
        return False
    # ISO 8601 duration: PT1M30S, PT45S, PT1H2M3S
    import re
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not match:
        return False
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    total_seconds = hours * 3600 + minutes * 60 + seconds
    return total_seconds <= 60

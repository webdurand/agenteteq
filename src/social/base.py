from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class SocialProfile:
    username: str
    display_name: str
    bio: str
    followers_count: int
    following_count: int
    posts_count: int
    profile_pic_url: str
    profile_url: str
    metadata: dict = field(default_factory=dict)


@dataclass
class SocialPost:
    platform_post_id: str
    content_type: str           # image, video, carousel, reel
    caption: str
    hashtags: list[str]
    media_urls: list[str]
    thumbnail_url: str
    likes_count: int
    comments_count: int
    shares_count: int
    views_count: int
    posted_at: str
    metadata: dict = field(default_factory=dict)


class SocialProvider(ABC):
    @abstractmethod
    async def get_profile(self, platform: str, username: str) -> SocialProfile:
        """Fetch profile info for a username on a given platform."""
        ...

    @abstractmethod
    async def get_recent_posts(self, platform: str, username: str, limit: int = 20) -> list[SocialPost]:
        """Fetch recent posts from a profile."""
        ...

    async def get_post_by_url(self, post_url: str) -> SocialPost:
        """Fetch a single post by its direct URL."""
        raise NotImplementedError("get_post_by_url not supported by this provider")

    @abstractmethod
    def supported_platforms(self) -> list[str]:
        """Return list of platforms this provider supports."""
        ...

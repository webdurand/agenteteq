import os

from .base import SocialProvider


def get_social_provider(platform: str = "instagram") -> SocialProvider:
    """Return the appropriate provider for the given platform."""
    platform = platform.lower().strip()

    if platform == "instagram":
        from .apify_provider import ApifyProvider
        return ApifyProvider()
    elif platform == "youtube":
        from .youtube_provider import YouTubeProvider
        return YouTubeProvider()

    raise ValueError(f"Plataforma nao suportada: {platform}")

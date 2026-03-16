import logging
import os

from .base import SocialProvider

logger = logging.getLogger(__name__)


def get_social_provider(platform: str = "instagram") -> SocialProvider:
    """Return the appropriate provider for the given platform.

    For Instagram the selection order is:
      1. RapidAPIProvider  – if RAPIDAPI_KEY is set in the environment
      2. ApifyProvider     – fallback / default

    RapidAPIProvider itself already falls back to Apify on request failures,
    so using it as the primary provider does not sacrifice reliability.
    """
    platform = platform.lower().strip()

    if platform == "instagram":
        # Try RapidAPI first when the key is available
        if os.getenv("RAPIDAPI_KEY"):
            try:
                from .rapidapi_provider import RapidAPIProvider

                return RapidAPIProvider()
            except Exception as exc:
                logger.warning(
                    "RapidAPIProvider nao pode ser inicializado (%s), usando Apify",
                    exc,
                )

        from .apify_provider import ApifyProvider

        return ApifyProvider()

    elif platform == "youtube":
        from .youtube_provider import YouTubeProvider

        return YouTubeProvider()

    raise ValueError(f"Plataforma nao suportada: {platform}")

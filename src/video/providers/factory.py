"""
Factory for video generation providers.
Configure via VIDEO_PROVIDER env var. Default: kling.
"""

import os

from src.video.providers.base import VideoProvider


def get_video_provider() -> VideoProvider:
    """Return the configured video provider instance."""
    provider_name = os.getenv("VIDEO_PROVIDER", "kling").lower()

    if provider_name == "kling":
        from src.video.providers.kling import KlingProvider
        return KlingProvider()
    else:
        raise ValueError(
            f"Unknown VIDEO_PROVIDER: {provider_name}. "
            f"Available: kling"
        )

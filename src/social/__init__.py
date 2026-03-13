import os

from .base import SocialProvider


def get_social_provider() -> SocialProvider:
    provider_name = os.getenv("SOCIAL_PROVIDER", "apify")
    if provider_name == "apify":
        from .apify_provider import ApifyProvider
        return ApifyProvider()
    raise ValueError(f"Social provider nao suportado: {provider_name}")

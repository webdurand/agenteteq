import os

# Re-export base classes from new locations for backward compatibility
from .providers.base import ImageProvider, resolve_aspect_ratio, FORMAT_TO_ASPECT_RATIO
from .providers.nano_banana import NanoBananaProvider, QuotaExhaustedError
from .providers import ProviderRegistry, get_provider_registry


def get_image_provider() -> ImageProvider:
    """
    Retorna o provider padrão. Para fallback automático, use get_provider_registry().
    Mantido para compatibilidade com código existente.
    """
    return get_provider_registry().get_default()

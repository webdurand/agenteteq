import os
from .base import ImageProvider
from .nano_banana import NanoBananaProvider

def get_image_provider() -> ImageProvider:
    provider_name = os.getenv("IMAGE_PROVIDER", "nano_banana")
    
    if provider_name == "nano_banana":
        return NanoBananaProvider()
    
    # Placeholder para futuros providers (ex: openai, fal, replicate)
    # elif provider_name == "openai":
    #     return OpenAIImageProvider()
    
    raise ValueError(f"Provider de imagem não suportado: {provider_name}")

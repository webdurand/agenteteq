"""
Provider Registry — gerencia providers de geração de imagem com fallback.
"""

import os
import logging
from typing import Optional

from .base import ImageProvider

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """
    Registry de providers de imagem com fallback automático.
    Tenta o provider padrão e faz fallback para o próximo em caso de erro.
    """

    _instance: Optional["ProviderRegistry"] = None
    _providers: dict[str, type[ImageProvider]] = {}

    def __init__(self):
        from .nano_banana import NanoBananaProvider
        from .gemini_native import GeminiNativeProvider

        self._providers = {
            "nano_banana": NanoBananaProvider,
            "gemini": GeminiNativeProvider,
        }
        self.default_name = os.getenv("IMAGE_PROVIDER", "nano_banana")

    @classmethod
    def get_instance(cls) -> "ProviderRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_default(self) -> ImageProvider:
        """Retorna instância do provider padrão."""
        provider_class = self._providers.get(self.default_name)
        if not provider_class:
            raise ValueError(f"Provider '{self.default_name}' não encontrado")
        return provider_class()

    async def generate(self, prompt: str, aspect_ratio: str = "1:1", **kwargs) -> bytes:
        """
        Gera imagem usando provider padrão com fallback automático.
        """
        errors = []

        # Tenta provider padrão
        try:
            provider = self.get_default()
            return await provider.generate(prompt, aspect_ratio=aspect_ratio, **kwargs)
        except Exception as e:
            errors.append((self.default_name, e))
            logger.warning("Provider %s falhou: %s", self.default_name, e)

        # Fallback para outros providers
        for name, provider_class in self._providers.items():
            if name == self.default_name:
                continue
            try:
                logger.info("Tentando fallback provider: %s", name)
                provider = provider_class()
                return await provider.generate(prompt, aspect_ratio=aspect_ratio, **kwargs)
            except Exception as e:
                errors.append((name, e))
                logger.warning("Fallback %s também falhou: %s", name, e)

        # Todos falharam
        error_summary = "; ".join(f"{name}: {err}" for name, err in errors)
        raise RuntimeError(f"Todos os providers falharam: {error_summary}")

    async def edit(self, prompt: str, reference_image: bytes, aspect_ratio: str = "1:1", **kwargs) -> bytes:
        """
        Edita imagem usando provider padrão com fallback automático.
        """
        errors = []

        try:
            provider = self.get_default()
            return await provider.edit(prompt, reference_image, aspect_ratio=aspect_ratio, **kwargs)
        except Exception as e:
            errors.append((self.default_name, e))
            logger.warning("Provider %s (edit) falhou: %s", self.default_name, e)

        for name, provider_class in self._providers.items():
            if name == self.default_name:
                continue
            try:
                provider = provider_class()
                return await provider.edit(prompt, reference_image, aspect_ratio=aspect_ratio, **kwargs)
            except Exception as e:
                errors.append((name, e))

        error_summary = "; ".join(f"{name}: {err}" for name, err in errors)
        raise RuntimeError(f"Todos os providers (edit) falharam: {error_summary}")


def get_provider_registry() -> ProviderRegistry:
    """Retorna instância singleton do registry."""
    return ProviderRegistry.get_instance()

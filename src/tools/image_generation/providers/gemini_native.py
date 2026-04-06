"""
Gemini Native Provider — fallback usando google-genai diretamente.

Usa o mesmo SDK (google-genai) mas com modelo diferente do NanoBanana,
servindo como fallback quando o provider primário falha.
"""

import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from google import genai
from google.genai.types import GenerateContentConfig, ImageConfig, Modality, Part
from .base import ImageProvider
import logging

logger = logging.getLogger(__name__)

_executor = None


def _get_executor():
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gemini_native")
    return _executor


class GeminiNativeProvider(ImageProvider):
    """
    Provider de fallback usando Gemini com modelo alternativo.
    """

    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY não configurada")
        self.client = genai.Client(api_key=self.api_key)
        # Usa modelo diferente do NanoBanana para diversificar fallback
        self.model_name = os.getenv("IMAGE_FALLBACK_MODEL", "gemini-2.0-flash-exp")

    async def generate(self, prompt: str, aspect_ratio: str = "1:1") -> bytes:
        loop = asyncio.get_event_loop()

        def _generate():
            logger.info(
                "GeminiNative gerando | modelo=%s | aspect_ratio=%s | prompt=%s...",
                self.model_name, aspect_ratio, prompt[:80],
            )
            config = GenerateContentConfig(
                response_modalities=[Modality.IMAGE],
                image_config=ImageConfig(aspect_ratio=aspect_ratio, image_size="1K"),
            )
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=config,
            )

            candidate = response.candidates[0] if response.candidates else None
            if not candidate or not candidate.content or not candidate.content.parts:
                raise Exception("GeminiNative: resposta sem conteúdo de imagem")

            for part in candidate.content.parts:
                if part.inline_data is not None:
                    return part.inline_data.data

            raise Exception("GeminiNative: resposta sem inline_data")

        return await loop.run_in_executor(_get_executor(), _generate)

    async def edit(self, prompt: str, reference_image: bytes, aspect_ratio: str = "1:1") -> bytes:
        loop = asyncio.get_event_loop()

        def _edit():
            logger.info(
                "GeminiNative editando | modelo=%s | prompt=%s...",
                self.model_name, prompt[:80],
            )
            config = GenerateContentConfig(
                response_modalities=[Modality.IMAGE],
                image_config=ImageConfig(aspect_ratio=aspect_ratio, image_size="1K"),
            )
            image_part = Part.from_bytes(data=reference_image, mime_type="image/png")
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[image_part, prompt],
                config=config,
            )

            candidate = response.candidates[0] if response.candidates else None
            if not candidate or not candidate.content or not candidate.content.parts:
                raise Exception("GeminiNative edit: resposta sem conteúdo")

            for part in candidate.content.parts:
                if part.inline_data is not None:
                    return part.inline_data.data

            raise Exception("GeminiNative edit: resposta sem inline_data")

        return await loop.run_in_executor(_get_executor(), _edit)

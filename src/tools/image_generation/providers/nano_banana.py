import os
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from google import genai
from google.genai.types import GenerateContentConfig, ImageConfig, Modality, Part
from .base import ImageProvider
import logging

_FALLBACK_ENABLED = os.getenv("IMAGE_FALLBACK_ENABLED", "false").lower() == "true"
_MAX_RETRIES = 3
_BASE_DELAY = 2  # seconds

logger = logging.getLogger(__name__)


class QuotaExhaustedError(Exception):
    """Raised when the Gemini API quota is exhausted after all retries."""
    pass

_image_executor = None

def _get_executor():
    global _image_executor
    if _image_executor is None:
        from src.config.system_config import get_config

        max_workers = int(get_config("max_image_workers", "4"))
        _image_executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="gemini_img")
    return _image_executor

def _extract_image_from_response(response) -> bytes:
    """Extrai bytes de imagem da resposta do Gemini."""
    candidate = response.candidates[0] if response.candidates else None
    if not candidate:
        raise Exception(f"API retornou sem candidates. finish_reason={getattr(response, 'prompt_feedback', 'unknown')}")

    content = candidate.content
    if not content or not content.parts:
        raise Exception(f"Conteúdo vazio. finish_reason={getattr(candidate, 'finish_reason', 'unknown')}")

    for part in content.parts:
        if part.inline_data is not None:
            logger.info("Imagem recebida | mime=%s | bytes=%s", part.inline_data.mime_type, len(part.inline_data.data))
            return part.inline_data.data

    raise Exception("Resposta recebida mas sem inline_data de imagem.")

def _is_retryable_error(err_str: str) -> bool:
    return any(k in err_str for k in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE"))

class NanoBananaProvider(ImageProvider):
    """
    Implementação do ImageProvider usando modelos Gemini de geração de imagem.
    Suporta fallback automático para modelo alternativo quando o primário
    retorna 429/503.
    """

    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("GOOGLE_API_KEY ou GEMINI_API_KEY não está configurada.")
        self.client = genai.Client(api_key=self.api_key)
        self.model_name = os.getenv("IMAGE_MODEL", "gemini-3.1-flash-image-preview")
        self.fallback_model = os.getenv("IMAGE_FALLBACK_MODEL", "")

    def _call_api(self, model: str, contents, config):
        """Single API call to a specific model."""
        return self.client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

    def _call_with_retry(self, contents, config, context: str = "generate"):
        """Call Gemini API with exponential backoff on 429/503, then fallback model."""
        last_error = None

        # Try primary model with retries
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return self._call_api(self.model_name, contents, config)
            except Exception as e:
                last_error = e
                err_str = str(e)
                if not _is_retryable_error(err_str):
                    raise
                if attempt < _MAX_RETRIES:
                    delay = _BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "[%s] %s retornou erro retryable (tentativa %s/%s), aguardando %ss: %s",
                        context, self.model_name, attempt + 1, _MAX_RETRIES, delay, err_str[:120],
                    )
                    time.sleep(delay)

        # Primary exhausted — try fallback model if configured
        if self.fallback_model:
            logger.warning(
                "[%s] Modelo primario %s esgotou retries. Tentando fallback: %s",
                context, self.model_name, self.fallback_model,
            )
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    return self._call_api(self.fallback_model, contents, config)
                except Exception as e:
                    last_error = e
                    err_str = str(e)
                    if not _is_retryable_error(err_str):
                        raise
                    if attempt < _MAX_RETRIES:
                        delay = _BASE_DELAY * (2 ** attempt)
                        logger.warning(
                            "[%s] %s retornou erro retryable (tentativa %s/%s), aguardando %ss: %s",
                            context, self.fallback_model, attempt + 1, _MAX_RETRIES, delay, err_str[:120],
                        )
                        time.sleep(delay)

        raise QuotaExhaustedError(
            f"Quota esgotada em todos os modelos após retries: {last_error}"
        ) from last_error

    async def generate(self, prompt: str, aspect_ratio: str = "1:1") -> bytes:
        loop = asyncio.get_event_loop()

        def _generate():
            logger.info("Gerando imagem | modelo=%s | aspect_ratio=%s | prompt=%s...", self.model_name, aspect_ratio, prompt[:80])
            config = GenerateContentConfig(
                response_modalities=[Modality.IMAGE],
                image_config=ImageConfig(aspect_ratio=aspect_ratio, image_size="1K"),
            )
            response = self._call_with_retry(prompt, config, "generate")

            try:
                return _extract_image_from_response(response)
            except Exception:
                if not _FALLBACK_ENABLED:
                    raise
                logger.info("Tentando prompt fallback...")
                fallback_prompt = f"Professional editorial photo for tech blog, clean modern design, {prompt[:120]}"
                response = self._call_with_retry(fallback_prompt, config, "generate_fallback")
                return _extract_image_from_response(response)

        return await loop.run_in_executor(_get_executor(), _generate)

    async def edit(self, prompt: str, reference_image: bytes, aspect_ratio: str = "1:1") -> bytes:
        loop = asyncio.get_event_loop()

        def _edit():
            logger.info("Editando imagem | modelo=%s | aspect_ratio=%s | prompt=%s...", self.model_name, aspect_ratio, prompt[:80])
            config = GenerateContentConfig(
                response_modalities=[Modality.IMAGE],
                image_config=ImageConfig(aspect_ratio=aspect_ratio, image_size="1K"),
            )

            image_part = Part.from_bytes(data=reference_image, mime_type="image/png")
            contents = [image_part, prompt]

            response = self._call_with_retry(contents, config, "edit")
            return _extract_image_from_response(response)

        return await loop.run_in_executor(_get_executor(), _edit)

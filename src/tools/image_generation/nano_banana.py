import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from google import genai
from google.genai.types import GenerateContentConfig, ImageConfig, Modality
from .base import ImageProvider

# Executor dedicado para chamadas à API do Gemini — permite paralelismo real
# entre os slides sem competir com o ThreadPoolExecutor padrão do asyncio
_image_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="gemini_img")


class NanoBananaProvider(ImageProvider):
    """
    Implementação do ImageProvider usando o modelo Gemini 'gemini-3-pro-image-preview'
    (Nano Banana Pro). Usa generate_content() com Modality.IMAGE.
    """

    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("GOOGLE_API_KEY ou GEMINI_API_KEY não está configurada.")
        self.client = genai.Client(api_key=self.api_key)
        self.model_name = "gemini-3-pro-image-preview"

    async def generate(self, prompt: str, aspect_ratio: str = "1:1") -> bytes:
        """
        Gera a imagem via API do Gemini de forma assíncrona usando executor dedicado.
        """
        loop = asyncio.get_event_loop()

        def _generate():
            print(f"[NANO_BANANA] Gerando imagem | modelo={self.model_name} | aspect_ratio={aspect_ratio} | prompt={prompt[:80]}...")
            config = GenerateContentConfig(
                response_modalities=[Modality.TEXT, Modality.IMAGE],
                image_config=ImageConfig(aspect_ratio=aspect_ratio),
            )
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=config,
            )

            candidate = response.candidates[0] if response.candidates else None
            if not candidate:
                raise Exception(f"API retornou sem candidates. finish_reason={getattr(response, 'prompt_feedback', 'unknown')}")

            content = candidate.content
            if not content or not content.parts:
                finish_reason = getattr(candidate, 'finish_reason', 'unknown')
                print(f"[NANO_BANANA] Conteúdo vazio (finish_reason={finish_reason}), tentando prompt fallback...")
                fallback_prompt = f"Professional editorial photo for tech blog, clean modern design, {prompt[:120]}"
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=fallback_prompt,
                    config=config,
                )
                candidate = response.candidates[0] if response.candidates else None
                content = getattr(candidate, 'content', None)
                if not content or not content.parts:
                    raise Exception(f"API bloqueou o conteúdo mesmo após fallback. finish_reason={finish_reason}")

            for part in content.parts:
                if part.inline_data is not None:
                    print(f"[NANO_BANANA] Imagem recebida | mime={part.inline_data.mime_type} | bytes={len(part.inline_data.data)}")
                    return part.inline_data.data

            raise Exception("Resposta recebida mas sem inline_data de imagem.")

        return await loop.run_in_executor(_image_executor, _generate)

import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
from google import genai
from google.genai.types import GenerateContentConfig, Modality
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
            print(f"[NANO_BANANA] Gerando imagem | modelo={self.model_name} | prompt={prompt[:80]}...")
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=GenerateContentConfig(
                    response_modalities=[Modality.TEXT, Modality.IMAGE],
                )
            )
            # Percorre as partes da resposta e extrai os bytes da imagem
            for part in response.candidates[0].content.parts:
                if part.inline_data is not None:
                    print(f"[NANO_BANANA] Imagem recebida | mime={part.inline_data.mime_type} | bytes={len(part.inline_data.data)}")
                    return part.inline_data.data

            raise Exception("Nenhuma imagem retornada pela API do Gemini.")

        return await loop.run_in_executor(_image_executor, _generate)

import os
import struct
from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)

class BaseTTS(ABC):
    @abstractmethod
    async def synthesize(self, text: str) -> tuple[bytes, str]:
        """Converte texto em áudio. Retorna (audio_bytes, mime_type)."""
        pass

class GeminiTTS(BaseTTS):
    """
    TTS via Gemini 2.5 Flash TTS.
    Usa a mesma GOOGLE_API_KEY já configurada — zero custo extra no tier gratuito.
    Retorna PCM linear16 (24kHz, mono) convertido para WAV.
    """

    def __init__(self):
        self.api_key = os.getenv("GOOGLE_API_KEY")
        self.voice = os.getenv("TTS_VOICE", "Aoede")

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        import asyncio
        from google import genai
        from google.genai import types
        from google.genai.errors import ServerError

        client = genai.Client(api_key=self.api_key)

        def _call():
            return client.models.generate_content(
                model="gemini-2.5-flash-preview-tts",
                contents=text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=self.voice
                            )
                        )
                    ),
                ),
            )

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = await asyncio.to_thread(_call)
                part = response.candidates[0].content.parts[0]
                mime = part.inline_data.mime_type
                pcm_data = part.inline_data.data
                logger.info("[TTS GEMINI] mime_type=%s | raw_bytes=%s | type=%s", mime, len(pcm_data), type(pcm_data).__name__)
                return _pcm_to_wav(pcm_data), "audio/wav"
            except ServerError as e:
                wait = 2 ** attempt
                logger.error("[TTS GEMINI] Erro 5xx (tentativa %s/%s): %s — retry em %ss", attempt + 1, max_retries, e, wait)
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(wait)

def get_tts() -> BaseTTS:
    return GeminiTTS()

def _pcm_to_wav(
    pcm_data: bytes,
    sample_rate: int = 24000,
    num_channels: int = 1,
    bits_per_sample: int = 16,
) -> bytes:
    data_size = len(pcm_data)
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header + pcm_data

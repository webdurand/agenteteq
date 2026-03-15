import os
import logging
from abc import ABC, abstractmethod
import httpx

logger = logging.getLogger(__name__)

class BaseTranscriber(ABC):
    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, filename: str = "audio.ogg", user_id: str = None) -> str:
        """Recebe os bytes do áudio e retorna o texto transcrito."""
        pass

class OpenAITranscriber(BaseTranscriber):
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.url = "https://api.openai.com/v1/audio/transcriptions"

    async def transcribe(self, audio_bytes: bytes, filename: str = "audio.ogg", user_id: str = None) -> str:
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY não configurada no .env")

        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }

        # O modelo Whisper da OpenAI requer um nome de arquivo válido com a extensão correta
        files = {
            "file": (filename, audio_bytes, "audio/ogg"),
        }

        data = {
            "model": "whisper-1",
            "language": "pt" # Forçando o idioma para português
        }

        audio_size = len(audio_bytes)

        async with httpx.AsyncClient() as client:
            response = await client.post(self.url, headers=headers, data=data, files=files, timeout=60.0)
            response.raise_for_status()
            result = response.json()
            text = result.get("text", "")

        # Rastrear custo: OGG ~16kbps → bytes/2000 ≈ seconds
        if user_id:
            try:
                from src.memory.analytics import log_event
                duration_seconds = max(1, audio_size / 2000)
                duration_minutes = duration_seconds / 60
                cost_usd = round(duration_minutes * 0.006, 6)
                log_event(
                    user_id=user_id,
                    channel="api",
                    event_type="whisper_transcription",
                    tool_name="whisper-1",
                    status="success",
                    latency_ms=int(duration_seconds * 1000),
                    extra_data={
                        "audio_size_bytes": audio_size,
                        "duration_seconds": round(duration_seconds, 1),
                        "cost_usd": cost_usd,
                    },
                )
            except Exception as e:
                logger.error("Erro ao logar custo Whisper: %s", e)

        return text

class MockTranscriber(BaseTranscriber):
    async def transcribe(self, audio_bytes: bytes, filename: str = "audio.ogg", user_id: str = None) -> str:
        # Apenas para testes sem gastar créditos
        return "Olá, este é um áudio de teste para o blog Diario Teq, validando o fluxo do agente."

def get_transcriber() -> BaseTranscriber:
    provider = os.getenv("TRANSCRIBER_PROVIDER", "mock").lower()
    
    if provider == "openai":
        return OpenAITranscriber()
    else:
        return MockTranscriber()

# Instância global configurada
transcriber = get_transcriber()

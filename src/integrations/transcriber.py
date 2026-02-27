import os
from abc import ABC, abstractmethod
import httpx

class BaseTranscriber(ABC):
    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, filename: str = "audio.ogg") -> str:
        """Recebe os bytes do áudio e retorna o texto transcrito."""
        pass

class OpenAITranscriber(BaseTranscriber):
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.url = "https://api.openai.com/v1/audio/transcriptions"

    async def transcribe(self, audio_bytes: bytes, filename: str = "audio.ogg") -> str:
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

        async with httpx.AsyncClient() as client:
            response = await client.post(self.url, headers=headers, data=data, files=files, timeout=60.0)
            response.raise_for_status()
            result = response.json()
            return result.get("text", "")

class MockTranscriber(BaseTranscriber):
    async def transcribe(self, audio_bytes: bytes, filename: str = "audio.ogg") -> str:
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

import os
import httpx
from typing import Optional

class WhatsAppClient:
    def __init__(self):
        self.api_token = os.getenv("WHATSAPP_API_TOKEN")
        self.phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        self.base_url = "https://graph.facebook.com/v18.0"

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    async def send_text_message(self, to_number: str, text: str, reply_to_message_id: Optional[str] = None) -> dict:
        """
        Envia uma mensagem de texto para um número.
        """
        url = f"{self.base_url}/{self.phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_number,
            "type": "text",
            "text": {"body": text},
        }
        
        if reply_to_message_id:
            payload["context"] = {"message_id": reply_to_message_id}

        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=self._get_headers())
            response.raise_for_status()
            return response.json()

    async def mark_message_as_read_and_typing(self, message_id: str, is_audio: bool = False) -> Optional[dict]:
        """
        Marca a mensagem como lida e exibe um indicador de "digitando..." ou "gravando áudio...".
        O indicador dura até 25 segundos ou até uma nova mensagem ser enviada.
        """
        url = f"{self.base_url}/{self.phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
            "typing_indicator": {
                "type": "audio" if is_audio else "text"
            }
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, headers=self._get_headers())
                response.raise_for_status()
                return response.json()
        except Exception as e:
            print(f"Erro ao enviar typing indicator: {e}")
            return None

    async def get_media_url(self, media_id: str) -> Optional[str]:
        """
        Obtém a URL de download de uma mídia a partir de seu ID.
        """
        url = f"{self.base_url}/{media_id}"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self._get_headers())
            response.raise_for_status()
            data = response.json()
            return data.get("url")

    async def download_media(self, media_url: str) -> bytes:
        """
        Faz o download do conteúdo da mídia.
        """
        # A API de mídia exige apenas o Authorization bearer
        headers = {"Authorization": f"Bearer {self.api_token}"}
        async with httpx.AsyncClient() as client:
            response = await client.get(media_url, headers=headers)
            response.raise_for_status()
            return response.content

# Instância padrão para uso
whatsapp_client = WhatsAppClient()

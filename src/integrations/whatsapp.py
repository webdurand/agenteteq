import os
import httpx
from typing import Optional, Protocol

class WhatsAppProvider(Protocol):
    async def send_text_message(self, to_number: str, text: str, reply_to_message_id: Optional[str] = None) -> dict:
        ...

    async def mark_message_as_read_and_typing(self, message_id: str, to_number: str, is_audio: bool = False) -> Optional[dict]:
        ...

    async def get_media_url(self, media_id_or_data: str) -> Optional[str]:
        ...

    async def download_media(self, media_url_or_base64: str) -> bytes:
        ...

class MetaWhatsAppClient:
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
        Envia uma mensagem de texto para um número usando a API Oficial da Meta.
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

    async def mark_message_as_read_and_typing(self, message_id: str, to_number: str, is_audio: bool = False) -> Optional[dict]:
        """
        Marca a mensagem como lida e exibe um indicador de "digitando..." ou "gravando áudio...".
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
            print(f"Erro ao enviar typing indicator (Meta): {e}")
            return None

    async def get_media_url(self, media_id: str) -> Optional[str]:
        """
        Obtém a URL de download de uma mídia a partir de seu ID na API da Meta.
        """
        url = f"{self.base_url}/{media_id}"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self._get_headers())
            response.raise_for_status()
            data = response.json()
            return data.get("url")

    async def download_media(self, media_url: str) -> bytes:
        """
        Faz o download do conteúdo da mídia da Meta API.
        """
        headers = {"Authorization": f"Bearer {self.api_token}"}
        async with httpx.AsyncClient() as client:
            response = await client.get(media_url, headers=headers)
            response.raise_for_status()
            return response.content

class EvolutionWhatsAppClient:
    def __init__(self):
        self.api_url = os.getenv("EVOLUTION_API_URL")
        self.api_token = os.getenv("EVOLUTION_API_TOKEN")
        self.instance_name = os.getenv("EVOLUTION_INSTANCE_NAME")

    def _get_headers(self) -> dict:
        return {
            "apikey": self.api_token,
            "Content-Type": "application/json",
        }

    async def send_text_message(self, to_number: str, text: str, reply_to_message_id: Optional[str] = None) -> dict:
        """
        Envia uma mensagem de texto usando a Evolution API.
        """
        url = f"{self.api_url}/message/sendText/{self.instance_name}"
        payload = {
            "number": to_number,
            "text": text,
            "delay": 1200 # Um pequeno delay para parecer mais humano
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=self._get_headers())
            response.raise_for_status()
            return response.json()

    async def mark_message_as_read_and_typing(self, message_id: str, to_number: str, is_audio: bool = False) -> Optional[dict]:
        """
        Envia indicador de digitando/gravando e tenta marcar como lido na Evolution API.
        """
        try:
            async with httpx.AsyncClient() as client:
                # Marcar como lido
                read_url = f"{self.api_url}/chat/markMessageAsRead/{self.instance_name}"
                read_payload = {
                    "readMessages": [
                        {
                            "remoteJid": to_number if "@" in to_number else f"{to_number}@s.whatsapp.net",
                            "fromMe": False,
                            "id": message_id
                        }
                    ]
                }
                # Não fazemos raise_for_status aqui porque pode falhar se a config da API estiver diferente e não queremos travar o fluxo
                await client.post(read_url, json=read_payload, headers=self._get_headers())
                
                # Enviar presence (digitando/gravando)
                presence_url = f"{self.api_url}/chat/sendPresence/{self.instance_name}"
                presence_payload = {
                    "number": to_number,
                    "delay": 5000,
                    "presence": "recording" if is_audio else "composing"
                }
                
                response = await client.post(presence_url, json=presence_payload, headers=self._get_headers())
                response.raise_for_status()
                return response.json()
        except Exception as e:
            print(f"Erro ao enviar typing indicator (Evolution): {e}")
            return None

    async def get_media_url(self, media_base64: str) -> Optional[str]:
        """
        No caso da Evolution API, para simplificar e suportar webhook_base64,
        esperamos receber a string base64 inteira do webhook.
        """
        return media_base64

    async def download_media(self, media_base64: str) -> bytes:
        """
        Decodifica o base64 da mídia recebido do webhook da Evolution.
        """
        import base64
        try:
            if media_base64.startswith("data:"):
                # Remove o header "data:audio/ogg;base64,"
                media_base64 = media_base64.split(",", 1)[1]
            return base64.b64decode(media_base64)
        except Exception as e:
            print(f"Erro ao decodificar mídia (Evolution): {e}")
            return b""


def get_whatsapp_client() -> WhatsAppProvider:
    provider = os.getenv("WHATSAPP_PROVIDER", "meta").lower()
    if provider == "evolution":
        return EvolutionWhatsAppClient()
    return MetaWhatsAppClient()

# Instância padrão para uso
whatsapp_client = get_whatsapp_client()

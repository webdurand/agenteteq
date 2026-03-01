import os
import httpx
from typing import Optional


class StatusNotifier:
    """
    Envia mensagens determinísticas de status para o WhatsApp de forma síncrona.
    
    Usa httpx síncrono porque as tools do Agno rodam dentro de agent.run() (síncrono).
    Suporta Meta e Evolution API via WHATSAPP_PROVIDER (mesmo padrão do projeto).
    Reutilizável por qualquer feature que precise de feedback intermediário ao usuário.
    """

    def __init__(self, to_number: str, reply_to_message_id: Optional[str] = None):
        self.to_number = to_number
        self.reply_to_message_id = reply_to_message_id
        self.provider = os.getenv("WHATSAPP_PROVIDER", "meta").lower()
        # Rastreia mensagens já enviadas nesta conversa para evitar duplicatas.
        # Como a instância é compartilhada por todas as tools da mesma requisição,
        # a deduplicação é automática independente de qual tool notifica.
        self._sent_messages: set[str] = set()

    def notify(self, message: str) -> None:
        """
        Envia mensagem de status síncrona via WhatsApp (Meta ou Evolution).
        Mensagens idênticas são enviadas apenas uma vez por conversa.
        """
        if message in self._sent_messages:
            return
        self._sent_messages.add(message)
        try:
            if self.provider == "evolution":
                self._send_evolution(message)
            else:
                self._send_meta(message)
        except Exception as e:
            print(f"[NOTIFIER] Falha ao enviar status '{message[:40]}...': {e}")

    def _send_meta(self, message: str) -> None:
        api_token = os.getenv("WHATSAPP_API_TOKEN")
        phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self.to_number,
            "type": "text",
            "text": {"body": message},
        }
        if self.reply_to_message_id:
            payload["context"] = {"message_id": self.reply_to_message_id}

        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

        with httpx.Client() as client:
            response = client.post(url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()

    def _send_evolution(self, message: str) -> None:
        api_url = os.getenv("EVOLUTION_API_URL")
        api_token = os.getenv("EVOLUTION_API_TOKEN")
        instance_name = os.getenv("EVOLUTION_INSTANCE_NAME")
        url = f"{api_url}/message/sendText/{instance_name}"

        payload = {
            "number": self.to_number,
            "text": message,
            "delay": 500,
        }

        headers = {
            "apikey": api_token,
            "Content-Type": "application/json",
        }

        with httpx.Client() as client:
            response = client.post(url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()

from datetime import datetime, timezone, timedelta
from typing import Optional, List
import json
import os

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from src.memory.integrations import get_user_integrations
import logging

logger = logging.getLogger(__name__)

def _get_google_credentials(user_phone: str, provider: str) -> Optional[Credentials]:
    """Recupera as credenciais do Google para o usuario dado um provider especifico."""
    integrations = get_user_integrations(user_phone, provider=provider, include_tokens=True)
    if not integrations:
        return None
    
    conn = integrations[0]
    
    return Credentials(
        token=conn.get("access_token"),
        refresh_token=conn.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
        scopes=conn.get("scopes", [])
    )

def create_google_tools(user_phone: str):
    """
    Factory que cria as tools do Google Workspace com o user_phone pre-injetado.
    """

    def read_emails(
        max_results: int = 10,
        query: str = "is:unread"
    ) -> str:
        """
        Lê e-mails do Gmail do usuário.
        Requer que o usuário tenha conectado a integração do Google.

        Args:
            max_results: Número máximo de e-mails para buscar (padrão 10).
            query: Query de busca do Gmail (ex: "is:unread", "from:chefe@empresa.com", "newer_than:1d"). Padrão é ler não lidos.

        Returns:
            Lista formatada com os e-mails encontrados ou mensagem de erro/aviso.
        """
        creds = _get_google_credentials(user_phone, "gmail")
        if not creds:
            return "Usuário não conectou o Gmail. Peça para conectar em Configurações > Integrações."

        try:
            service = build('gmail', 'v1', credentials=creds)
            results = service.users().messages().list(userId='me', q=query, maxResults=max_results).execute()
            messages = results.get('messages', [])

            if not messages:
                return f"Nenhum e-mail encontrado para a busca: '{query}'"

            output = []
            for msg_ref in messages:
                msg = service.users().messages().get(userId='me', id=msg_ref['id'], format='metadata').execute()
                headers = msg.get('payload', {}).get('headers', [])
                
                subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '(Sem Assunto)')
                sender = next((h['value'] for h in headers if h['name'] == 'From'), '(Desconhecido)')
                date = next((h['value'] for h in headers if h['name'] == 'Date'), '')
                snippet = msg.get('snippet', '')
                
                output.append(f"De: {sender}\nData: {date}\nAssunto: {subject}\nResumo: {snippet}\n---")

            return "\n".join(output)
        except Exception as e:
            logger.error("Erro ao ler e-mails para %s: %s", user_phone, e)
            return f"Erro ao acessar Gmail: {str(e)}"


    def get_calendar_events(
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 10
    ) -> str:
        """
        Busca os próximos eventos na Agenda do Google do usuário.
        Requer que o usuário tenha conectado a integração do Google.

        Args:
            time_min: Data/hora inicial no formato ISO 8601 (ex: "2026-03-01T00:00:00Z"). Se vazio, usa agora.
            time_max: Data/hora final no formato ISO 8601. Se vazio, não tem limite superior.
            max_results: Número máximo de eventos para retornar.

        Returns:
            Lista de eventos formatada.
        """
        creds = _get_google_credentials(user_phone, "google_calendar")
        if not creds:
            return "Usuário não conectou o Google Calendar. Peça para conectar em Configurações > Integrações."

        try:
            service = build('calendar', 'v3', credentials=creds)
            
            if not time_min:
                time_min = datetime.now(timezone.utc).isoformat()
                
            events_result = service.events().list(
                calendarId='primary', timeMin=time_min, timeMax=time_max,
                maxResults=max_results, singleEvents=True,
                orderBy='startTime').execute()
            
            events = events_result.get('items', [])

            if not events:
                return "Nenhum evento futuro encontrado na sua agenda."

            output = []
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                summary = event.get('summary', '(Sem Título)')
                output.append(f"[{start}] {summary}")

            return "\n".join(output)
        except Exception as e:
            logger.error("Erro ao buscar eventos para %s: %s", user_phone, e)
            return f"Erro ao acessar Google Calendar: {str(e)}"


    def create_calendar_event(
        summary: str,
        start_time: str,
        end_time: str,
        description: str = "",
        location: str = ""
    ) -> str:
        """
        Cria um novo evento na Agenda do Google do usuário.
        Requer que o usuário tenha conectado a integração do Google.

        Args:
            summary: Título do evento.
            start_time: Data/hora de início no formato ISO 8601 (ex: "2026-03-08T10:00:00-03:00"). O timezone é muito importante.
            end_time: Data/hora de término no formato ISO 8601.
            description: Descrição do evento (opcional).
            location: Local do evento (opcional).

        Returns:
            Mensagem de sucesso com link para o evento ou erro.
        """
        creds = _get_google_credentials(user_phone, "google_calendar")
        if not creds:
            return "Usuário não conectou o Google Calendar. Peça para conectar em Configurações > Integrações."

        try:
            service = build('calendar', 'v3', credentials=creds)
            
            event = {
                'summary': summary,
                'location': location,
                'description': description,
                'start': {
                    'dateTime': start_time,
                },
                'end': {
                    'dateTime': end_time,
                },
            }

            event = service.events().insert(calendarId='primary', body=event).execute()
            return f"Evento criado com sucesso! Link: {event.get('htmlLink')}"
        except Exception as e:
            logger.error("Erro ao criar evento para %s: %s", user_phone, e)
            return f"Erro ao criar evento no Google Calendar: {str(e)}"

    return read_emails, get_calendar_events, create_calendar_event

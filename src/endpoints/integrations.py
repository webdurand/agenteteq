from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
import os

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request

from src.auth.deps import get_current_user
from src.memory.integrations import get_user_integrations, delete_integration, upsert_integration
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])

# Modelos Pydantic
class IntegrationResponse(BaseModel):
    id: int
    provider: str
    account_id: Optional[str]
    account_email: Optional[str]
    scopes: List[str]
    created_at: Optional[str]

class AvailableProvider(BaseModel):
    id: str
    name: str
    description: str
    icon: str

class ConnectIntegrationRequest(BaseModel):
    provider: str
    code: str

# Configuracoes Google Oauth
def get_google_client_config():
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    
    if not client_id or not client_secret:
        return None
        
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        }
    }

PROVIDER_SCOPES = {
    "gmail": ["openid", "email", "profile", "https://www.googleapis.com/auth/gmail.readonly"],
    "google_calendar": ["openid", "email", "profile", "https://www.googleapis.com/auth/calendar", "https://www.googleapis.com/auth/calendar.events"],
}

@router.get("/available", response_model=List[AvailableProvider])
async def get_available_integrations(user: dict = Depends(get_current_user)):
    """Retorna a lista de provedores que o usuario pode conectar no sistema."""
    return [
        {
            "id": "gmail",
            "name": "Gmail",
            "description": "Permite que o Teq leia e pesquise seus e-mails.",
            "icon": "gmail"
        },
        {
            "id": "google_calendar",
            "name": "Google Calendar",
            "description": "Permite que o Teq veja e crie eventos na sua agenda.",
            "icon": "google_calendar"
        }
    ]

@router.get("/", response_model=List[IntegrationResponse])
async def list_integrations(user: dict = Depends(get_current_user)):
    """Retorna todas as contas conectadas do usuario."""
    return get_user_integrations(user["phone_number"])

@router.post("/", response_model=IntegrationResponse)
async def connect_integration(req: ConnectIntegrationRequest, user: dict = Depends(get_current_user)):
    """Recebe um authorization code, troca por tokens e salva no banco."""
    if req.provider not in PROVIDER_SCOPES:
        raise HTTPException(status_code=400, detail="Provedor não suportado atualmente.")

    client_config = get_google_client_config()
    if not client_config:
        raise HTTPException(status_code=500, detail="Configuração de OAuth do Google ausente no backend.")

    scopes = PROVIDER_SCOPES[req.provider]

    try:
        flow = Flow.from_client_config(
            client_config,
            scopes=scopes,
            redirect_uri="postmessage" 
        )
        
        flow.fetch_token(code=req.code)
        credentials = flow.credentials

        from googleapiclient.discovery import build

        service = build("oauth2", "v2", credentials=credentials)
        user_info = service.userinfo().get().execute()

        account_id = user_info.get("id")
        account_email = user_info.get("email")

        integration = upsert_integration(
            phone_number=user["phone_number"],
            provider=req.provider,
            account_id=account_id,
            account_email=account_email,
            access_token=credentials.token,
            refresh_token=credentials.refresh_token,
            scopes=",".join(credentials.scopes) if credentials.scopes else "",
            expires_at=credentials.expiry
        )
        
        return integration

    except Exception as e:
        logger.error("Erro ao trocar code no Google OAuth para %s: %s", req.provider, e)
        raise HTTPException(status_code=400, detail=f"Erro ao validar código com Google: {str(e)}")

@router.delete("/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_integration(integration_id: int, user: dict = Depends(get_current_user)):
    """Desconecta uma conta especifica."""
    success = delete_integration(integration_id, user["phone_number"])
    if not success:
        raise HTTPException(status_code=404, detail="Integração não encontrada ou não pertence a você.")
    return None

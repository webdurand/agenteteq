import os
from typing import Optional, List
from datetime import datetime

from sqlalchemy.orm import Session
from src.db.session import get_db
from src.db.models import UserIntegration, User

def get_user_integrations(phone_number: str, provider: Optional[str] = None, include_tokens: bool = False) -> List[dict]:
    """Retorna todas as integracoes de um usuario. Se provider for passado, filtra por ele.
    Se include_tokens=True, inclui access_token e refresh_token (somente para uso interno do backend).
    """
    try:
        with get_db() as session:
            query = session.query(UserIntegration).filter(UserIntegration.user_id == phone_number)
            if provider:
                query = query.filter(UserIntegration.provider == provider)
            
            integrations = query.all()
            results = []
            for i in integrations:
                d = i.to_dict()
                if include_tokens:
                    d["access_token"] = i.access_token
                    d["refresh_token"] = i.refresh_token
                results.append(d)
            return results
    except Exception as e:
        print(f"[INTEGRATIONS] Erro ao buscar integracoes do usuario {phone_number}: {e}")
        return []

def get_integration_by_id(integration_id: int, phone_number: str) -> Optional[dict]:
    """Busca uma integracao especifica garantindo que pertence ao usuario"""
    try:
        with get_db() as session:
            integration = session.query(UserIntegration).filter(
                UserIntegration.id == integration_id,
                UserIntegration.user_id == phone_number
            ).first()
            
            # Precisamos retornar os tokens para o backend usar nas tools
            if integration:
                data = integration.to_dict()
                data["access_token"] = integration.access_token
                data["refresh_token"] = integration.refresh_token
                return data
            return None
    except Exception as e:
        print(f"[INTEGRATIONS] Erro ao buscar integracao {integration_id}: {e}")
        return None

def upsert_integration(
    phone_number: str,
    provider: str,
    account_id: str,
    account_email: str,
    access_token: str,
    refresh_token: Optional[str],
    scopes: str,
    expires_at: Optional[datetime] = None
) -> dict:
    """Cria ou atualiza uma conexao baseada no account_id do provedor"""
    try:
        with get_db() as session:
            integration = session.query(UserIntegration).filter(
                UserIntegration.user_id == phone_number,
                UserIntegration.provider == provider,
                UserIntegration.account_id == account_id
            ).first()
            
            if integration:
                integration.account_email = account_email
                integration.access_token = access_token
                # Só atualiza refresh_token se ele vier no request (Google as vezes nao manda se ja tem)
                if refresh_token:
                    integration.refresh_token = refresh_token
                integration.scopes = scopes
                if expires_at:
                    integration.expires_at = expires_at
            else:
                integration = UserIntegration(
                    user_id=phone_number,
                    provider=provider,
                    account_id=account_id,
                    account_email=account_email,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    scopes=scopes,
                    expires_at=expires_at
                )
                session.add(integration)
                
            session.commit()
            return integration.to_dict()
    except Exception as e:
        print(f"[INTEGRATIONS] Erro ao salvar integracao {provider} para {phone_number}: {e}")
        raise e

def delete_integration(integration_id: int, phone_number: str) -> bool:
    """Remove uma integracao garantindo que pertence ao usuario"""
    try:
        with get_db() as session:
            integration = session.query(UserIntegration).filter(
                UserIntegration.id == integration_id,
                UserIntegration.user_id == phone_number
            ).first()
            
            if integration:
                session.delete(integration)
                session.commit()
                return True
            return False
    except Exception as e:
        print(f"[INTEGRATIONS] Erro ao deletar integracao {integration_id}: {e}")
        return False

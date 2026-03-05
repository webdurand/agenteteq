import os
from google.oauth2 import id_token
from google.auth.transport import requests

def get_google_client_id() -> str:
    return os.getenv("GOOGLE_CLIENT_ID", "")

def verify_google_token(token: str) -> dict:
    """
    Verifica o token de ID do Google usando o SDK oficial.
    Retorna um dicionario com os dados do usuario (email, name, google_id)
    Levanta ValueError se o token for invalido.
    """
    client_id = get_google_client_id()
    if not client_id:
        raise ValueError("GOOGLE_CLIENT_ID não configurado no backend.")

    try:
        idinfo = id_token.verify_oauth2_token(token, requests.Request(), client_id, clock_skew_in_seconds=10)

        email = idinfo.get("email")
        if not email:
            raise ValueError("Token do Google não contém e-mail.")

        return {
            "email": email,
            "name": idinfo.get("name", ""),
            "google_id": idinfo.get("sub", "")
        }

    except ValueError as e:
        raise ValueError(f"Token do Google inválido: {str(e)}")
    except Exception as e:
        raise ValueError(f"Erro ao verificar token do Google: {str(e)}")

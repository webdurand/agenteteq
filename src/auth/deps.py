from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from src.auth.jwt import decode_token
from src.memory.identity import get_user, is_plan_active

security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """
    Decodifica o token JWT e busca o usuario no banco de dados.
    """
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token ausente")
        
    token = credentials.credentials
    payload = decode_token(token)
    
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token invalido ou expirado")
        
    phone_number = payload.get("sub")
    if not phone_number:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token malformado")
        
    user = get_user(phone_number)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario nao encontrado")
        
    return user

def require_active_plan(user: dict = Depends(get_current_user)) -> dict:
    """
    Garante que o usuario tem um plano ativo ou que o trial nao expirou.
    """
    if not is_plan_active(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Plano ou trial expirado")
    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """
    Garante que o usuario tem role 'admin'.
    """
    if user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso negado")
    return user

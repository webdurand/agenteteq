import os
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional

JWT_SECRET = os.getenv("JWT_SECRET", "")
if not JWT_SECRET:
    import warnings
    warnings.warn("JWT_SECRET not set — using insecure default for development only", stacklevel=2)
    JWT_SECRET = "dev-only-insecure-secret-do-not-use-in-production"
ALGORITHM = "HS256"

def create_token(phone_number: str, username: str, email: str, role: str = "user") -> str:
    """
    Cria um JWT com validade de 30 dias.
    O 'sub' (subject) e o phone_number, para manter compatibilidade com
    a logica de session_id baseada no numero.
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=30)
    
    payload = {
        "sub": phone_number,
        "username": username,
        "email": email,
        "role": role,
        "iat": now,
        "exp": expire
    }
    
    encoded_jwt = jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)
    return encoded_jwt

def decode_token(token: str) -> Optional[dict]:
    """
    Decodifica e valida um JWT.
    Retorna o payload dict se valido, ou None se invalido/expirado.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None

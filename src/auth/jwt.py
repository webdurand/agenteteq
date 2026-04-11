import os
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional

JWT_SECRET = os.getenv("JWT_SECRET", "")
if not JWT_SECRET:
    if os.getenv("ENV", "production").lower() in ("dev", "development", "local", "test"):
        import warnings
        warnings.warn("JWT_SECRET not set — using insecure default for development only", stacklevel=2)
        JWT_SECRET = "dev-only-insecure-secret-do-not-use-in-production"
    else:
        raise RuntimeError(
            "FATAL: JWT_SECRET environment variable is not set. "
            "Set JWT_SECRET before starting the application in production."
        )
ALGORITHM = "HS256"

ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30"))


def create_token(phone_number: str, username: str, email: str, role: str = "user") -> str:
    """
    Cria um access token JWT com validade curta (default 1h).
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    payload = {
        "sub": phone_number,
        "username": username,
        "email": email,
        "role": role,
        "type": "access",
        "iat": now,
        "exp": expire,
    }

    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def create_refresh_token(phone_number: str) -> str:
    """
    Cria um refresh token com validade longa (default 30 dias).
    Contém apenas o subject — dados do usuario são buscados no refresh.
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    payload = {
        "sub": phone_number,
        "type": "refresh",
        "iat": now,
        "exp": expire,
    }

    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """
    Decodifica e valida um JWT.
    Retorna o payload dict se valido, ou None se invalido/expirado.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return {"_error": "expired"}
    except jwt.PyJWTError:
        return None

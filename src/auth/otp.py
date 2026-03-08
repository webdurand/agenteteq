import os
import random
import string
from datetime import datetime, timedelta, timezone

from src.db.session import get_db
from src.db.models import OtpCode

def get_expiry_seconds() -> int:
    return int(os.getenv("OTP_EXPIRY_SECONDS", "120"))

def generate_code(phone_number: str, purpose: str) -> str:
    """
    Gera um codigo de 6 caracteres alfanumericos em uppercase, 
    associado a um proposito (ex: 'register', 'login_2fa').
    Retorna o codigo gerado.
    """
    characters = string.ascii_uppercase + string.digits
    code = ''.join(random.choice(characters) for _ in range(6))
    
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=get_expiry_seconds())
    
    with get_db() as session:
        existing = session.get(OtpCode, phone_number)
        if existing:
            existing.code = code
            existing.purpose = purpose
            existing.attempts = 0
            existing.expires_at = expires_at
        else:
            session.add(OtpCode(
                phone_number=phone_number,
                code=code,
                purpose=purpose,
                attempts=0,
                expires_at=expires_at,
            ))
    
    return code

def verify_code(phone_number: str, code: str, purpose: str) -> bool:
    """
    Verifica se o codigo bate com o guardado para o numero e proposito.
    Invalida apos uso com sucesso ou 3 tentativas falhas ou tempo expirado.
    """
    with get_db() as session:
        record = session.get(OtpCode, phone_number)
        
        if not record:
            return False
            
        if record.purpose != purpose:
            return False
            
        expires = record.expires_at if record.expires_at.tzinfo else record.expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires:
            session.delete(record)
            return False
            
        record.attempts = (record.attempts or 0) + 1
        
        # Compara ignorando case
        if record.code.upper() == code.upper():
            # Consome o codigo
            session.delete(record)
            return True
            
        # Mais de 3 tentativas, invalida
        if record.attempts >= 3:
            session.delete(record)
            
        return False


def cleanup_expired_codes():
    """Remove codigos expirados. Chamado periodicamente pelo scheduler."""
    with get_db() as session:
        session.query(OtpCode).filter(
            OtpCode.expires_at < datetime.now(timezone.utc)
        ).delete(synchronize_session=False)

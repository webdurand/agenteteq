import os
import random
import string
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple, Optional

# Estrutura in-memory: 
# { "phone_number": { "code": "A1B2C3", "expires_at": datetime, "attempts": 0, "purpose": "register" } }
_otp_store: Dict[str, dict] = {}

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
    
    _otp_store[phone_number] = {
        "code": code,
        "expires_at": expires_at,
        "attempts": 0,
        "purpose": purpose
    }
    
    return code

def verify_code(phone_number: str, code: str, purpose: str) -> bool:
    """
    Verifica se o codigo bate com o guardado para o numero e proposito.
    Invalida apos uso com sucesso ou 3 tentativas falhas ou tempo expirado.
    """
    record = _otp_store.get(phone_number)
    
    if not record:
        return False
        
    if record["purpose"] != purpose:
        return False
        
    if datetime.now(timezone.utc) > record["expires_at"]:
        _otp_store.pop(phone_number, None)
        return False
        
    record["attempts"] += 1
    
    # Compara ignorando case
    if record["code"].upper() == code.upper():
        # Consome o codigo
        _otp_store.pop(phone_number, None)
        return True
        
    # Mais de 3 tentativas, invalida
    if record["attempts"] >= 3:
        _otp_store.pop(phone_number, None)
        
    return False

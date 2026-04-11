"""
Encrypt / decrypt sensitive strings at rest using Fernet (AES-128-CBC).
Key is derived from TOKEN_ENCRYPTION_KEY env var.
If the env var is missing, encryption is a no-op (plaintext passthrough)
so existing deployments don't break until the key is set.
"""
import base64
import hashlib
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_fernet = None


def _get_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet

    key = os.getenv("TOKEN_ENCRYPTION_KEY", "")
    if not key:
        if os.getenv("ENV", "").lower() == "production":
            logger.error("TOKEN_ENCRYPTION_KEY obrigatório em produção! Tokens NÃO serão criptografados.")
        return None

    # Derive a 32-byte key via SHA-256, then base64-encode for Fernet
    derived = hashlib.sha256(key.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(derived)

    from cryptography.fernet import Fernet
    _fernet = Fernet(fernet_key)
    return _fernet


def encrypt(value: Optional[str]) -> Optional[str]:
    """Encrypt a string. Returns prefixed ciphertext or plaintext if no key."""
    if not value:
        return value
    f = _get_fernet()
    if f is None:
        return value
    return "enc:" + f.encrypt(value.encode()).decode()


def decrypt(value: Optional[str]) -> Optional[str]:
    """Decrypt a string. Handles both encrypted (enc: prefix) and legacy plaintext."""
    if not value:
        return value
    if not value.startswith("enc:"):
        return value  # legacy plaintext, return as-is
    f = _get_fernet()
    if f is None:
        logger.warning("TOKEN_ENCRYPTION_KEY not set but found encrypted token")
        return None
    try:
        return f.decrypt(value[4:].encode()).decode()
    except Exception as e:
        logger.error("Failed to decrypt token: %s", e)
        return None

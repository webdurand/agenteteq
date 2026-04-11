"""
Password hashing with Argon2id (OWASP recommended 2024+).
Maintains backward compatibility with existing bcrypt hashes.
"""
import bcrypt

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, InvalidHashError
    _ph = PasswordHasher()
    _HAS_ARGON2 = True
except ImportError:
    _HAS_ARGON2 = False


def hash_password(password: str) -> str:
    """Gera hash Argon2id (preferido) ou bcrypt (fallback)."""
    if _HAS_ARGON2:
        return _ph.hash(password)
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verifica senha contra hash Argon2id ou bcrypt (backward compat).
    Detecta automaticamente o formato do hash.
    """
    if not hashed_password:
        return False

    # Argon2 hashes start with $argon2
    if hashed_password.startswith("$argon2") and _HAS_ARGON2:
        try:
            return _ph.verify(hashed_password, plain_password)
        except (VerifyMismatchError, InvalidHashError):
            return False

    # bcrypt hashes start with $2b$ or $2a$
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except (ValueError, TypeError):
        return False


def needs_rehash(hashed_password: str) -> bool:
    """Retorna True se o hash deveria ser atualizado para Argon2id."""
    if not _HAS_ARGON2:
        return False
    if not hashed_password:
        return False
    # bcrypt hashes should be rehashed to Argon2id
    if hashed_password.startswith("$2b$") or hashed_password.startswith("$2a$"):
        return True
    # Check if Argon2 parameters need updating
    try:
        return _ph.check_needs_rehash(hashed_password)
    except Exception:
        return False

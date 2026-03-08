"""Tests for token encryption/decryption."""
import os
import importlib


def test_encrypt_decrypt_roundtrip():
    os.environ["TOKEN_ENCRYPTION_KEY"] = "test-key-for-encryption"
    # Force re-init of fernet
    import src.auth.crypto as crypto
    crypto._fernet = None
    
    original = "ya29.a0AfH6SMBx-some-google-access-token"
    encrypted = crypto.encrypt(original)
    
    assert encrypted.startswith("enc:")
    assert encrypted != original
    
    decrypted = crypto.decrypt(encrypted)
    assert decrypted == original


def test_decrypt_legacy_plaintext():
    """Existing plaintext tokens should pass through unchanged."""
    import src.auth.crypto as crypto
    
    plaintext = "ya29.a0AfH6SMBx-legacy-token"
    assert crypto.decrypt(plaintext) == plaintext


def test_encrypt_none_returns_none():
    import src.auth.crypto as crypto
    assert crypto.encrypt(None) is None
    assert crypto.decrypt(None) is None


def test_encrypt_empty_returns_empty():
    import src.auth.crypto as crypto
    assert crypto.encrypt("") == ""
    assert crypto.decrypt("") == ""


def test_no_key_passthrough():
    """Without TOKEN_ENCRYPTION_KEY, encrypt is a no-op."""
    os.environ.pop("TOKEN_ENCRYPTION_KEY", None)
    import src.auth.crypto as crypto
    crypto._fernet = None
    
    token = "ya29.some-token"
    assert crypto.encrypt(token) == token
    assert crypto.decrypt(token) == token


def test_different_keys_fail():
    """Decrypting with wrong key should fail gracefully."""
    os.environ["TOKEN_ENCRYPTION_KEY"] = "key-one"
    import src.auth.crypto as crypto
    crypto._fernet = None
    
    encrypted = crypto.encrypt("secret-token")
    
    # Switch key
    os.environ["TOKEN_ENCRYPTION_KEY"] = "key-two"
    crypto._fernet = None
    
    result = crypto.decrypt(encrypted)
    assert result is None  # Should fail gracefully

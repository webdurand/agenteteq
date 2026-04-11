"""Tests for JWT access and refresh token creation, decoding, and validation."""
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest

from src.auth.jwt import (
    ALGORITHM,
    JWT_SECRET,
    create_refresh_token,
    create_token,
    decode_token,
)


# ---------------------------------------------------------------------------
# Access token creation
# ---------------------------------------------------------------------------


class TestCreateAccessToken:
    def test_creates_valid_jwt_string(self):
        token = create_token("+5511999990001", "alice", "alice@example.com")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_payload_contains_expected_claims(self):
        token = create_token("+5511999990001", "alice", "alice@example.com", role="user")
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        assert payload["sub"] == "+5511999990001"
        assert payload["username"] == "alice"
        assert payload["email"] == "alice@example.com"
        assert payload["role"] == "user"
        assert payload["type"] == "access"
        assert "iat" in payload
        assert "exp" in payload

    def test_default_role_is_user(self):
        token = create_token("+5511999990001", "alice", "alice@example.com")
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        assert payload["role"] == "user"

    def test_custom_role(self):
        token = create_token("+5511999990001", "admin_user", "admin@example.com", role="admin")
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        assert payload["role"] == "admin"

    def test_expiry_is_in_the_future(self):
        token = create_token("+5511999990001", "alice", "alice@example.com")
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        assert payload["exp"] > payload["iat"]

    def test_different_users_produce_different_tokens(self):
        t1 = create_token("+5511999990001", "alice", "alice@example.com")
        t2 = create_token("+5511999990002", "bob", "bob@example.com")
        assert t1 != t2


# ---------------------------------------------------------------------------
# Refresh token creation
# ---------------------------------------------------------------------------


class TestCreateRefreshToken:
    def test_creates_valid_jwt_string(self):
        token = create_refresh_token("+5511999990001")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_payload_has_only_sub_and_type(self):
        token = create_refresh_token("+5511999990001")
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        assert payload["sub"] == "+5511999990001"
        assert payload["type"] == "refresh"
        # Should NOT have username/email/role
        assert "username" not in payload
        assert "email" not in payload
        assert "role" not in payload

    def test_expiry_is_set(self):
        token = create_refresh_token("+5511999990001")
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        assert "exp" in payload
        assert payload["exp"] > payload["iat"]


# ---------------------------------------------------------------------------
# Token decoding — valid tokens
# ---------------------------------------------------------------------------


class TestDecodeToken:
    def test_decode_valid_access_token(self):
        token = create_token("+5511999990001", "alice", "alice@example.com")
        payload = decode_token(token)
        assert payload is not None
        assert payload["sub"] == "+5511999990001"
        assert payload["type"] == "access"

    def test_decode_valid_refresh_token(self):
        token = create_refresh_token("+5511999990001")
        payload = decode_token(token)
        assert payload is not None
        assert payload["sub"] == "+5511999990001"
        assert payload["type"] == "refresh"


# ---------------------------------------------------------------------------
# Token decoding — invalid / expired tokens
# ---------------------------------------------------------------------------


class TestDecodeInvalidToken:
    def test_garbage_string_returns_none(self):
        result = decode_token("not.a.jwt")
        assert result is None

    def test_empty_string_returns_none(self):
        result = decode_token("")
        assert result is None

    def test_wrong_secret_returns_none(self):
        token = pyjwt.encode(
            {"sub": "x", "type": "access", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            "wrong-secret",
            algorithm=ALGORITHM,
        )
        result = decode_token(token)
        assert result is None

    def test_expired_token_returns_error_dict(self):
        expired_payload = {
            "sub": "+5511999990001",
            "type": "access",
            "iat": datetime.now(timezone.utc) - timedelta(hours=2),
            "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
        }
        token = pyjwt.encode(expired_payload, JWT_SECRET, algorithm=ALGORITHM)
        result = decode_token(token)
        assert result is not None
        assert result.get("_error") == "expired"

    def test_wrong_algorithm_returns_none(self):
        token = pyjwt.encode(
            {"sub": "x", "type": "access", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            JWT_SECRET,
            algorithm="HS384",
        )
        # decode_token enforces HS256 only
        result = decode_token(token)
        assert result is None


# ---------------------------------------------------------------------------
# Token type validation
# ---------------------------------------------------------------------------


class TestTokenTypeValidation:
    def test_access_token_has_type_access(self):
        token = create_token("+5511999990001", "alice", "alice@example.com")
        payload = decode_token(token)
        assert payload["type"] == "access"

    def test_refresh_token_has_type_refresh(self):
        token = create_refresh_token("+5511999990001")
        payload = decode_token(token)
        assert payload["type"] == "refresh"

    def test_access_and_refresh_tokens_are_distinguishable(self):
        access = create_token("+5511999990001", "alice", "alice@example.com")
        refresh = create_refresh_token("+5511999990001")
        access_payload = decode_token(access)
        refresh_payload = decode_token(refresh)
        assert access_payload["type"] != refresh_payload["type"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_unicode_username(self):
        token = create_token("+5511999990001", "joao_silva", "joao@example.com")
        payload = decode_token(token)
        assert payload["username"] == "joao_silva"

    def test_special_chars_in_email(self):
        token = create_token("+5511999990001", "user", "user+tag@example.com")
        payload = decode_token(token)
        assert payload["email"] == "user+tag@example.com"

    @pytest.mark.parametrize("phone", ["+5511999990001", "+1234567890", "+44123456789"])
    def test_various_phone_formats(self, phone):
        token = create_token(phone, "user", "user@example.com")
        payload = decode_token(token)
        assert payload["sub"] == phone

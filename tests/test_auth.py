"""Tests for auth-related functions (password hash, identity)."""
from src.memory.identity import (
    create_user_full,
    get_user,
    get_user_by_email,
    get_user_by_username,
    get_password_hash,
    get_password_hash_by_email,
)
from src.auth.passwords import hash_password, verify_password


def _create_test_user(phone="+5500000000100", email="test@example.com", username="testuser"):
    hashed = hash_password("SecurePass123!")
    create_user_full(
        phone_number=phone,
        username=username,
        name="Test User",
        email=email,
        birth_date="2000-01-01",
        password_hash=hashed,
    )
    return phone, email, username


def test_create_and_get_user():
    phone, email, username = _create_test_user()
    user = get_user(phone)
    assert user is not None
    assert user["phone_number"] == phone
    assert user["email"] == email
    assert user["username"] == username
    assert user["name"] == "Test User"


def test_password_hash_not_in_to_dict():
    phone, email, _ = _create_test_user()
    user = get_user(phone)
    assert "password_hash" not in user


def test_get_password_hash_by_phone():
    phone, _, _ = _create_test_user()
    h = get_password_hash(phone)
    assert h is not None
    assert verify_password("SecurePass123!", h) is True


def test_get_password_hash_by_email():
    _, email, _ = _create_test_user()
    h = get_password_hash_by_email(email)
    assert h is not None
    assert verify_password("SecurePass123!", h) is True


def test_wrong_password_fails():
    _, email, _ = _create_test_user()
    h = get_password_hash_by_email(email)
    assert verify_password("WrongPass!", h) is False


def test_get_user_by_email():
    phone, email, _ = _create_test_user()
    user = get_user_by_email(email)
    assert user is not None
    assert user["phone_number"] == phone


def test_get_user_by_username():
    phone, _, username = _create_test_user()
    user = get_user_by_username(username)
    assert user is not None
    assert user["phone_number"] == phone


def test_nonexistent_user_returns_none():
    assert get_user("+5599999999999") is None
    assert get_user_by_email("nobody@example.com") is None
    assert get_user_by_username("nobody") is None
    assert get_password_hash("+5599999999999") is None
    assert get_password_hash_by_email("nobody@example.com") is None

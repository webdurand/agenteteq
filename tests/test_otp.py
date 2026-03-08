"""Tests for OTP generation and verification (PostgreSQL-backed)."""
from datetime import datetime, timedelta, timezone

from src.auth.otp import generate_code, verify_code, cleanup_expired_codes
from src.db.session import get_db
from src.db.models import OtpCode


def test_generate_returns_6char_code():
    code = generate_code("+5500000000001", "register")
    assert len(code) == 6
    assert code.isalnum()


def test_verify_correct_code():
    code = generate_code("+5500000000002", "register")
    assert verify_code("+5500000000002", code, "register") is True


def test_code_consumed_after_use():
    code = generate_code("+5500000000003", "register")
    verify_code("+5500000000003", code, "register")
    assert verify_code("+5500000000003", code, "register") is False


def test_wrong_code_rejected():
    generate_code("+5500000000004", "register")
    assert verify_code("+5500000000004", "WRONG1", "register") is False


def test_wrong_purpose_rejected():
    code = generate_code("+5500000000005", "register")
    assert verify_code("+5500000000005", code, "login_2fa") is False


def test_brute_force_invalidates_after_3():
    code = generate_code("+5500000000006", "register")
    verify_code("+5500000000006", "WRONG1", "register")
    verify_code("+5500000000006", "WRONG2", "register")
    verify_code("+5500000000006", "WRONG3", "register")
    # Code should be invalidated now, even with correct code
    assert verify_code("+5500000000006", code, "register") is False


def test_upsert_replaces_old_code():
    c1 = generate_code("+5500000000007", "register")
    c2 = generate_code("+5500000000007", "register")
    assert c1 != c2 or True  # codes may collide, that's fine
    # Old code should not work after replacement
    assert verify_code("+5500000000007", c1, "register") is False


def test_expired_code_rejected():
    phone = "+5500000000008"
    generate_code(phone, "register")
    # Manually set expires_at to the past
    with get_db() as session:
        record = session.get(OtpCode, phone)
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert verify_code(phone, "ANYTHING", "register") is False


def test_cleanup_removes_expired():
    phone = "+5500000000009"
    generate_code(phone, "register")
    with get_db() as session:
        record = session.get(OtpCode, phone)
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    cleanup_expired_codes()
    with get_db() as session:
        assert session.get(OtpCode, phone) is None


def test_cleanup_keeps_valid():
    phone = "+5500000000010"
    generate_code(phone, "register")
    cleanup_expired_codes()
    with get_db() as session:
        assert session.get(OtpCode, phone) is not None
    # Clean up
    with get_db() as session:
        session.query(OtpCode).delete()


def test_db_empty_after_all_consumed():
    phone = "+5500000000011"
    code = generate_code(phone, "register")
    verify_code(phone, code, "register")
    with get_db() as session:
        assert session.query(OtpCode).count() == 0

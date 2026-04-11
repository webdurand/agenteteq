"""Tests for identity management: phone change, account deletion (LGPD), plan status, sessions."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.auth.passwords import hash_password
from src.db.models import (
    ChatMessage,
    OtpCode,
    Reminder,
    Subscription,
    Task,
    UsageEvent,
    User,
)
from src.db.session import get_db
from src.memory.identity import (
    change_user_phone_number,
    create_user_full,
    delete_account,
    get_or_rotate_session,
    get_user,
    is_new_session,
    is_plan_active,
    promote_user_to_admin,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(phone="+5500000002000", **kwargs):
    defaults = dict(
        username=f"u_{phone[-4:]}",
        name="Test User",
        email=f"u_{phone[-4:]}@test.com",
        birth_date="2000-01-01",
        password_hash=hash_password("Pass123!"),
    )
    defaults.update(kwargs)
    create_user_full(phone_number=phone, **defaults)
    return phone


def _add_chat_message(user_id, text="hello"):
    with get_db() as session:
        session.add(ChatMessage(
            user_id=user_id,
            session_id=f"{user_id}_test",
            role="user",
            text=text,
        ))


def _add_task(user_id, title="Test Task"):
    with get_db() as session:
        session.add(Task(
            user_id=user_id,
            title=title,
            created_at=datetime.now(timezone.utc).isoformat(),
        ))


def _add_reminder(user_id, title="Test Reminder"):
    with get_db() as session:
        session.add(Reminder(
            user_id=user_id,
            title=title,
            task_instructions="do something",
            trigger_type="one_time",
            trigger_config="{}",
            created_at=datetime.now(timezone.utc).isoformat(),
        ))


def _add_usage_event(user_id, event_type="message_sent"):
    with get_db() as session:
        session.add(UsageEvent(
            user_id=user_id,
            channel="test",
            event_type=event_type,
            status="success",
            created_at=datetime.now(timezone.utc).isoformat(),
        ))


def _add_otp_code(phone):
    with get_db() as session:
        session.add(OtpCode(
            phone_number=phone,
            code="ABC123",
            purpose="register",
            attempts=0,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        ))


# ---------------------------------------------------------------------------
# change_user_phone_number
# ---------------------------------------------------------------------------


class TestChangeUserPhoneNumber:
    def test_updates_user_phone(self):
        old = _make_user("+5500000002001")
        change_user_phone_number(old, "+5500000002901")
        assert get_user("+5500000002901") is not None
        assert get_user(old) is None

    def test_migrates_chat_messages(self):
        old = _make_user("+5500000002002")
        _add_chat_message(old, "before migration")
        change_user_phone_number(old, "+5500000002902")
        with get_db() as session:
            msgs = session.query(ChatMessage).filter_by(user_id="+5500000002902").all()
            assert len(msgs) == 1
            assert msgs[0].text == "before migration"

    def test_migrates_tasks(self):
        old = _make_user("+5500000002003")
        _add_task(old, "important task")
        change_user_phone_number(old, "+5500000002903")
        with get_db() as session:
            tasks = session.query(Task).filter_by(user_id="+5500000002903").all()
            assert len(tasks) == 1

    def test_migrates_otp_code(self):
        old = _make_user("+5500000002004")
        _add_otp_code(old)
        change_user_phone_number(old, "+5500000002904")
        with get_db() as session:
            otp = session.get(OtpCode, "+5500000002904")
            assert otp is not None
            old_otp = session.get(OtpCode, old)
            assert old_otp is None

    def test_migrates_usage_events(self):
        old = _make_user("+5500000002005")
        _add_usage_event(old)
        change_user_phone_number(old, "+5500000002905")
        with get_db() as session:
            events = session.query(UsageEvent).filter_by(user_id="+5500000002905").all()
            assert len(events) == 1

    def test_rejects_duplicate_new_phone(self):
        _make_user("+5500000002006")
        _make_user("+5500000002906", username="other", email="other@test.com")
        with pytest.raises(ValueError, match="[Jj]a cadastrado"):
            change_user_phone_number("+5500000002006", "+5500000002906")

    def test_migrates_reminders(self):
        old = _make_user("+5500000002007")
        _add_reminder(old, "my reminder")
        change_user_phone_number(old, "+5500000002907")
        with get_db() as session:
            reminders = session.query(Reminder).filter_by(user_id="+5500000002907").all()
            assert len(reminders) == 1


# ---------------------------------------------------------------------------
# delete_account (LGPD)
# ---------------------------------------------------------------------------


class TestDeleteAccount:
    def test_deletes_user_record(self):
        phone = _make_user("+5500000002010")
        result = delete_account(phone)
        assert result is True
        assert get_user(phone) is None

    def test_nonexistent_user_returns_false(self):
        result = delete_account("+5599999999998")
        assert result is False

    def test_deletes_chat_messages(self):
        phone = _make_user("+5500000002011")
        _add_chat_message(phone)
        delete_account(phone)
        with get_db() as session:
            assert session.query(ChatMessage).filter_by(user_id=phone).count() == 0

    def test_deletes_tasks(self):
        phone = _make_user("+5500000002012")
        _add_task(phone)
        delete_account(phone)
        with get_db() as session:
            assert session.query(Task).filter_by(user_id=phone).count() == 0

    def test_deletes_reminders(self):
        phone = _make_user("+5500000002013")
        _add_reminder(phone)
        delete_account(phone)
        with get_db() as session:
            assert session.query(Reminder).filter_by(user_id=phone).count() == 0

    def test_deletes_usage_events(self):
        phone = _make_user("+5500000002014")
        _add_usage_event(phone)
        delete_account(phone)
        with get_db() as session:
            assert session.query(UsageEvent).filter_by(user_id=phone).count() == 0

    def test_deletes_otp_codes(self):
        phone = _make_user("+5500000002015")
        _add_otp_code(phone)
        delete_account(phone)
        with get_db() as session:
            assert session.get(OtpCode, phone) is None

    def test_cancels_stripe_subscription(self):
        phone = _make_user("+5500000002016")
        # Give the user a stripe customer id
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone).first()
            user.stripe_customer_id = "cus_test123"
        # Add a subscription
        with get_db() as session:
            session.add(Subscription(
                user_id=phone,
                plan_code="pro",
                provider_customer_id="cus_test123",
                provider_subscription_id="sub_test123",
                status="active",
            ))
        with patch("src.integrations.stripe.cancel_subscription") as mock_cancel:
            delete_account(phone)
            mock_cancel.assert_called_once_with("sub_test123", immediately=True)

    def test_deletes_all_data_comprehensive(self):
        """Ensure delete_account removes ALL user-related data (LGPD compliance)."""
        phone = _make_user("+5500000002017")
        _add_chat_message(phone)
        _add_task(phone)
        _add_reminder(phone)
        _add_usage_event(phone)
        _add_otp_code(phone)
        delete_account(phone)
        with get_db() as session:
            assert session.query(User).filter_by(phone_number=phone).first() is None
            assert session.query(ChatMessage).filter_by(user_id=phone).count() == 0
            assert session.query(Task).filter_by(user_id=phone).count() == 0
            assert session.query(Reminder).filter_by(user_id=phone).count() == 0
            assert session.query(UsageEvent).filter_by(user_id=phone).count() == 0
            assert session.get(OtpCode, phone) is None


# ---------------------------------------------------------------------------
# is_plan_active
# ---------------------------------------------------------------------------


class TestIsPlanActive:
    def test_admin_always_active(self):
        user = {"phone_number": "+5500000002020", "role": "admin", "plan_type": "paid"}
        assert is_plan_active(user) is True

    def test_free_plan_always_active(self):
        user = {"phone_number": "+5500000002021", "role": "user", "plan_type": "free"}
        assert is_plan_active(user) is True

    def test_trial_plan_always_active(self):
        user = {"phone_number": "+5500000002022", "role": "user", "plan_type": "trial"}
        assert is_plan_active(user) is True

    def test_paid_plan_active_subscription(self):
        user = {"phone_number": "+5500000002023", "role": "user", "plan_type": "paid"}
        with patch("src.billing.service.is_subscription_active", return_value=True):
            assert is_plan_active(user) is True

    def test_paid_plan_expired_subscription(self):
        user = {"phone_number": "+5500000002024", "role": "user", "plan_type": "paid"}
        with patch("src.billing.service.is_subscription_active", return_value=False):
            assert is_plan_active(user) is False

    @pytest.mark.parametrize("plan_type", ["free", "trial"])
    def test_free_or_trial_always_active(self, plan_type):
        user = {"phone_number": "+5500000002025", "role": "user", "plan_type": plan_type}
        assert is_plan_active(user) is True


# ---------------------------------------------------------------------------
# get_or_rotate_session
# ---------------------------------------------------------------------------


class TestGetOrRotateSession:
    def test_returns_existing_session_id(self):
        phone = _make_user("+5500000002030")
        # Set an existing session
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone).first()
            user.current_session_id = f"{phone}_abc12345"
        session_id = get_or_rotate_session(phone)
        assert session_id == f"{phone}_abc12345"

    def test_creates_new_session_when_none_exists(self):
        phone = _make_user("+5500000002031")
        session_id = get_or_rotate_session(phone)
        assert session_id.startswith(phone)
        assert "_" in session_id
        assert len(session_id) > len(phone)

    def test_force_new_creates_new_session(self):
        phone = _make_user("+5500000002032")
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone).first()
            user.current_session_id = f"{phone}_old00000"
        old_session = f"{phone}_old00000"
        new_session = get_or_rotate_session(phone, force_new=True)
        assert new_session != old_session
        assert new_session.startswith(phone)

    def test_session_persists_in_db(self):
        phone = _make_user("+5500000002033")
        session_id = get_or_rotate_session(phone)
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone).first()
            assert user.current_session_id == session_id

    def test_nonexistent_user_returns_phone_as_fallback(self):
        phone = "+5599999999000"
        session_id = get_or_rotate_session(phone)
        assert session_id == phone

    def test_force_new_false_keeps_existing(self):
        phone = _make_user("+5500000002034")
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone).first()
            user.current_session_id = f"{phone}_keep0000"
        session_id = get_or_rotate_session(phone, force_new=False)
        assert session_id == f"{phone}_keep0000"


# ---------------------------------------------------------------------------
# is_new_session
# ---------------------------------------------------------------------------


class TestIsNewSession:
    def test_no_last_seen_returns_true(self):
        user = {"last_seen_at": None}
        assert is_new_session(user) is True

    def test_recent_last_seen_returns_false(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        user = {"last_seen_at": recent}
        assert is_new_session(user) is False

    def test_old_last_seen_returns_true(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        user = {"last_seen_at": old}
        assert is_new_session(user) is True

    def test_exactly_at_threshold_returns_true(self):
        at_threshold = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        user = {"last_seen_at": at_threshold}
        assert is_new_session(user) is True

    def test_custom_threshold(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        user = {"last_seen_at": recent}
        assert is_new_session(user, threshold_hours=2) is True
        assert is_new_session(user, threshold_hours=4) is False

    def test_datetime_object_works(self):
        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        user = {"last_seen_at": recent}
        assert is_new_session(user) is False

    def test_naive_datetime_treated_as_utc(self):
        recent = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        user = {"last_seen_at": recent}
        assert is_new_session(user) is False

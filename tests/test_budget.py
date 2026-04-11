"""Tests for feature_gates: check_budget, get_user_plan, budget calculation, addon budget."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.auth.passwords import hash_password
from src.config.feature_gates import (
    _get_addon_budget,
    _get_budget_info,
    _get_limit,
    _sum_costs_in_period,
    check_budget,
    get_budget_summary,
    get_monthly_total_cost,
    get_user_plan,
    get_user_plan_type,
    is_admin_unlimited,
    is_feature_enabled,
)
from src.db.models import BudgetAddOn, UsageEvent
from src.db.session import get_db
from src.memory.identity import create_user_full, promote_user_to_admin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_user(phone="+5500000001000", role="user", plan_type="free"):
    hashed = hash_password("TestPass123!")
    create_user_full(
        phone_number=phone,
        username=f"user_{phone[-4:]}",
        name="Test User",
        email=f"user_{phone[-4:]}@test.com",
        birth_date="2000-01-01",
        password_hash=hashed,
        role=role,
    )
    return phone


def _add_usage_event(user_id, cost_usd, event_type="llm_usage"):
    """Insert a usage event with a given cost."""
    with get_db() as session:
        session.add(UsageEvent(
            user_id=user_id,
            channel="test",
            event_type=event_type,
            status="success",
            cost_usd=cost_usd,
            created_at=datetime.now(timezone.utc).isoformat(),
        ))


def _add_addon(user_id, amount_usd, expires_in_days=30):
    """Insert a budget add-on."""
    expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).isoformat()
    with get_db() as session:
        session.add(BudgetAddOn(
            user_id=user_id,
            amount_usd=amount_usd,
            expires_at=expires_at,
        ))


# ---------------------------------------------------------------------------
# get_user_plan
# ---------------------------------------------------------------------------


class TestGetUserPlan:
    def test_nonexistent_user_returns_free_plan(self):
        plan = get_user_plan("+5599999999999")
        assert plan["code"] == "free"

    def test_free_user_returns_free_plan(self):
        phone = _create_user("+5500000001001")
        with patch("src.billing.service.is_subscription_active", return_value=False):
            plan = get_user_plan(phone)
        assert plan["code"] == "free"

    def test_admin_user_with_bypass_returns_admin_plan(self):
        phone = _create_user("+5500000001002")
        promote_user_to_admin(phone)
        with patch("src.config.feature_gates.get_config", return_value="true"):
            plan = get_user_plan(phone)
        assert plan.get("_is_admin") is True
        assert plan["code"] == "admin"

    def test_paid_user_returns_paid_plan(self):
        phone = _create_user("+5500000001003")
        mock_sub = {"plan_code": "pro", "status": "active", "current_period_start": None, "current_period_end": None}
        mock_plan = {"code": "pro", "name": "Pro", "limits_json": json.dumps({"monthly_budget_usd": 10.0})}
        with patch("src.billing.service.is_subscription_active", return_value=True), \
             patch("src.models.subscriptions.get_active_subscription", return_value=mock_sub), \
             patch("src.models.subscriptions.get_plan", return_value=mock_plan):
            plan = get_user_plan(phone)
        assert plan["code"] == "pro"


class TestGetUserPlanType:
    def test_admin_returns_admin(self):
        phone = _create_user("+5500000001010")
        promote_user_to_admin(phone)
        with patch("src.config.feature_gates.get_config", return_value="true"):
            assert get_user_plan_type(phone) == "admin"

    def test_free_user_returns_trial(self):
        phone = _create_user("+5500000001011")
        with patch("src.billing.service.is_subscription_active", return_value=False):
            assert get_user_plan_type(phone) == "trial"

    def test_paid_user_returns_paid(self):
        phone = _create_user("+5500000001012")
        mock_sub = {"plan_code": "pro", "status": "active", "current_period_start": None, "current_period_end": None}
        mock_plan = {"code": "pro", "name": "Pro", "limits_json": "{}"}
        with patch("src.billing.service.is_subscription_active", return_value=True), \
             patch("src.models.subscriptions.get_active_subscription", return_value=mock_sub), \
             patch("src.models.subscriptions.get_plan", return_value=mock_plan):
            assert get_user_plan_type(phone) == "paid"


# ---------------------------------------------------------------------------
# _get_limit helper
# ---------------------------------------------------------------------------


class TestGetLimit:
    def test_admin_plan_returns_large_number(self):
        admin_plan = {"_is_admin": True}
        assert _get_limit(admin_plan, "monthly_budget_usd") == 999999

    def test_admin_plan_returns_true_for_enabled_keys(self):
        admin_plan = {"_is_admin": True}
        assert _get_limit(admin_plan, "voice_live_enabled") is True

    def test_reads_from_limits_json(self):
        plan = {"limits_json": json.dumps({"monthly_budget_usd": 5.0})}
        assert _get_limit(plan, "monthly_budget_usd") == 5.0

    def test_returns_default_for_missing_key(self):
        plan = {"limits_json": "{}"}
        assert _get_limit(plan, "nonexistent_key", 42) == 42

    def test_handles_invalid_json_gracefully(self):
        plan = {"limits_json": "not-json"}
        assert _get_limit(plan, "any_key", 0) == 0

    def test_handles_none_limits_json(self):
        plan = {"limits_json": None}
        assert _get_limit(plan, "any_key", 0) == 0


# ---------------------------------------------------------------------------
# check_budget
# ---------------------------------------------------------------------------


class TestCheckBudget:
    def test_admin_always_passes(self):
        phone = _create_user("+5500000001020")
        promote_user_to_admin(phone)
        with patch("src.config.feature_gates.get_config", return_value="true"):
            result = check_budget(phone)
        assert result is None

    def test_free_user_within_budget_passes(self):
        phone = _create_user("+5500000001021")
        # Free plan has monthly_budget_usd=0.50
        with patch("src.billing.service.is_subscription_active", return_value=False):
            result = check_budget(phone)
        assert result is None

    def test_free_user_over_budget_blocked(self):
        phone = _create_user("+5500000001022")
        # Add usage exceeding free plan limit ($0.50)
        _add_usage_event(phone, 0.60)
        with patch("src.billing.service.is_subscription_active", return_value=False):
            result = check_budget(phone)
        assert result is not None
        assert "limite" in result.lower() or "encerrou" in result.lower()

    def test_budget_zero_returns_none(self):
        """When total_budget is 0 (plan has no budget), check_budget should pass."""
        phone = _create_user("+5500000001023")
        plan = {"code": "custom", "name": "Custom", "limits_json": json.dumps({"monthly_budget_usd": 0})}
        with patch("src.config.feature_gates.get_user_plan", return_value=plan):
            result = check_budget(phone)
        assert result is None

    def test_addon_budget_extends_limit(self):
        phone = _create_user("+5500000001024")
        # Free has $0.50 budget, add $1.00 addon
        _add_addon(phone, 1.00, expires_in_days=30)
        # Use $1.00 (under the combined $1.50)
        _add_usage_event(phone, 1.00)
        with patch("src.billing.service.is_subscription_active", return_value=False):
            result = check_budget(phone)
        assert result is None

    def test_addon_budget_expired_not_counted(self):
        phone = _create_user("+5500000001025")
        # Add expired addon
        expires_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with get_db() as session:
            session.add(BudgetAddOn(
                user_id=phone,
                amount_usd=10.0,
                expires_at=expires_at,
            ))
        # Use $0.60 (over the free $0.50 — expired addon should not help)
        _add_usage_event(phone, 0.60)
        with patch("src.billing.service.is_subscription_active", return_value=False):
            result = check_budget(phone)
        assert result is not None


# ---------------------------------------------------------------------------
# _get_budget_info
# ---------------------------------------------------------------------------


class TestGetBudgetInfo:
    def test_admin_returns_zero_used_huge_budget(self):
        phone = _create_user("+5500000001030")
        promote_user_to_admin(phone)
        with patch("src.config.feature_gates.get_config", return_value="true"):
            used, total, pct, resets_at = _get_budget_info(phone)
        assert used == 0.0
        assert total == 999999.0
        assert pct == 0.0

    def test_zero_budget_returns_all_zeros(self):
        phone = _create_user("+5500000001031")
        plan = {"code": "custom", "limits_json": json.dumps({"monthly_budget_usd": 0})}
        with patch("src.config.feature_gates.get_user_plan", return_value=plan):
            used, total, pct, resets_at = _get_budget_info(phone)
        assert used == 0.0
        assert total == 0.0
        assert pct == 0.0


# ---------------------------------------------------------------------------
# _sum_costs_in_period
# ---------------------------------------------------------------------------


class TestSumCostsInPeriod:
    def test_no_events_returns_zero(self):
        phone = _create_user("+5500000001040")
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=30)).isoformat()
        end = now.isoformat()
        total = _sum_costs_in_period(phone, start, end)
        assert total == 0.0

    def test_sums_cost_usd_column(self):
        phone = _create_user("+5500000001041")
        _add_usage_event(phone, 0.10)
        _add_usage_event(phone, 0.20)
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=1)).isoformat()
        end = (now + timedelta(days=1)).isoformat()
        total = _sum_costs_in_period(phone, start, end)
        assert abs(total - 0.30) < 0.001

    def test_ignores_non_cost_event_types(self):
        phone = _create_user("+5500000001042")
        _add_usage_event(phone, 0.10, event_type="llm_usage")
        _add_usage_event(phone, 0.50, event_type="message_received")  # Not a cost event
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=1)).isoformat()
        end = (now + timedelta(days=1)).isoformat()
        total = _sum_costs_in_period(phone, start, end)
        assert abs(total - 0.10) < 0.001

    def test_legacy_extra_data_fallback(self):
        """Events with cost_usd=0 but cost in extra_data JSON (legacy) should be counted."""
        phone = _create_user("+5500000001043")
        with get_db() as session:
            session.add(UsageEvent(
                user_id=phone,
                channel="test",
                event_type="llm_usage",
                status="success",
                cost_usd=0,
                extra_data=json.dumps({"cost_usd": 0.15}),
                created_at=datetime.now(timezone.utc).isoformat(),
            ))
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=1)).isoformat()
        end = (now + timedelta(days=1)).isoformat()
        total = _sum_costs_in_period(phone, start, end)
        assert abs(total - 0.15) < 0.001


# ---------------------------------------------------------------------------
# _get_addon_budget
# ---------------------------------------------------------------------------


class TestGetAddonBudget:
    def test_no_addons_returns_zero(self):
        phone = _create_user("+5500000001050")
        assert _get_addon_budget(phone) == 0.0

    def test_sums_active_addons(self):
        phone = _create_user("+5500000001051")
        _add_addon(phone, 1.00)
        _add_addon(phone, 2.50)
        total = _get_addon_budget(phone)
        assert abs(total - 3.50) < 0.01

    def test_ignores_expired_addons(self):
        phone = _create_user("+5500000001052")
        _add_addon(phone, 5.00, expires_in_days=30)
        expires_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with get_db() as session:
            session.add(BudgetAddOn(
                user_id=phone,
                amount_usd=10.0,
                expires_at=expires_at,
            ))
        total = _get_addon_budget(phone)
        assert abs(total - 5.00) < 0.01


# ---------------------------------------------------------------------------
# is_feature_enabled
# ---------------------------------------------------------------------------


class TestIsFeatureEnabled:
    def test_admin_all_features_enabled(self):
        phone = _create_user("+5500000001060")
        promote_user_to_admin(phone)
        with patch("src.config.feature_gates.get_config", return_value="true"):
            assert is_feature_enabled(phone, "voice_live_enabled") is True
            assert is_feature_enabled(phone, "tts_enabled") is True

    def test_free_user_feature_disabled(self):
        phone = _create_user("+5500000001061")
        with patch("src.billing.service.is_subscription_active", return_value=False):
            assert is_feature_enabled(phone, "voice_live_enabled") is False

    def test_feature_as_string_true(self):
        plan = {"code": "test", "limits_json": json.dumps({"tts_enabled": "true"})}
        with patch("src.config.feature_gates.get_user_plan", return_value=plan):
            assert is_feature_enabled("+5500000001062", "tts_enabled") is True

    def test_feature_as_string_false(self):
        plan = {"code": "test", "limits_json": json.dumps({"tts_enabled": "false"})}
        with patch("src.config.feature_gates.get_user_plan", return_value=plan):
            assert is_feature_enabled("+5500000001063", "tts_enabled") is False


# ---------------------------------------------------------------------------
# is_admin_unlimited
# ---------------------------------------------------------------------------


class TestIsAdminUnlimited:
    def test_admin_is_unlimited(self):
        phone = _create_user("+5500000001070")
        promote_user_to_admin(phone)
        with patch("src.config.feature_gates.get_config", return_value="true"):
            assert is_admin_unlimited(phone) is True

    def test_normal_user_not_unlimited(self):
        phone = _create_user("+5500000001071")
        with patch("src.billing.service.is_subscription_active", return_value=False):
            assert is_admin_unlimited(phone) is False


# ---------------------------------------------------------------------------
# get_budget_summary
# ---------------------------------------------------------------------------


class TestGetBudgetSummary:
    def test_admin_summary_is_unlimited(self):
        phone = _create_user("+5500000001080")
        promote_user_to_admin(phone)
        with patch("src.config.feature_gates.get_config", return_value="true"):
            summary = get_budget_summary(phone)
        assert summary["budget"]["unlimited"] is True
        assert summary["budget"]["used_pct"] == 0.0
        assert summary["plan_name"] == "Admin"

    def test_free_user_summary_has_features(self):
        phone = _create_user("+5500000001081")
        with patch("src.billing.service.is_subscription_active", return_value=False):
            summary = get_budget_summary(phone)
        assert "features" in summary
        assert summary["plan_code"] == "free"
        # At least some feature gates should be present
        assert "voice_live_enabled" in summary["features"]


# ---------------------------------------------------------------------------
# Backward compat aliases
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_get_monthly_total_cost(self):
        phone = _create_user("+5500000001090")
        _add_usage_event(phone, 0.05)
        with patch("src.billing.service.is_subscription_active", return_value=False):
            used, total = get_monthly_total_cost(phone)
        assert used >= 0.05
        assert total >= 0

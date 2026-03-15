import json
import os
from datetime import datetime, timezone
from typing import Optional

from src.db.session import get_db
from src.db.models import BillingPlan, Subscription, BillingEvent


FREE_LIMITS = {
    "max_tasks_per_user": 2,
    "max_tasks_per_user_daily": 5,
    "voice_live_enabled": False,
    "voice_live_max_minutes_daily": 0,
    "tts_enabled": False,
    "max_searches_daily": 10,
    "max_deep_research_daily": 1,
    "social_monitoring_enabled": True,
    "max_tracked_accounts": 3,
    "video_analysis_max_minutes_monthly": 0,
}

PAID_LIMITS = {
    "max_tasks_per_user": 5,
    "max_tasks_per_user_daily": 50,
    "voice_live_enabled": True,
    "voice_live_max_minutes_daily": 20,
    "tts_enabled": True,
    "max_searches_daily": 50,
    "max_deep_research_daily": 3,
    "social_monitoring_enabled": True,
    "max_tracked_accounts": 20,
    "video_analysis_max_minutes_monthly": 30,
}


def init_billing_db():
    pass


def ensure_default_plan():
    """Backward compat alias."""
    ensure_default_plans()


def ensure_default_plans():
    # --- Free plan (first-class, non-deletable) ---
    free = get_plan("free", initialize=False)
    if not free:
        create_plan(
            code="free",
            name="Free",
            description="Plano gratuito com limites básicos.",
            amount_cents=0,
            trial_days=0,
            features_json='["Chat texto","Tarefas","Lembretes"]',
            limits_json=json.dumps(FREE_LIMITS),
        )
    elif not free.get("limits_json") or free["limits_json"] == "{}":
        update_plan("free", limits_json=json.dumps(FREE_LIMITS))

    # Planos pagos são criados manualmente pelo admin via API.
    # Certifique-se de configurar o stripe_price_id ao criar o plano.


def is_event_processed(event_id: str) -> bool:
    with get_db() as session:
        row = session.query(BillingEvent).filter_by(event_id=event_id).first()
        return row is not None


def record_billing_event(event_id: str, event_type: str, payload_json: str):
    with get_db() as session:
        existing = session.query(BillingEvent).filter_by(event_id=event_id).first()
        if existing:
            return
        ev = BillingEvent(
            event_id=event_id,
            event_type=event_type,
            payload_json=payload_json,
        )
        session.add(ev)


def upsert_subscription(data: dict):
    now_iso = datetime.now(timezone.utc).isoformat()
    data["updated_at"] = now_iso

    fields = [
        "user_id", "plan_code", "provider", "provider_customer_id",
        "provider_subscription_id", "status", "trial_start", "trial_end",
        "current_period_start", "current_period_end", "cancel_at_period_end",
        "canceled_at", "ended_at", "payment_method_summary", "last_invoice_id",
        "updated_at",
    ]

    with get_db() as session:
        sub = (
            session.query(Subscription)
            .filter_by(provider_subscription_id=data["provider_subscription_id"])
            .first()
        )
        if sub:
            for f in fields:
                if f in data:
                    setattr(sub, f, data[f])
        else:
            sub = Subscription(**{f: data[f] for f in fields if f in data})
            session.add(sub)


def get_active_subscription(user_id: str) -> Optional[dict]:
    with get_db() as session:
        sub = (
            session.query(Subscription)
            .filter(
                Subscription.user_id == user_id,
                Subscription.status.in_(["active", "trialing", "past_due"]),
            )
            .order_by(Subscription.created_at.desc())
            .first()
        )
        if not sub:
            return None
        return sub.to_dict()


def get_plan(code: str, initialize: bool = True) -> Optional[dict]:
    with get_db() as session:
        plan = session.query(BillingPlan).filter_by(code=code).first()
        if not plan:
            return None
        return plan.to_dict()


def get_plan_by_price_id(price_id: str) -> Optional[dict]:
    with get_db() as session:
        plan = (
            session.query(BillingPlan)
            .filter_by(stripe_price_id=price_id)
            .order_by(BillingPlan.created_at.desc())
            .first()
        )
        if not plan:
            return None
        return plan.to_dict()


def get_default_active_plan() -> Optional[dict]:
    """Return the first active *paid* plan (skips free)."""
    with get_db() as session:
        plan = (
            session.query(BillingPlan)
            .filter(
                BillingPlan.is_active == True,  # noqa: E712
                BillingPlan.code != "free",
            )
            .order_by(BillingPlan.id.asc())
            .first()
        )
        if not plan:
            return None
        return plan.to_dict()


def list_plans(active_only: bool = False) -> list[dict]:
    with get_db() as session:
        q = session.query(BillingPlan)
        if active_only:
            q = q.filter_by(is_active=True)
        plans = q.order_by(BillingPlan.id.asc()).all()
        return [p.to_dict() for p in plans]


def create_plan(
    code: str,
    name: str,
    description: str,
    amount_cents: int,
    trial_days: int = 7,
    stripe_product_id: str = "",
    stripe_price_id: str = "",
    currency: str = "brl",
    interval: str = "month",
    features_json: str = "[]",
    limits_json: str = "{}",
    is_active: bool = True,
):
    with get_db() as session:
        existing = session.query(BillingPlan).filter_by(code=code).first()
        if existing:
            return existing.to_dict()
        plan = BillingPlan(
            code=code,
            name=name,
            description=description,
            features_json=features_json,
            limits_json=limits_json,
            is_active=is_active,
            trial_days=trial_days,
            stripe_product_id=stripe_product_id,
            stripe_price_id=stripe_price_id,
            amount_cents=amount_cents,
            currency=currency,
            interval=interval,
        )
        session.add(plan)
        session.flush()
        return plan.to_dict()


def update_plan(code: str, **fields):
    allowed = {
        "name", "description", "features_json", "limits_json", "is_active", "trial_days",
        "stripe_product_id", "stripe_price_id", "amount_cents", "currency", "interval",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return get_plan(code)
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    with get_db() as session:
        plan = session.query(BillingPlan).filter_by(code=code).first()
        if not plan:
            return None
        for k, v in updates.items():
            setattr(plan, k, v)
        session.flush()
        return plan.to_dict()


def delete_plan(code: str) -> bool:
    if code == "free":
        return False
    with get_db() as session:
        plan = session.query(BillingPlan).filter_by(code=code).first()
        if plan:
            session.delete(plan)
    return True


def list_subscriptions(limit: int = 100) -> list[dict]:
    with get_db() as session:
        subs = (
            session.query(Subscription)
            .order_by(Subscription.created_at.desc())
            .limit(limit)
            .all()
        )
        return [s.to_dict() for s in subs]


def update_subscription_user_id(old_user_id: str, new_user_id: str):
    with get_db() as session:
        subs = session.query(Subscription).filter_by(user_id=old_user_id).all()
        for sub in subs:
            sub.user_id = new_user_id

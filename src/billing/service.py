from datetime import datetime, timezone
import json
from src.billing.types import SubscriptionStatus, BillingContext
from src.models.subscriptions import (
    upsert_subscription,
    get_active_subscription,
    record_billing_event,
    is_event_processed,
    get_plan,
    get_plan_by_price_id,
    get_default_active_plan,
)
from src.memory.identity import update_stripe_customer_id, get_user

def sync_subscription_from_stripe(event_id: str, event_type: str, stripe_obj: dict):
    """
    Sincroniza um objeto Subscription ou Invoice do Stripe para o banco local.
    Idempotente: checa event_id antes de processar.
    """
    if is_event_processed(event_id):
        return
        
    if event_type in ["customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"]:
        # Update subscription status
        sub_id = stripe_obj.get("id")
        customer_id = stripe_obj.get("customer")
        status = stripe_obj.get("status")
        cancel_at_period_end = stripe_obj.get("cancel_at_period_end", False)
        canceled_at = stripe_obj.get("canceled_at")
        ended_at = stripe_obj.get("ended_at")
        trial_start = stripe_obj.get("trial_start")
        trial_end = stripe_obj.get("trial_end")
        current_period_start = stripe_obj.get("current_period_start")
        current_period_end = stripe_obj.get("current_period_end")
        
        # Get plan code from local catalog by Stripe price
        items = stripe_obj.get("items", {}).get("data", [])
        plan_code = "pro_mensal"
        if items:
            price_id = items[0].get("price", {}).get("id")
            mapped_plan = get_plan_by_price_id(price_id) if price_id else None
            if mapped_plan:
                plan_code = mapped_plan["code"]

        # Parse timestamps
        def ts_to_iso(ts):
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None

        # Determine user_id from customer_id (Requires a mapping function or looking up users table)
        from src.memory.identity import _use_postgres, _get_pg_engine, _get_sqlite_conn
        
        user_phone = None
        if _use_postgres():
            engine = _get_pg_engine()
            with engine.connect() as conn:
                row = conn.execute(__import__("sqlalchemy").text("SELECT phone_number FROM users WHERE stripe_customer_id = :c"), {"c": customer_id}).fetchone()
                if row:
                    user_phone = row[0]
        else:
            conn = _get_sqlite_conn()
            row = conn.execute("SELECT phone_number FROM users WHERE stripe_customer_id = ?", (customer_id,)).fetchone()
            if row:
                user_phone = row[0]
            conn.close()
            
        if not user_phone:
            # Log error: unknown customer
            record_billing_event(event_id, event_type, json.dumps(stripe_obj))
            return
            
        upsert_data = {
            "user_id": user_phone,
            "plan_code": plan_code,
            "provider": "stripe",
            "provider_customer_id": customer_id,
            "provider_subscription_id": sub_id,
            "status": status,
            "trial_start": ts_to_iso(trial_start),
            "trial_end": ts_to_iso(trial_end),
            "current_period_start": ts_to_iso(current_period_start),
            "current_period_end": ts_to_iso(current_period_end),
            "cancel_at_period_end": cancel_at_period_end,
            "canceled_at": ts_to_iso(canceled_at),
            "ended_at": ts_to_iso(ended_at),
        }
        upsert_subscription(upsert_data)
        
    elif event_type == "invoice.payment_failed":
        # Handle invoice payment failure (e.g. notify via whatsapp)
        pass
        
    elif event_type == "invoice.paid":
        # Handle successful payment
        pass
        
    record_billing_event(event_id, event_type, json.dumps(stripe_obj))

def is_subscription_active(user_phone: str) -> bool:
    sub = get_active_subscription(user_phone)
    if not sub:
        return False
    return sub["status"] in ["active", "trialing", "past_due"]

def get_billing_context(user_phone: str) -> BillingContext:
    sub = get_active_subscription(user_phone)
    if not sub:
        user = get_user(user_phone)
        if user and user.get("plan_type") == "trial" and user.get("trial_ends_at"):
            try:
                trial_end = datetime.fromisoformat(user["trial_ends_at"]) if isinstance(user["trial_ends_at"], str) else user["trial_ends_at"]
                if trial_end.tzinfo is None:
                    trial_end = trial_end.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) < trial_end:
                    return BillingContext(
                        status=SubscriptionStatus.TRIALING,
                        trial_end=trial_end,
                        current_period_end=trial_end,
                        cancel_at_period_end=False,
                        plan_code="pro_mensal",
                        has_active_subscription=True,
                        has_stripe_subscription=False,
                    )
            except Exception:
                pass
        return BillingContext(
            status=SubscriptionStatus.INCOMPLETE,
            trial_end=None,
            current_period_end=None,
            cancel_at_period_end=False,
            plan_code=None,
            has_active_subscription=False,
            has_stripe_subscription=False
        )
        
    def parse_dt(dt_val):
        if not dt_val:
            return None
        if isinstance(dt_val, datetime):
            return dt_val if dt_val.tzinfo else dt_val.replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(str(dt_val))
        
    status = SubscriptionStatus(sub["status"])
    return BillingContext(
        status=status,
        trial_end=parse_dt(sub["trial_end"]),
        current_period_end=parse_dt(sub["current_period_end"]),
        cancel_at_period_end=sub["cancel_at_period_end"],
        plan_code=sub["plan_code"],
        has_active_subscription=(status in [SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING, SubscriptionStatus.PAST_DUE]),
        has_stripe_subscription=True
    )


def get_billing_overview(user: dict) -> dict:
    from src.integrations.stripe import list_customer_payment_methods

    ctx = get_billing_context(user["phone_number"])
    plan = get_plan(ctx.plan_code) if ctx.plan_code else get_default_active_plan()
    payment_methods = []
    if user.get("stripe_customer_id"):
        try:
            payment_methods = list_customer_payment_methods(user["stripe_customer_id"])
        except Exception:
            payment_methods = []

    sub = get_active_subscription(user["phone_number"])
    has_stripe_subscription = sub is not None

    return {
        "status": ctx.status.value,
        "trial_end": ctx.trial_end.isoformat() if ctx.trial_end else user.get("trial_ends_at"),
        "current_period_end": ctx.current_period_end.isoformat() if ctx.current_period_end else None,
        "cancel_at_period_end": ctx.cancel_at_period_end,
        "plan_code": ctx.plan_code or (plan["code"] if plan else None),
        "plan_name": plan["name"] if plan else None,
        "plan_description": plan["description"] if plan else None,
        "amount_cents": plan["amount_cents"] if plan else None,
        "currency": plan["currency"] if plan else "brl",
        "interval": plan["interval"] if plan else "month",
        "features_json": plan["features_json"] if plan else "[]",
        "payment_methods": payment_methods,
        "has_active_subscription": ctx.has_active_subscription or user.get("plan_type") == "trial",
        "has_stripe_subscription": has_stripe_subscription,
    }


def get_or_create_customer(user: dict) -> str:
    from src.integrations.stripe import create_customer
    if user.get("stripe_customer_id"):
        return user["stripe_customer_id"]
        
    customer = create_customer(
        email=user.get("email", ""),
        name=user.get("name", ""),
        phone=user.get("phone_number", "")
    )
    update_stripe_customer_id(user["phone_number"], customer.id)
    return customer.id

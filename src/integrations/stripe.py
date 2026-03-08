import os
import stripe
from typing import Optional

# Initialize stripe key
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

def get_webhook_secret() -> str:
    return os.getenv("STRIPE_WEBHOOK_SECRET", "")

def create_product_and_price(name: str, description: str, amount_cents: int, currency: str = "brl", interval: str = "month"):
    product = stripe.Product.create(
        name=name,
        description=description,
    )
    price = stripe.Price.create(
        product=product.id,
        unit_amount=amount_cents,
        currency=currency,
        recurring={"interval": interval},
    )
    return product.id, price.id

def create_customer(email: str, name: str, phone: str) -> stripe.Customer:
    return stripe.Customer.create(
        email=email,
        name=name,
        phone=phone
    )

def get_customer(customer_id: str) -> stripe.Customer:
    return stripe.Customer.retrieve(customer_id)


def list_customer_payment_methods(customer_id: str) -> list[dict]:
    methods = stripe.PaymentMethod.list(customer=customer_id, type="card")
    result = []
    for method in methods.data:
        card = getattr(method, "card", None)
        result.append(
            {
                "id": method.id,
                "brand": getattr(card, "brand", None),
                "last4": getattr(card, "last4", None),
                "exp_month": getattr(card, "exp_month", None),
                "exp_year": getattr(card, "exp_year", None),
            }
        )
    return result

def create_subscription(customer_id: str, price_id: str, trial_days: int = 7) -> dict:
    """
    Creates a subscription with default_incomplete payment behavior.
    Returns the subscription object which contains the pending_setup_intent
    (if trial) or latest_invoice.payment_intent (if no trial) client_secret.
    """
    params = {
        "customer": customer_id,
        "items": [{"price": price_id}],
        "payment_behavior": "default_incomplete",
        "payment_settings": {"save_default_payment_method": "on_subscription"},
        "expand": ["latest_invoice.payment_intent", "pending_setup_intent"],
    }
    
    if trial_days > 0:
        params["trial_period_days"] = trial_days

    return stripe.Subscription.create(**params)

def create_portal_session(customer_id: str, return_url: str) -> stripe.billing_portal.Session:
    return stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=return_url
    )

def create_refund(payment_intent_id: str, amount_cents: Optional[int] = None) -> stripe.Refund:
    params = {"payment_intent": payment_intent_id}
    if amount_cents is not None:
        params["amount"] = amount_cents
    return stripe.Refund.create(**params)

def cancel_subscription(subscription_id: str, immediately: bool = False) -> stripe.Subscription:
    if immediately:
        return stripe.Subscription.delete(subscription_id)
    else:
        return stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)

def reactivate_subscription(subscription_id: str) -> stripe.Subscription:
    return stripe.Subscription.modify(subscription_id, cancel_at_period_end=False)

def create_setup_intent(customer_id: str) -> stripe.SetupIntent:
    return stripe.SetupIntent.create(
        customer=customer_id,
        payment_method_types=["card"],
        usage="off_session",
    )

def set_default_payment_method(customer_id: str, subscription_id: str, payment_method_id: str):
    stripe.PaymentMethod.attach(payment_method_id, customer=customer_id)
    stripe.Customer.modify(
        customer_id,
        invoice_settings={"default_payment_method": payment_method_id},
    )
    stripe.Subscription.modify(subscription_id, default_payment_method=payment_method_id)

def update_subscription_price(subscription_id: str, new_price_id: str) -> stripe.Subscription:
    """Upgrade/downgrade: swap the single item to a new price with proration."""
    sub = stripe.Subscription.retrieve(subscription_id)
    item_id = sub["items"]["data"][0]["id"]
    return stripe.Subscription.modify(
        subscription_id,
        items=[{"id": item_id, "price": new_price_id}],
        proration_behavior="create_prorations",
    )


def construct_webhook_event(payload: bytes, sig_header: str) -> stripe.Event:
    webhook_secret = get_webhook_secret()
    return stripe.Webhook.construct_event(
        payload, sig_header, webhook_secret
    )

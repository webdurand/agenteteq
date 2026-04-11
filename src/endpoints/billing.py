import os
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from typing import Optional
from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address)


def ts_to_iso(ts) -> str | None:
    """Convert a Unix timestamp to ISO 8601 string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None


from src.auth.deps import get_current_user
from src.billing.service import (
    get_billing_overview,
    get_billing_context,
    get_or_create_customer,
    sync_subscription_from_stripe
)
from src.models.subscriptions import get_default_active_plan, list_plans, get_plan
from src.integrations.stripe import (
    create_portal_session,
    cancel_subscription,
    construct_webhook_event,
    create_setup_intent,
    set_default_payment_method,
    update_subscription_price,
)
from src.models.subscriptions import upsert_subscription

router = APIRouter(prefix="/billing", tags=["billing"])

class SetupRequest(BaseModel):
    plan_code: Optional[str] = None

class ActivateRequest(BaseModel):
    plan_code: str
    setup_intent_id: str

class UpgradeRequest(BaseModel):
    plan_code: str


@router.post("/setup")
@limiter.limit("5/minute")
def setup_checkout(request: Request, req: SetupRequest, user: dict = Depends(get_current_user)):
    """
    Step 1: Create Stripe customer + SetupIntent to collect the card.
    No subscription is created yet — trial only starts after /activate.
    """
    # Validate the plan exists and has a stripe_price_id
    plan = None
    if req.plan_code:
        plan = get_plan(req.plan_code)
    if not plan:
        plan = get_default_active_plan()
    if not plan or not plan.get("stripe_price_id"):
        raise HTTPException(
            status_code=400,
            detail="Plano inválido: O ID de preço do Stripe (Price ID) não foi configurado. Edite este plano no painel de Admin e adicione o Price ID (ex: price_1Pxyz...)."
        )

    customer_id = get_or_create_customer(user)
    try:
        intent = create_setup_intent(customer_id)
        return {
            "client_secret": intent.client_secret,
            "setup_intent_id": intent.id,
            "customer_id": customer_id,
            "plan_code": plan["code"],
        }
    except Exception:
        logger.exception("Erro ao criar SetupIntent")
        raise HTTPException(status_code=500, detail="Erro interno do servidor")


@router.post("/activate")
@limiter.limit("5/minute")
def activate_subscription(request: Request, req: ActivateRequest, user: dict = Depends(get_current_user)):
    """
    Step 2: Card was confirmed. Now create the subscription with the saved payment method.
    Trial starts only here, after the card is validated.
    """
    import stripe
    from datetime import datetime, timezone

    plan = get_plan(req.plan_code)
    if not plan or not plan.get("stripe_price_id"):
        raise HTTPException(status_code=400, detail="Plano inválido ou sem Price ID")

    customer_id = get_or_create_customer(user)

    # Retrieve the SetupIntent to get the payment_method
    try:
        si = stripe.SetupIntent.retrieve(req.setup_intent_id)
    except Exception:
        logger.exception("Erro ao recuperar SetupIntent")
        raise HTTPException(status_code=400, detail="SetupIntent inválido")

    if si.status != "succeeded":
        raise HTTPException(status_code=400, detail="Cartão ainda não foi confirmado")

    # [SEC] Verify SetupIntent belongs to this customer (prevents IDOR - CWE-639)
    if si.customer and si.customer != customer_id:
        raise HTTPException(status_code=403, detail="SetupIntent não pertence a este usuário")

    payment_method_id = si.payment_method
    if not payment_method_id:
        raise HTTPException(status_code=400, detail="Nenhum método de pagamento encontrado no SetupIntent")

    # Attach payment method to customer and set as default
    try:
        stripe.PaymentMethod.attach(payment_method_id, customer=customer_id)
        stripe.Customer.modify(
            customer_id,
            invoice_settings={"default_payment_method": payment_method_id},
        )
    except Exception:
        logger.exception("Erro ao vincular método de pagamento")
        raise HTTPException(status_code=500, detail="Erro ao salvar cartão")

    # Now create the subscription with the card already saved
    try:
        trial_days = plan.get("trial_days", 0)
        params = {
            "customer": customer_id,
            "items": [{"price": plan["stripe_price_id"]}],
            "default_payment_method": payment_method_id,
            "expand": ["latest_invoice.payment_intent"],
        }
        if trial_days > 0:
            params["trial_period_days"] = trial_days

        subscription = stripe.Subscription.create(**params)

        upsert_subscription({
            "user_id": user["phone_number"],
            "plan_code": plan["code"],
            "provider": "stripe",
            "provider_customer_id": customer_id,
            "provider_subscription_id": subscription["id"],
            "status": subscription["status"],
            "trial_start": ts_to_iso(subscription.get("trial_start")),
            "trial_end": ts_to_iso(subscription.get("trial_end")),
            "current_period_start": ts_to_iso(subscription.get("current_period_start")),
            "current_period_end": ts_to_iso(subscription.get("current_period_end")),
            "cancel_at_period_end": subscription.get("cancel_at_period_end", False),
        })

        return {
            "subscription_id": subscription["id"],
            "status": subscription["status"],
            "plan_code": plan["code"],
            "plan_name": plan["name"],
        }
    except Exception:
        logger.exception("Erro ao criar assinatura")
        raise HTTPException(status_code=500, detail="Erro interno do servidor")


@router.post("/upgrade")
def upgrade_plan(req: UpgradeRequest, user: dict = Depends(get_current_user)):
    """
    Switch active subscription to a different plan.
    - paid → paid: Stripe proration (immediate price swap)
    - paid → free: cancel subscription at period end (downgrade)
    """
    from src.models.subscriptions import get_active_subscription, get_plan as get_plan_model

    sub = get_active_subscription(user["phone_number"])
    if not sub or not sub.get("provider_subscription_id"):
        raise HTTPException(status_code=400, detail="Nenhuma assinatura ativa para fazer upgrade")

    target_plan = get_plan_model(req.plan_code)
    if not target_plan:
        raise HTTPException(status_code=404, detail="Plano não encontrado")

    # --- Downgrade to free: cancel at period end ---
    if target_plan["code"] == "free":
        try:
            updated_sub = cancel_subscription(sub["provider_subscription_id"])
            upsert_subscription({
                "user_id": user["phone_number"],
                "plan_code": sub["plan_code"],
                "provider": "stripe",
                "provider_customer_id": sub.get("provider_customer_id", ""),
                "provider_subscription_id": updated_sub["id"],
                "status": updated_sub["status"],
                "current_period_start": ts_to_iso(updated_sub.get("current_period_start")),
                "current_period_end": ts_to_iso(updated_sub.get("current_period_end")),
                "cancel_at_period_end": True,
            })

            period_end = ts_to_iso(updated_sub.get("current_period_end"))
            return {
                "status": "downgrading_to_free",
                "plan_code": "free",
                "plan_name": "Free",
                "effective_date": period_end,
            }
        except Exception:
            logger.exception("Erro ao fazer downgrade para free")
            raise HTTPException(status_code=500, detail="Erro interno do servidor")

    # --- Upgrade/change between paid plans ---
    if not target_plan.get("stripe_price_id"):
        raise HTTPException(status_code=400, detail="Plano sem Price ID configurado no Stripe")

    try:
        updated_sub = update_subscription_price(
            sub["provider_subscription_id"],
            target_plan["stripe_price_id"],
        )

        upsert_subscription({
            "user_id": user["phone_number"],
            "plan_code": target_plan["code"],
            "provider": "stripe",
            "provider_customer_id": sub.get("provider_customer_id", ""),
            "provider_subscription_id": updated_sub["id"],
            "status": updated_sub["status"],
            "current_period_start": ts_to_iso(updated_sub.get("current_period_start")),
            "current_period_end": ts_to_iso(updated_sub.get("current_period_end")),
            "cancel_at_period_end": updated_sub.get("cancel_at_period_end", False),
        })

        return {
            "status": "upgraded",
            "plan_code": target_plan["code"],
            "plan_name": target_plan["name"],
        }
    except Exception:
        logger.exception("Erro ao fazer upgrade de plano")
        raise HTTPException(status_code=500, detail="Erro interno do servidor")


@router.post("/portal")
def portal(user: dict = Depends(get_current_user)):
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=400, detail="User has no Stripe customer ID")
        
    return_url = os.getenv("FRONTEND_URL", "http://localhost:5173") + "/dashboard"
    try:
        session = create_portal_session(customer_id, return_url)
        return {"url": session.url}
    except Exception as e:
        logger.exception("Erro ao criar sessão do portal Stripe")
        raise HTTPException(status_code=500, detail="Erro interno do servidor")


@router.get("/subscription")
def get_subscription_status(user: dict = Depends(get_current_user)):
    return get_billing_overview(user)


@router.get("/plans")
def get_available_plans(user: dict = Depends(get_current_user)):
    return {"plans": list_plans(active_only=True)}


@router.get("/plans/public")
def get_public_plans():
    """Public endpoint for landing page pricing — no auth required."""
    plans = list_plans(active_only=True)
    # Strip sensitive Stripe IDs from public response
    return {
        "plans": [
            {
                "code": p["code"],
                "name": p["name"],
                "description": p.get("description", ""),
                "amount_cents": p.get("amount_cents", 0),
                "trial_days": p.get("trial_days", 0),
                "features_json": p.get("features_json", "[]"),
            }
            for p in plans
            if p.get("code") != "free"
        ]
    }


@router.post("/setup-payment-method")
def setup_payment_method(user: dict = Depends(get_current_user)):
    customer_id = get_or_create_customer(user)
    try:
        intent = create_setup_intent(customer_id)
        return {"client_secret": intent.client_secret}
    except Exception as e:
        logger.exception("Erro ao criar setup intent de pagamento")
        raise HTTPException(status_code=500, detail="Erro interno do servidor")


class UpdateDefaultPaymentRequest(BaseModel):
    payment_method_id: str

@router.post("/update-default-payment")
def update_default_payment(req: UpdateDefaultPaymentRequest, user: dict = Depends(get_current_user)):
    import stripe as _stripe
    from src.models.subscriptions import get_active_subscription
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=400, detail="Sem cadastro Stripe")
    sub = get_active_subscription(user["phone_number"])
    if not sub:
        raise HTTPException(status_code=400, detail="Sem assinatura ativa")
    # [SEC] Verify PaymentMethod belongs to this customer (prevents IDOR - CWE-639)
    try:
        pm = _stripe.PaymentMethod.retrieve(req.payment_method_id)
        if pm.customer and pm.customer != customer_id:
            raise HTTPException(status_code=403, detail="Método de pagamento não pertence a este usuário")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Método de pagamento inválido")
    try:
        set_default_payment_method(customer_id, sub["provider_subscription_id"], req.payment_method_id)
        return {"status": "updated"}
    except Exception as e:
        logger.exception("Erro ao atualizar método de pagamento padrão")
        raise HTTPException(status_code=500, detail="Erro interno do servidor")


@router.post("/cancel")
@limiter.limit("3/minute")
def cancel(request: Request, user: dict = Depends(get_current_user)):
    # Get active subscription from local db
    from src.models.subscriptions import get_active_subscription
    sub = get_active_subscription(user["phone_number"])
    if not sub:
        raise HTTPException(status_code=400, detail="No active subscription found")
        
    try:
        updated_sub = cancel_subscription(sub["provider_subscription_id"], immediately=False)
        return {"status": "canceled_at_period_end"}
    except Exception as e:
        logger.exception("Erro ao cancelar assinatura")
        raise HTTPException(status_code=500, detail="Erro interno do servidor")

        
# ---------------------------------------------------------------------------
# Budget Add-on: comprar mais limite (R$99,90 avulso)
# ---------------------------------------------------------------------------

MAX_ACTIVE_ADDONS = 3

@router.post("/addon")
@limiter.limit("3/minute")
def purchase_addon(request: Request, user: dict = Depends(get_current_user)):
    """
    Cria um PaymentIntent para comprar limite adicional (R$99,90).
    Após confirmação do pagamento, o budget do usuário é ampliado em $10 USD
    por 30 dias. Máximo de 3 add-ons ativos por vez.
    """
    import stripe
    from src.db.models import BudgetAddOn
    from src.db.session import get_db

    now = datetime.now(timezone.utc).isoformat()
    with get_db() as session:
        active_count = session.query(BudgetAddOn).filter(
            BudgetAddOn.user_id == user["phone_number"],
            BudgetAddOn.expires_at > now,
        ).count()
    if active_count >= MAX_ACTIVE_ADDONS:
        raise HTTPException(status_code=400, detail=f"Limite de {MAX_ACTIVE_ADDONS} add-ons ativos. Aguarde um expirar.")

    customer_id = get_or_create_customer(user)
    amount_cents = 9990  # R$99,90

    try:
        intent = stripe.PaymentIntent.create(
            amount=amount_cents,
            currency="brl",
            customer=customer_id,
            metadata={
                "user_id": user["phone_number"],
                "type": "budget_addon",
                "budget_usd": "10.00",
            },
        )
        return {
            "client_secret": intent.client_secret,
            "payment_intent_id": intent.id,
        }
    except Exception as e:
        logger.exception("Erro ao criar PaymentIntent para add-on")
        raise HTTPException(status_code=500, detail="Erro ao processar pagamento")


class AddonConfirmRequest(BaseModel):
    payment_intent_id: str

@router.post("/addon/confirm")
@limiter.limit("5/minute")
def confirm_addon(request: Request, req: AddonConfirmRequest, user: dict = Depends(get_current_user)):
    """
    Chamado pelo frontend após confirmação do pagamento.
    Verifica o PaymentIntent no Stripe e registra o add-on no banco.
    """
    import stripe as _stripe
    from datetime import timedelta
    from src.db.models import BudgetAddOn
    from src.db.session import get_db

    try:
        intent = _stripe.PaymentIntent.retrieve(req.payment_intent_id)
    except Exception:
        raise HTTPException(status_code=400, detail="PaymentIntent inválido")

    if intent.status != "succeeded":
        raise HTTPException(status_code=400, detail="Pagamento não confirmado")

    if intent.metadata.get("user_id") != user["phone_number"]:
        raise HTTPException(status_code=403, detail="PaymentIntent não pertence a este usuário")

    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=30)

    with get_db() as session:
        existing = session.query(BudgetAddOn).filter_by(
            user_id=user["phone_number"],
            stripe_payment_id=req.payment_intent_id,
        ).first()
        if existing:
            from src.config.feature_gates import get_budget_summary
            return {"ok": True, "message": "Add-on já registrado.", "budget": get_budget_summary(user["phone_number"])}

        # [SEC] Re-check addon limit inside transaction to prevent race condition (CWE-362)
        active_count = session.query(BudgetAddOn).filter(
            BudgetAddOn.user_id == user["phone_number"],
            BudgetAddOn.expires_at > now.isoformat(),
        ).count()
        if active_count >= MAX_ACTIVE_ADDONS:
            raise HTTPException(status_code=400, detail=f"Limite de {MAX_ACTIVE_ADDONS} add-ons ativos.")

        addon = BudgetAddOn(
            user_id=user["phone_number"],
            amount_usd=10.00,
            stripe_payment_id=req.payment_intent_id,
            purchased_at=now.isoformat(),
            expires_at=expires.isoformat(),
        )
        session.add(addon)

    from src.config.feature_gates import get_budget_summary
    return {
        "ok": True,
        "message": "Limite adicionado com sucesso! +100% por 30 dias.",
        "budget": get_budget_summary(user["phone_number"]),
    }


webhook_router = APIRouter(tags=["webhooks"])

@webhook_router.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing signature")
        
    try:
        event = construct_webhook_event(payload, sig_header)
    except ValueError as e:
        # Invalid payload
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Invalid signature
        raise HTTPException(status_code=400, detail=str(e))
        
    # Process event
    try:
        event_type = event.get("type")
        event_id = event.get("id")
        data_obj = event.get("data", {}).get("object", {})
        
        sync_subscription_from_stripe(event_id, event_type, data_obj)
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        # Return 500 to trigger Stripe's automatic retries
        import traceback
        traceback.print_exc()
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

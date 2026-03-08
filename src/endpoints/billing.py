import os
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger(__name__)

from src.auth.deps import get_current_user
from src.billing.service import (
    get_billing_overview,
    get_billing_context,
    get_or_create_customer,
    sync_subscription_from_stripe
)
from src.models.subscriptions import get_default_active_plan, list_plans, get_plan
from src.integrations.stripe import (
    create_subscription,
    create_portal_session,
    cancel_subscription,
    construct_webhook_event,
    create_setup_intent,
    set_default_payment_method,
    update_subscription_price,
)
from src.models.subscriptions import upsert_subscription

router = APIRouter(prefix="/billing", tags=["billing"])

class SubscribeRequest(BaseModel):
    price_id: Optional[str] = None

class UpgradeRequest(BaseModel):
    plan_code: str
    
@router.post("/subscribe")
def subscribe(req: SubscribeRequest, user: dict = Depends(get_current_user)):
    customer_id = get_or_create_customer(user)
    
    active_plan = None
    if req.price_id:
        active_plan = get_plan(req.price_id)
        if not active_plan:
            from src.models.subscriptions import get_plan_by_price_id
            active_plan = get_plan_by_price_id(req.price_id)
        
    if not active_plan:
        active_plan = get_default_active_plan()
        
    stripe_price_id = (active_plan["stripe_price_id"] if active_plan else None) or os.getenv("STRIPE_PRICE_ID_DEFAULT")
    
    if not stripe_price_id:
        raise HTTPException(
            status_code=400, 
            detail="Plano inválido: O ID de preço do Stripe (Price ID) não foi configurado. Edite este plano no painel de Admin e adicione o Price ID (ex: price_1Pxyz...)."
        )
        
    try:
        trial_days = active_plan["trial_days"] if active_plan else 7
        subscription = create_subscription(customer_id, stripe_price_id, trial_days=trial_days)
        
        # If it has a trial, Stripe returns a pending_setup_intent. 
        # If no trial, it returns a payment_intent in latest_invoice.
        client_secret = None
        if subscription.get("pending_setup_intent"):
            client_secret = subscription["pending_setup_intent"].get("client_secret")
        elif subscription.get("latest_invoice") and subscription["latest_invoice"].get("payment_intent"):
            client_secret = subscription["latest_invoice"]["payment_intent"].get("client_secret")

        # Fallback: alguns fluxos do Stripe não retornam client_secret na criação da subscription.
        # Nesses casos criamos um SetupIntent explícito para o frontend coletar o cartão.
        if not client_secret:
            setup_intent = create_setup_intent(customer_id)
            client_secret = setup_intent.client_secret

        if not client_secret:
            raise HTTPException(status_code=500, detail="Failed to generate client secret")

        # Salva imediatamente no banco local (não depender só do webhook)
        from datetime import datetime, timezone
        def ts_to_iso(ts):
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None

        try:
            upsert_subscription({
                "user_id": user["phone_number"],
                "plan_code": active_plan["code"],
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
        except Exception:
            pass  # Não bloquear — webhook vai sincronizar depois

        return {
            "subscription_id": subscription["id"],
            "client_secret": client_secret,
            "status": subscription["status"],
            "plan_code": active_plan["code"],
        }
    except Exception as e:
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
            from datetime import datetime, timezone
            def ts_to_iso(ts):
                return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None

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

        from datetime import datetime, timezone
        def ts_to_iso(ts):
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None

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
    from src.models.subscriptions import get_active_subscription
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=400, detail="Sem cadastro Stripe")
    sub = get_active_subscription(user["phone_number"])
    if not sub:
        raise HTTPException(status_code=400, detail="Sem assinatura ativa")
    try:
        set_default_payment_method(customer_id, sub["provider_subscription_id"], req.payment_method_id)
        return {"status": "updated"}
    except Exception as e:
        logger.exception("Erro ao atualizar método de pagamento padrão")
        raise HTTPException(status_code=500, detail="Erro interno do servidor")


@router.post("/cancel")
def cancel(user: dict = Depends(get_current_user)):
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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.auth.deps import require_admin
from src.models.subscriptions import (
    init_billing_db,
    list_plans as list_plans_model,
    list_subscriptions as list_subscriptions_model,
    create_plan,
    update_plan,
    get_plan,
    delete_plan,
    upsert_subscription,
)
from src.integrations.stripe import create_product_and_price

router = APIRouter(prefix="/admin/billing", tags=["admin_billing"])

class PlanCreateReq(BaseModel):
    code: str
    name: str
    description: str = ""
    amount_cents: int
    trial_days: int = 7
    stripe_product_id: str = ""
    stripe_price_id: str = ""
    features_json: str = "[]"


class PlanUpdateReq(BaseModel):
    name: str
    description: str = ""
    amount_cents: int | None = None
    trial_days: int | None = None
    stripe_product_id: str | None = None
    stripe_price_id: str | None = None
    features_json: str | None = None
    is_active: bool | None = None

class RefundReq(BaseModel):
    subscription_id: int
    amount_cents: int
    reason: str

class ManualSubscriptionReq(BaseModel):
    phone_number: str
    plan_code: str
    days: int = 30
    status: str = "active"

@router.get("/plans")
def list_plans(user: dict = Depends(require_admin)):
    return list_plans_model(active_only=False)


@router.post("/plans")
def create_plan_endpoint(req: PlanCreateReq, user: dict = Depends(require_admin)):
    existing = get_plan(req.code)
    if existing:
        raise HTTPException(status_code=400, detail="Ja existe um plano com esse codigo")
        
    try:
        if not req.stripe_price_id:
            prod_id, price_id = create_product_and_price(
                name=req.name,
                description=req.description,
                amount_cents=req.amount_cents
            )
            req.stripe_product_id = prod_id
            req.stripe_price_id = price_id
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao criar no Stripe: {str(e)}")

    return create_plan(
        code=req.code,
        name=req.name,
        description=req.description,
        amount_cents=req.amount_cents,
        trial_days=req.trial_days,
        stripe_product_id=req.stripe_product_id,
        stripe_price_id=req.stripe_price_id,
        features_json=req.features_json,
    )


@router.put("/plans/{code}")
def update_plan_endpoint(code: str, req: PlanUpdateReq, user: dict = Depends(require_admin)):
    existing = get_plan(code)
    if not existing:
        raise HTTPException(status_code=404, detail="Plano nao encontrado")
        
    try:
        # Se nao tinha price_id antes, e nao mandou agora, cria um novo
        if not existing.get("stripe_price_id") and not req.stripe_price_id:
            prod_id, price_id = create_product_and_price(
                name=req.name,
                description=req.description,
                amount_cents=req.amount_cents or existing.get("amount_cents", 0)
            )
            req.stripe_product_id = prod_id
            req.stripe_price_id = price_id
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao criar no Stripe: {str(e)}")

    return update_plan(
        code,
        name=req.name,
        description=req.description,
        amount_cents=req.amount_cents,
        trial_days=req.trial_days,
        stripe_product_id=req.stripe_product_id,
        stripe_price_id=req.stripe_price_id,
        features_json=req.features_json,
        is_active=req.is_active,
    )


@router.delete("/plans/{code}")
def delete_plan_endpoint(code: str, user: dict = Depends(require_admin)):
    existing = get_plan(code)
    if not existing:
        raise HTTPException(status_code=404, detail="Plano nao encontrado")
    delete_plan(code)
    return {"message": "Plano removido com sucesso"}


@router.get("/subscriptions")
def list_subscriptions(user: dict = Depends(require_admin)):
    return list_subscriptions_model(limit=200)

@router.post("/subscriptions/manual")
def create_manual_subscription(req: ManualSubscriptionReq, user: dict = Depends(require_admin)):
    import uuid
    from datetime import datetime, timedelta, timezone
    
    plan = get_plan(req.plan_code)
    if not plan:
        raise HTTPException(status_code=404, detail="Plano nao encontrado")
        
    now = datetime.now(timezone.utc)
    end_date = now + timedelta(days=req.days)
    
    # Check if a manual sub already exists to reuse provider_subscription_id, or generate one
    sub_id = f"manual_{uuid.uuid4().hex[:8]}"
    
    upsert_data = {
        "user_id": req.phone_number,
        "plan_code": req.plan_code,
        "provider": "manual",
        "provider_customer_id": "manual",
        "provider_subscription_id": sub_id,
        "status": req.status,
        "current_period_start": now.isoformat(),
        "current_period_end": end_date.isoformat(),
        "cancel_at_period_end": False,
    }
    upsert_subscription(upsert_data)
    return {"message": "Assinatura manual adicionada com sucesso!"}

@router.post("/refunds")
def process_refund(req: RefundReq, user: dict = Depends(require_admin)):
    init_billing_db()
    raise HTTPException(status_code=501, detail="Reembolso automatico ainda depende do payment_intent salvo localmente. Use o painel da Stripe por enquanto.")

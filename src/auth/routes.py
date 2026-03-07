from fastapi import APIRouter, HTTPException, status, Depends, Request
from pydantic import BaseModel, EmailStr
from slowapi import Limiter
from slowapi.util import get_remote_address
from src.auth.passwords import hash_password, verify_password
from src.auth.jwt import create_token
from src.auth.otp import generate_code, verify_code
from src.auth.google import verify_google_token
from src.auth.deps import get_current_user
from src.memory.identity import (
    create_user_full,
    get_user,
    get_user_by_email,
    get_user_by_username,
    set_whatsapp_verified,
    link_google_account,
    is_plan_active,
    change_user_phone_number,
)
from src.integrations.whatsapp import whatsapp_client

limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/auth", tags=["auth"])

class RegisterRequest(BaseModel):
    username: str
    name: str
    email: EmailStr
    birth_date: str
    phone: str
    password: str

class VerifyRequest(BaseModel):
    phone: str
    code: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class GoogleAuthRequest(BaseModel):
    id_token: str

class GoogleCompleteRequest(BaseModel):
    google_id_token: str
    username: str
    phone: str
    birth_date: str
    password: str

class ResendCodeRequest(BaseModel):
    phone: str
    purpose: str


class ChangePhoneRequest(BaseModel):
    new_phone: str


class ChangePhoneVerifyRequest(BaseModel):
    new_phone: str
    code: str

async def send_otp_whatsapp(phone: str, purpose: str):
    code = generate_code(phone, purpose)
    text = f"Seu codigo de verificacao do Teq e: *{code}*\n\nNao compartilhe este codigo com ninguem."
    try:
        await whatsapp_client.send_text_message(to_number=phone, text=text)
    except Exception as e:
        print(f"[AUTH] Falha ao enviar OTP para {phone}: {e}")
        raise HTTPException(status_code=502, detail=f"Falha ao enviar codigo via WhatsApp. Verifique o numero.")

@router.post("/register", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(request: Request, req: RegisterRequest):
    if get_user_by_email(req.email):
        raise HTTPException(status_code=400, detail="E-mail ja cadastrado")
    if get_user_by_username(req.username):
        raise HTTPException(status_code=400, detail="Username ja em uso")
    if get_user(req.phone):
        raise HTTPException(status_code=400, detail="Telefone ja cadastrado")
        
    hashed = hash_password(req.password)
    create_user_full(
        phone_number=req.phone,
        username=req.username,
        name=req.name,
        email=req.email,
        birth_date=req.birth_date,
        password_hash=hashed
    )
    
    await send_otp_whatsapp(req.phone, "register")
    return {"message": "Usuario criado. Codigo enviado para o WhatsApp."}

@router.post("/verify-whatsapp")
@limiter.limit("5/minute")
async def verify_whatsapp(request: Request, req: VerifyRequest):
    user = get_user(req.phone)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado")
        
    if not verify_code(req.phone, req.code, "register"):
        raise HTTPException(status_code=400, detail="Codigo invalido ou expirado")
        
    set_whatsapp_verified(req.phone)
    
    # Atualiza dados para adicionar ao JWT
    user = get_user(req.phone)
    token = create_token(user["phone_number"], user["username"], user["email"], user.get("role", "user"))
    
    return {
        "message": "WhatsApp verificado com sucesso",
        "token": token
    }

@router.post("/login")
@limiter.limit("10/minute")
async def login(request: Request, req: LoginRequest):
    user = get_user_by_email(req.email)
    if not user:
        raise HTTPException(status_code=401, detail="Credenciais invalidas")
        
    if not user.get("password_hash") or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenciais invalidas")
        
    if not user.get("whatsapp_verified"):
        # Se nao tiver verificado ainda, envia codigo para "register" de novo? 
        # O ideal seria "login_2fa" tbm, vamos usar "register" pra nao bagunçar o onboarding
        purpose = "register"
    else:
        purpose = "login_2fa"
        
    await send_otp_whatsapp(user["phone_number"], purpose)
    
    # Mascarar o telefone para o frontend exibir "(11) 9****-1234" se quiser, mas retornamos limpo aqui 
    # pra facilitar a integracao (ou mascaramos? o frontend pede o phone para dar o proximo POST)
    return {
        "message": "Codigo 2FA enviado para o WhatsApp",
        "phone": user["phone_number"],
        "purpose": purpose
    }

@router.post("/verify-2fa")
async def verify_2fa(req: VerifyRequest):
    user = get_user(req.phone)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado")
        
    if not verify_code(req.phone, req.code, "login_2fa"):
        # Tenta fallback para "register" caso seja primeiro acesso incompleto
        if not verify_code(req.phone, req.code, "register"):
            raise HTTPException(status_code=400, detail="Codigo invalido ou expirado")
        else:
            set_whatsapp_verified(req.phone)
            user = get_user(req.phone)
            
    token = create_token(user["phone_number"], user["username"], user["email"], user.get("role", "user"))
    return {
        "message": "Login bem sucedido",
        "token": token
    }

@router.post("/google")
@limiter.limit("10/minute")
async def google_auth(request: Request, req: GoogleAuthRequest):
    try:
        google_data = verify_google_token(req.id_token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    user = get_user_by_email(google_data["email"])
    
    if user:
        if not user.get("google_id"):
            link_google_account(google_data["email"], google_data["google_id"])
            user = get_user_by_email(google_data["email"])
            
        if not user.get("whatsapp_verified"):
            await send_otp_whatsapp(user["phone_number"], "register")
            return {
                "needs_registration": False,
                "needs_verification": True,
                "phone": user["phone_number"]
            }
            
        token = create_token(user["phone_number"], user["username"], user["email"], user.get("role", "user"))
        return {
            "needs_registration": False,
            "needs_verification": False,
            "token": token
        }
    else:
        # Usuario novo
        return {
            "needs_registration": True,
            "email": google_data["email"],
            "name": google_data["name"]
        }

@router.post("/google/complete", status_code=status.HTTP_201_CREATED)
async def google_complete(req: GoogleCompleteRequest):
    try:
        google_data = verify_google_token(req.google_id_token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    if get_user_by_username(req.username):
        raise HTTPException(status_code=400, detail="Username ja em uso")
    if get_user(req.phone):
        raise HTTPException(status_code=400, detail="Telefone ja cadastrado")
        
    hashed = hash_password(req.password)
    create_user_full(
        phone_number=req.phone,
        username=req.username,
        name=google_data["name"],
        email=google_data["email"],
        birth_date=req.birth_date,
        password_hash=hashed,
        google_id=google_data["google_id"],
        auth_provider="google"
    )
    
    await send_otp_whatsapp(req.phone, "register")
    return {"message": "Conta vinculada. Codigo enviado para o WhatsApp."}

@router.post("/resend-code")
async def resend_code(req: ResendCodeRequest):
    user = get_user(req.phone)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado")
    await send_otp_whatsapp(req.phone, req.purpose)
    return {"message": "Codigo reenviado"}


@router.post("/change-phone/request")
async def request_phone_change(req: ChangePhoneRequest, current_user: dict = Depends(get_current_user)):
    if get_user(req.new_phone):
        raise HTTPException(status_code=400, detail="Telefone ja cadastrado")
    await send_otp_whatsapp(req.new_phone, "change_phone")
    return {"message": "Codigo enviado para o novo WhatsApp"}


@router.post("/change-phone/verify")
async def verify_phone_change(req: ChangePhoneVerifyRequest, current_user: dict = Depends(get_current_user)):
    if not verify_code(req.new_phone, req.code, "change_phone"):
        raise HTTPException(status_code=400, detail="Codigo invalido ou expirado")

    old_phone = current_user["phone_number"]
    change_user_phone_number(old_phone, req.new_phone)
    updated_user = get_user(req.new_phone)
    token = create_token(updated_user["phone_number"], updated_user["username"], updated_user["email"], updated_user.get("role", "user"))
    return {
        "message": "Telefone atualizado com sucesso",
        "token": token,
        "phone_number": updated_user["phone_number"],
    }

from src.billing.service import get_billing_context

@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    ctx = get_billing_context(current_user["phone_number"])
    
    # Retornar dados seguros
    safe_user = {
        "phone_number": current_user.get("phone_number"),
        "name": current_user.get("name"),
        "username": current_user.get("username"),
        "email": current_user.get("email"),
        "whatsapp_verified": current_user.get("whatsapp_verified"),
        "plan_type": current_user.get("plan_type"),
        "plan_active": is_plan_active(current_user),
        "role": current_user.get("role", "user"),
        "subscription_status": ctx.status.value,
        "trial_end": ctx.trial_end.isoformat() if ctx.trial_end else None,
        "current_period_end": ctx.current_period_end.isoformat() if ctx.current_period_end else None,
        "cancel_at_period_end": ctx.cancel_at_period_end,
        "plan_code": ctx.plan_code,
        "has_stripe_subscription": ctx.has_stripe_subscription
    }
    return safe_user

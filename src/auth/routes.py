import logging

from fastapi import APIRouter, HTTPException, status, Depends, Request
from pydantic import BaseModel, EmailStr
from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)
from src.auth.passwords import hash_password, verify_password
from src.auth.jwt import create_token, create_refresh_token, decode_token
from src.auth.otp import generate_code, verify_code
from src.auth.google import verify_google_token
from src.auth.deps import get_current_user
from src.memory.identity import (
    create_user_full,
    delete_account,
    get_user,
    get_user_by_email,
    get_user_by_username,
    get_password_hash_by_email,
    update_password,
    set_whatsapp_verified,
    link_google_account,
    is_plan_active,
    change_user_phone_number,
)
from src.integrations.whatsapp import whatsapp_client

limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/auth", tags=["auth"])

from pydantic import field_validator

class RegisterRequest(BaseModel):
    username: str
    name: str
    email: EmailStr
    birth_date: str
    phone: str
    password: str

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Senha deve ter no minimo 8 caracteres")
        if not any(c.isdigit() for c in v):
            raise ValueError("Senha deve conter ao menos um numero")
        if not any(c.isalpha() for c in v):
            raise ValueError("Senha deve conter ao menos uma letra")
        return v

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
        logger.error("Falha ao enviar OTP para %s: %s", phone, e)
        raise HTTPException(status_code=502, detail=f"Falha ao enviar codigo via WhatsApp. Verifique o numero.")

@router.post("/register", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(request: Request, req: RegisterRequest):
    from src.auth.terms import CURRENT_TERMS_VERSION

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
        password_hash=hashed,
        terms_accepted_version=CURRENT_TERMS_VERSION,
    )

    try:
        await send_otp_whatsapp(req.phone, "register")
    except HTTPException:
        # OTP falhou -- limpa o usuário para permitir re-registro
        delete_account(req.phone)
        raise
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
    refresh = create_refresh_token(user["phone_number"])

    return {
        "message": "WhatsApp verificado com sucesso",
        "token": token,
        "refresh_token": refresh,
    }

@router.post("/login")
@limiter.limit("10/minute")
async def login(request: Request, req: LoginRequest):
    user = get_user_by_email(req.email)
    if not user:
        raise HTTPException(status_code=401, detail="Credenciais invalidas")
        
    stored_hash = get_password_hash_by_email(req.email)
    if not stored_hash or not verify_password(req.password, stored_hash):
        raise HTTPException(status_code=401, detail="Credenciais invalidas")
        
    # Conta de teste: pula 2FA somente em dev/test (para verificacao Google OAuth)
    import os
    _test_email = os.getenv("TEST_ACCOUNT_EMAIL")
    _env = os.getenv("ENV", "dev")
    if _test_email and _env in ("dev", "test") and req.email.lower() == _test_email.lower() and user.get("whatsapp_verified"):
        token = create_token(user["phone_number"], user["username"], user["email"], user.get("role", "user"))
        refresh = create_refresh_token(user["phone_number"])
        return {
            "message": "Login bem sucedido (test account)",
            "token": token,
            "refresh_token": refresh,
            "phone": user["phone_number"],
            "purpose": "none",
        }

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
@limiter.limit("5/minute")
async def verify_2fa(request: Request, req: VerifyRequest):
    user = get_user(req.phone)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado")
        
    if not verify_code(req.phone, req.code, "login_2fa"):
        raise HTTPException(status_code=400, detail="Codigo invalido ou expirado")

    token = create_token(user["phone_number"], user["username"], user["email"], user.get("role", "user"))
    refresh = create_refresh_token(user["phone_number"])
    return {
        "message": "Login bem sucedido",
        "token": token,
        "refresh_token": refresh,
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
        refresh = create_refresh_token(user["phone_number"])
        return {
            "needs_registration": False,
            "needs_verification": False,
            "token": token,
            "refresh_token": refresh,
        }
    else:
        # Usuario novo
        return {
            "needs_registration": True,
            "email": google_data["email"],
            "name": google_data["name"]
        }

@router.post("/google/complete", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def google_complete(request: Request, req: GoogleCompleteRequest):
    try:
        google_data = verify_google_token(req.google_id_token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    if get_user_by_username(req.username):
        raise HTTPException(status_code=400, detail="Username ja em uso")
    if get_user(req.phone):
        raise HTTPException(status_code=400, detail="Telefone ja cadastrado")
        
    from src.auth.terms import CURRENT_TERMS_VERSION

    hashed = hash_password(req.password)
    create_user_full(
        phone_number=req.phone,
        username=req.username,
        name=google_data["name"],
        email=google_data["email"],
        birth_date=req.birth_date,
        password_hash=hashed,
        google_id=google_data["google_id"],
        auth_provider="google",
        terms_accepted_version=CURRENT_TERMS_VERSION,
    )
    
    await send_otp_whatsapp(req.phone, "register")
    return {"message": "Conta vinculada. Codigo enviado para o WhatsApp."}

@router.post("/resend-code")
@limiter.limit("3/minute")
async def resend_code(request: Request, req: ResendCodeRequest):
    user = get_user(req.phone)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado")
    await send_otp_whatsapp(req.phone, req.purpose)
    return {"message": "Codigo reenviado"}


@router.post("/change-phone/request")
@limiter.limit("3/minute")
async def request_phone_change(request: Request, req: ChangePhoneRequest, current_user: dict = Depends(get_current_user)):
    if get_user(req.new_phone):
        raise HTTPException(status_code=400, detail="Telefone ja cadastrado")
    await send_otp_whatsapp(req.new_phone, "change_phone")
    return {"message": "Codigo enviado para o novo WhatsApp"}


@router.post("/change-phone/verify")
@limiter.limit("5/minute")
async def verify_phone_change(request: Request, req: ChangePhoneVerifyRequest, current_user: dict = Depends(get_current_user)):
    if not verify_code(req.new_phone, req.code, "change_phone"):
        raise HTTPException(status_code=400, detail="Codigo invalido ou expirado")

    old_phone = current_user["phone_number"]
    change_user_phone_number(old_phone, req.new_phone)
    updated_user = get_user(req.new_phone)
    token = create_token(updated_user["phone_number"], updated_user["username"], updated_user["email"], updated_user.get("role", "user"))
    refresh = create_refresh_token(updated_user["phone_number"])
    return {
        "message": "Telefone atualizado com sucesso",
        "token": token,
        "refresh_token": refresh,
        "phone_number": updated_user["phone_number"],
    }

from src.billing.service import get_billing_context


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh")
@limiter.limit("10/minute")
async def refresh_token(request: Request, req: RefreshRequest):
    """
    Troca um refresh_token valido por um novo par access + refresh token.
    """
    payload = decode_token(req.refresh_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Refresh token invalido")
    if payload.get("_error") == "expired":
        raise HTTPException(status_code=401, detail="Refresh token expirado. Faca login novamente.")
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Token invalido para refresh")

    phone = payload.get("sub")
    if not phone:
        raise HTTPException(status_code=401, detail="Token malformado")

    user = get_user(phone)
    if not user:
        raise HTTPException(status_code=401, detail="Usuario nao encontrado")

    new_access = create_token(user["phone_number"], user["username"], user["email"], user.get("role", "user"))
    new_refresh = create_refresh_token(user["phone_number"])
    return {
        "token": new_access,
        "refresh_token": new_refresh,
    }


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
        "has_stripe_subscription": ctx.has_stripe_subscription,
        "terms_accepted_version": current_user.get("terms_accepted_version"),
    }
    return safe_user


@router.post("/accept-terms")
async def accept_terms(current_user: dict = Depends(get_current_user)):
    from src.auth.terms import CURRENT_TERMS_VERSION
    from src.db.session import get_db
    from src.db.models import User
    from datetime import datetime, timezone

    try:
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=current_user["phone_number"]).first()
            if not user:
                raise HTTPException(status_code=404, detail="Usuário não encontrado.")
            user.terms_accepted_version = CURRENT_TERMS_VERSION
            user.terms_accepted_at = datetime.now(timezone.utc)
            session.commit()
        return {"accepted_version": CURRENT_TERMS_VERSION}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Erro ao aceitar termos: %s", e)
        raise HTTPException(status_code=500, detail="Erro ao registrar aceite dos termos.")


@router.delete("/account")
async def delete_my_account(current_user: dict = Depends(get_current_user)):
    """Exclui permanentemente a conta e todos os dados pessoais (LGPD Art. 18 VI)."""
    phone = current_user["phone_number"]
    try:
        deleted = delete_account(phone)
        if not deleted:
            raise HTTPException(status_code=404, detail="Conta não encontrada.")
        return {"message": "Conta e todos os dados pessoais foram excluídos permanentemente."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Erro ao excluir conta: %s", e)
        raise HTTPException(status_code=500, detail="Erro ao excluir conta.")


@router.get("/export-data")
async def export_my_data(current_user: dict = Depends(get_current_user)):
    """Retorna todos os dados pessoais do usuario em formato JSON (LGPD Art. 18 V — portabilidade)."""
    from src.db.session import get_db
    from src.db.models import (
        User, ChatMessage, Task, Reminder, Subscription,
        UsageEvent, UserIntegration, Carousel, BackgroundTask,
    )

    phone = current_user["phone_number"]
    try:
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone).first()
            if not user:
                raise HTTPException(status_code=404, detail="Usuário não encontrado.")

            messages = session.query(ChatMessage).filter_by(user_id=phone).order_by(ChatMessage.created_at).all()
            tasks = session.query(Task).filter_by(user_id=phone).all()
            reminders = session.query(Reminder).filter_by(user_id=phone).all()
            subscriptions = session.query(Subscription).filter_by(user_id=phone).all()
            usage = session.query(UsageEvent).filter_by(user_id=phone).all()
            integrations = session.query(UserIntegration).filter_by(user_id=phone).all()
            carousels = session.query(Carousel).filter_by(user_id=phone).all()

            return {
                "export_date": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                "profile": {
                    "phone_number": user.phone_number,
                    "name": user.name,
                    "username": user.username,
                    "email": user.email,
                    "birth_date": user.birth_date,
                    "auth_provider": user.auth_provider,
                    "plan_type": user.plan_type,
                    "timezone": user.timezone,
                    "created_terms_version": user.terms_accepted_version,
                    "terms_accepted_at": user.terms_accepted_at.isoformat() if user.terms_accepted_at else None,
                },
                "chat_messages": [
                    {"role": m.role, "text": m.text, "session_id": m.session_id, "created_at": str(m.created_at)}
                    for m in messages
                ],
                "tasks": [t.to_dict() for t in tasks],
                "reminders": [r.to_dict() for r in reminders],
                "subscriptions": [s.to_dict() for s in subscriptions],
                "usage_events": [
                    {"event_type": u.event_type, "channel": u.channel, "tool_name": u.tool_name, "created_at": u.created_at}
                    for u in usage
                ],
                "integrations": [
                    {"provider": i.provider, "account_email": i.account_email, "scopes": i.scopes, "created_at": str(i.created_at)}
                    for i in integrations
                ],
                "carousels": [c.to_dict() for c in carousels],
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Erro ao exportar dados: %s", e)
        raise HTTPException(status_code=500, detail="Erro ao exportar dados.")


# ── Forgot Password (via WhatsApp OTP) ──

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    phone: str
    code: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Senha deve ter no minimo 8 caracteres")
        if not any(c.isdigit() for c in v):
            raise ValueError("Senha deve conter ao menos um numero")
        if not any(c.isalpha() for c in v):
            raise ValueError("Senha deve conter ao menos uma letra")
        return v


@router.post("/forgot-password")
@limiter.limit("3/minute")
async def forgot_password(request: Request, req: ForgotPasswordRequest):
    """Send OTP to user's WhatsApp for password reset. Always returns success to prevent enumeration."""
    user = get_user_by_email(req.email)
    if user and user.get("phone_number"):
        try:
            await send_otp_whatsapp(user["phone_number"], "reset_password")
        except Exception:
            pass  # Don't leak whether user exists
    # Always return same response to prevent email enumeration
    return {
        "message": "Se o e-mail estiver cadastrado, enviamos um codigo para o WhatsApp associado.",
        "phone_hint": user["phone_number"][-4:] if user else None,
    }


@router.post("/reset-password")
@limiter.limit("5/minute")
async def reset_password(request: Request, req: ResetPasswordRequest):
    """Verify OTP and reset password."""
    if not verify_code(req.phone, req.code, "reset_password"):
        raise HTTPException(status_code=400, detail="Codigo invalido ou expirado")

    user = get_user(req.phone)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado")

    hashed = hash_password(req.new_password)
    if not update_password(req.phone, hashed):
        raise HTTPException(status_code=500, detail="Erro ao atualizar senha")

    return {"message": "Senha atualizada com sucesso. Faca login com a nova senha."}

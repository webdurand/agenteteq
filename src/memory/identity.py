import os
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

from src.db.session import get_db, get_engine
from src.db.models import (
    ChatMessage,
    Reminder,
    Subscription,
    Task,
    UsageEvent,
    User,
)


def _use_postgres() -> bool:
    return bool(os.getenv("DATABASE_URL"))


def _get_pg_engine():
    return get_engine()


def _get_sqlite_conn():
    return sqlite3.connect("app.db")


def init_db():
    pass


def get_user(phone_number: str) -> Optional[dict]:
    try:
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone_number).first()
            return user.to_dict() if user else None
    except Exception as e:
        print(f"[IDENTITY] Erro ao buscar usuario {phone_number}: {e}")
    return None


def get_user_by_email(email: str) -> Optional[dict]:
    try:
        with get_db() as session:
            user = session.query(User).filter_by(email=email).first()
            return user.to_dict() if user else None
    except Exception as e:
        print(f"[IDENTITY] Erro ao buscar usuario por email {email}: {e}")
    return None


def get_user_by_username(username: str) -> Optional[dict]:
    try:
        with get_db() as session:
            user = session.query(User).filter_by(username=username).first()
            return user.to_dict() if user else None
    except Exception as e:
        print(f"[IDENTITY] Erro ao buscar usuario por username {username}: {e}")
    return None


def create_user(phone_number: str):
    try:
        with get_db() as session:
            existing = session.query(User).filter_by(phone_number=phone_number).first()
            if not existing:
                session.add(User(phone_number=phone_number, onboarding_step="asking_name"))
    except Exception as e:
        print(f"[IDENTITY] Erro ao criar usuario {phone_number}: {e}")


def create_user_full(
    phone_number: str,
    username: str,
    name: str,
    email: str,
    birth_date: str,
    password_hash: str,
    google_id: str = None,
    auth_provider: str = "local",
    role: str = "user",
):
    now = datetime.now(timezone.utc)
    trial_ends = now + timedelta(days=7)

    try:
        with get_db() as session:
            existing = session.query(User).filter_by(phone_number=phone_number).first()
            if not existing:
                session.add(User(
                    phone_number=phone_number,
                    username=username,
                    name=name,
                    email=email,
                    birth_date=birth_date,
                    password_hash=password_hash,
                    google_id=google_id,
                    auth_provider=auth_provider,
                    onboarding_step="completed",
                    plan_type="trial",
                    trial_started_at=now,
                    trial_ends_at=trial_ends,
                    whatsapp_verified=False,
                    role=role,
                ))
    except Exception as e:
        print(f"[IDENTITY] Erro ao criar usuario completo {phone_number}: {e}")
        raise e


def promote_user_to_admin(phone_number: str):
    try:
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone_number).first()
            if user:
                user.role = "admin"
    except Exception as e:
        print(f"[IDENTITY] Erro ao promover {phone_number} para admin: {e}")


def demote_admin(phone_number: str):
    try:
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone_number).first()
            if user:
                user.role = "user"
    except Exception as e:
        print(f"[IDENTITY] Erro ao rebaixar {phone_number} para user: {e}")


def set_whatsapp_verified(phone_number: str):
    try:
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone_number).first()
            if user:
                user.whatsapp_verified = True
                user.onboarding_step = "completed"
    except Exception as e:
        print(f"[IDENTITY] Erro ao marcar whatsapp_verified para {phone_number}: {e}")


def link_google_account(email: str, google_id: str):
    try:
        with get_db() as session:
            user = session.query(User).filter_by(email=email).first()
            if user:
                user.google_id = google_id
                user.auth_provider = "google"
    except Exception as e:
        print(f"[IDENTITY] Erro ao vincular google_id para email {email}: {e}")


def update_stripe_customer_id(phone_number: str, customer_id: str):
    try:
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone_number).first()
            if user:
                user.stripe_customer_id = customer_id
    except Exception as e:
        print(f"[IDENTITY] Erro ao atualizar stripe_customer_id para {phone_number}: {e}")


def change_user_phone_number(old_phone_number: str, new_phone_number: str):
    try:
        if get_user(new_phone_number):
            raise ValueError("Novo telefone ja cadastrado")

        with get_db() as session:
            # FK children first to avoid constraint violations
            session.query(Task).filter_by(user_id=old_phone_number).update(
                {"user_id": new_phone_number}
            )
            session.query(Subscription).filter_by(user_id=old_phone_number).update(
                {"user_id": new_phone_number}
            )
            session.query(Reminder).filter_by(user_id=old_phone_number).update(
                {"user_id": new_phone_number}
            )
            session.query(UsageEvent).filter_by(user_id=old_phone_number).update(
                {"user_id": new_phone_number}
            )
            session.query(ChatMessage).filter_by(user_id=old_phone_number).update(
                {"user_id": new_phone_number}
            )
            session.query(User).filter_by(phone_number=old_phone_number).update(
                {"phone_number": new_phone_number}
            )
    except Exception as e:
        print(f"[IDENTITY] Erro ao trocar telefone de {old_phone_number} para {new_phone_number}: {e}")
        raise e


def update_user_name(phone_number: str, name: str):
    try:
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone_number).first()
            if user:
                user.name = name
                user.onboarding_step = "completed"
    except Exception as e:
        print(f"[IDENTITY] Erro ao atualizar nome de {phone_number}: {e}")


def update_last_seen(phone_number: str):
    """Atualiza o timestamp da ultima mensagem recebida do usuario."""
    now = datetime.now(timezone.utc)
    try:
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone_number).first()
            if user:
                user.last_seen_at = now
    except Exception as e:
        print(f"[IDENTITY] Erro ao atualizar last_seen_at de {phone_number}: {e}")


def is_new_session(user: dict, threshold_hours: int = 4) -> bool:
    """
    Retorna True se o usuario ficou mais de threshold_hours sem enviar mensagens
    (ou se nao tem last_seen_at registrado), indicando que deve receber uma saudacao.
    """
    last_seen = user.get("last_seen_at")
    if not last_seen:
        return True
    try:
        if isinstance(last_seen, str):
            last_dt = datetime.fromisoformat(last_seen)
        else:
            last_dt = last_seen
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        elapsed_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
        return elapsed_hours >= threshold_hours
    except Exception as e:
        print(f"[IDENTITY] Erro ao calcular is_new_session: {e}")
        return False


def is_plan_active(user: dict) -> bool:
    """
    Verifica se o usuario tem plano ativo (não é trial ou o trial ainda não acabou).
    """
    if user.get("role") == "admin":
        return True

    if user.get("plan_type") == "trial":
        trial_ends = user.get("trial_ends_at")
        if not trial_ends:
            return False

        try:
            if isinstance(trial_ends, str):
                trial_dt = datetime.fromisoformat(trial_ends)
            else:
                trial_dt = trial_ends

            if trial_dt.tzinfo is None:
                trial_dt = trial_dt.replace(tzinfo=timezone.utc)

            if datetime.now(timezone.utc) < trial_dt:
                return True
        except Exception:
            pass

    from src.billing.service import is_subscription_active
    return is_subscription_active(user["phone_number"])

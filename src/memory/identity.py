import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

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
        logger.error("Erro ao buscar usuario %s: %s", phone_number, e)
    return None


def get_password_hash(phone_number: str) -> Optional[str]:
    """Retorna apenas o password_hash do usuario (nunca exposto via to_dict)."""
    try:
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone_number).first()
            return user.password_hash if user else None
    except Exception as e:
        logger.error("Erro ao buscar password_hash de %s: %s", phone_number, e)
    return None


def get_password_hash_by_email(email: str) -> Optional[str]:
    """Retorna apenas o password_hash buscando por email (nunca exposto via to_dict)."""
    try:
        with get_db() as session:
            user = session.query(User).filter_by(email=email).first()
            return user.password_hash if user else None
    except Exception as e:
        logger.error("Erro ao buscar password_hash por email %s: %s", email, e)
    return None


def get_user_by_email(email: str) -> Optional[dict]:
    try:
        with get_db() as session:
            user = session.query(User).filter_by(email=email).first()
            return user.to_dict() if user else None
    except Exception as e:
        logger.error("Erro ao buscar usuario por email %s: %s", email, e)
    return None


def get_user_by_username(username: str) -> Optional[dict]:
    try:
        with get_db() as session:
            user = session.query(User).filter_by(username=username).first()
            return user.to_dict() if user else None
    except Exception as e:
        logger.error("Erro ao buscar usuario por username %s: %s", username, e)
    return None


def create_user(phone_number: str):
    try:
        with get_db() as session:
            existing = session.query(User).filter_by(phone_number=phone_number).first()
            if not existing:
                session.add(User(phone_number=phone_number, onboarding_step="asking_name"))
    except Exception as e:
        logger.error("Erro ao criar usuario %s: %s", phone_number, e)


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
    terms_accepted_version: str = None,
):
    now = datetime.now(timezone.utc)

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
                    plan_type="free",
                    trial_started_at=now,
                    whatsapp_verified=False,
                    role=role,
                    terms_accepted_version=terms_accepted_version,
                    terms_accepted_at=now if terms_accepted_version else None,
                ))
    except Exception as e:
        logger.error("Erro ao criar usuario completo %s: %s", phone_number, e)
        raise e


def promote_user_to_admin(phone_number: str):
    try:
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone_number).first()
            if user:
                user.role = "admin"
    except Exception as e:
        logger.error("Erro ao promover %s para admin: %s", phone_number, e)


def demote_admin(phone_number: str):
    try:
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone_number).first()
            if user:
                user.role = "user"
    except Exception as e:
        logger.error("Erro ao rebaixar %s para user: %s", phone_number, e)


def set_whatsapp_verified(phone_number: str):
    try:
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone_number).first()
            if user:
                user.whatsapp_verified = True
                user.onboarding_step = "completed"
    except Exception as e:
        logger.error("Erro ao marcar whatsapp_verified para %s: %s", phone_number, e)


def link_google_account(email: str, google_id: str):
    try:
        with get_db() as session:
            user = session.query(User).filter_by(email=email).first()
            if user:
                user.google_id = google_id
                user.auth_provider = "google"
    except Exception as e:
        logger.error("Erro ao vincular google_id para email %s: %s", email, e)


def update_stripe_customer_id(phone_number: str, customer_id: str):
    try:
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone_number).first()
            if user:
                user.stripe_customer_id = customer_id
    except Exception as e:
        logger.error("Erro ao atualizar stripe_customer_id para %s: %s", phone_number, e)


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
        logger.error("Erro ao trocar telefone de %s para %s: %s", old_phone_number, new_phone_number, e)
        raise e


def update_user_name(phone_number: str, name: str):
    try:
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone_number).first()
            if user:
                user.name = name
                user.onboarding_step = "completed"
    except Exception as e:
        logger.error("Erro ao atualizar nome de %s: %s", phone_number, e)


def update_last_seen(phone_number: str):
    """Atualiza o timestamp da ultima mensagem recebida do usuario."""
    now = datetime.now(timezone.utc)
    try:
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone_number).first()
            if user:
                user.last_seen_at = now
    except Exception as e:
        logger.error("Erro ao atualizar last_seen_at de %s: %s", phone_number, e)


def get_or_rotate_session(phone_number: str, force_new: bool = False) -> str:
    """
    Returns the current session_id for the user, or creates a new one if
    ``force_new`` is True (e.g. gap > 4 h detected by ``is_new_session``).

    The rotated session_id is stored in ``users.current_session_id`` so it
    survives restarts.  Format: ``<phone>_<random8>``.
    """
    try:
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone_number).first()
            if not user:
                return phone_number  # fallback

            if user.current_session_id and not force_new:
                return user.current_session_id

            import uuid
            new_session = f"{phone_number}_{uuid.uuid4().hex[:8]}"
            user.current_session_id = new_session
            return new_session
    except Exception as e:
        logger.error("Erro ao rotacionar sessao de %s: %s", phone_number, e)
        return phone_number  # fallback


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
        logger.error("Erro ao calcular is_new_session: %s", e)
        return False


def delete_account(phone_number: str) -> bool:
    """
    Remove permanentemente todos os dados pessoais do usuario (LGPD Art. 18 VI).
    Retorna True se o usuario existia e foi removido.
    """
    from src.db.models import (
        BackgroundTask,
        Carousel,
        ImageSession,
        MessageBuffer,
        OtpCode,
        UserIntegration,
    )

    try:
        with get_db() as session:
            user = session.query(User).filter_by(phone_number=phone_number).first()
            if not user:
                return False

            # Cancela assinatura Stripe se houver
            if user.stripe_customer_id:
                try:
                    sub = session.query(Subscription).filter_by(user_id=phone_number).first()
                    if sub and sub.provider_subscription_id and sub.status in ("active", "trialing", "past_due"):
                        from src.integrations.stripe import cancel_subscription
                        cancel_subscription(sub.provider_subscription_id, immediately=True)
                except Exception as e:
                    logger.warning("Erro ao cancelar assinatura Stripe durante exclusao de conta %s: %s", phone_number[:4] + "***", e)

            # Limpa agno agent sessions (tabela gerenciada pelo agno, fora do ORM)
            try:
                session.execute(
                    __import__("sqlalchemy").text("DELETE FROM agent_sessions WHERE session_id LIKE :pattern"),
                    {"pattern": f"%{phone_number}%"},
                )
            except Exception:
                pass  # tabela pode nao existir em dev

            # Remove dados de todas as tabelas filhas
            session.query(ChatMessage).filter_by(user_id=phone_number).delete()
            session.query(Task).filter_by(user_id=phone_number).delete()
            session.query(Reminder).filter_by(user_id=phone_number).delete()
            session.query(Subscription).filter_by(user_id=phone_number).delete()
            session.query(UsageEvent).filter_by(user_id=phone_number).delete()
            session.query(UserIntegration).filter_by(user_id=phone_number).delete()
            session.query(BackgroundTask).filter_by(user_id=phone_number).delete()
            session.query(Carousel).filter_by(user_id=phone_number).delete()
            session.query(MessageBuffer).filter_by(user_id=phone_number).delete()
            session.query(OtpCode).filter_by(phone_number=phone_number).delete()

            # Remove o usuario
            session.delete(user)

        logger.info("Conta excluida com sucesso: %s***", phone_number[:4])
        return True
    except Exception as e:
        logger.error("Erro ao excluir conta %s***: %s", phone_number[:4], e)
        raise e


def is_plan_active(user: dict) -> bool:
    """
    Verifica se o usuario tem plano ativo.
    Free é sempre ativo (com limites). Premium/trial Stripe também.
    """
    if user.get("role") == "admin":
        return True

    if user.get("plan_type") in ("free", "trial"):
        return True

    from src.billing.service import is_subscription_active
    return is_subscription_active(user["phone_number"])

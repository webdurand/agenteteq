import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


def _utcnow():
    return datetime.now(timezone.utc)


# ──────────────────────────── Users & Chat ────────────────────────────


class User(Base):
    __tablename__ = "users"

    phone_number = Column(String, primary_key=True)
    name = Column(String)
    onboarding_step = Column(String, default="pending")
    last_seen_at = Column(DateTime(timezone=True))
    username = Column(String, unique=True)
    email = Column(String, unique=True)
    birth_date = Column(String)
    password_hash = Column(String)
    whatsapp_verified = Column(Boolean, default=False)
    google_id = Column(String)
    auth_provider = Column(String, default="local")
    plan_type = Column(String, default="free")
    trial_started_at = Column(DateTime(timezone=True))
    trial_ends_at = Column(DateTime(timezone=True))
    timezone = Column(String, default="America/Sao_Paulo")
    role = Column(String, default="user")
    stripe_customer_id = Column(String)
    terms_accepted_version = Column(String)
    terms_accepted_at = Column(DateTime(timezone=True))

    chat_messages = relationship("ChatMessage", back_populates="user")
    tasks = relationship("Task", back_populates="user")
    integrations = relationship("UserIntegration", back_populates="user", cascade="all, delete-orphan")

    def to_dict(self) -> dict:
        return {
            "phone_number": self.phone_number,
            "name": self.name,
            "onboarding_step": self.onboarding_step,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "username": self.username,
            "email": self.email,
            "birth_date": self.birth_date,
            "password_hash": self.password_hash,
            "whatsapp_verified": bool(self.whatsapp_verified),
            "google_id": self.google_id,
            "auth_provider": self.auth_provider,
            "plan_type": self.plan_type,
            "trial_started_at": self.trial_started_at.isoformat() if self.trial_started_at else None,
            "trial_ends_at": self.trial_ends_at.isoformat() if self.trial_ends_at else None,
            "timezone": self.timezone or "America/Sao_Paulo",
            "role": self.role or "user",
            "stripe_customer_id": self.stripe_customer_id,
            "terms_accepted_version": self.terms_accepted_version,
        }


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("idx_chat_messages_user_created", "user_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.phone_number"), nullable=False)
    session_id = Column(String, nullable=False)
    role = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    user = relationship("User", back_populates="chat_messages")


# ──────────────────────────── Integrations ────────────────────────────


class UserIntegration(Base):
    __tablename__ = "user_integrations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.phone_number"), nullable=False)
    provider = Column(String, nullable=False)  # ex: "google", "slack", "notion"
    account_id = Column(String)  # ID unico no provedor (ex: sub do Google)
    account_email = Column(String)  # Email ou nome para exibicao
    access_token = Column(Text)
    refresh_token = Column(Text)
    scopes = Column(Text)  # Lista de scopes separados por virgula
    expires_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    user = relationship("User", back_populates="integrations")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "provider": self.provider,
            "account_id": self.account_id,
            "account_email": self.account_email,
            "scopes": self.scopes.split(",") if self.scopes else [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            # Nao retornamos tokens para o frontend
        }


# ──────────────────────────── Tasks ────────────────────────────


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.phone_number"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text)
    due_date = Column(String)
    location = Column(String)
    notes = Column(Text)
    status = Column(String, default="pending")
    created_at = Column(String, nullable=False)

    user = relationship("User", back_populates="tasks")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "due_date": self.due_date,
            "location": self.location,
            "notes": self.notes,
            "status": self.status,
            "created_at": self.created_at,
        }


# ──────────────────────────── Reminders ────────────────────────────


class Reminder(Base):
    __tablename__ = "reminders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, nullable=False)
    title = Column(String)
    task_instructions = Column(Text, nullable=False)
    trigger_type = Column(String, nullable=False)
    trigger_config = Column(Text, nullable=False)
    notification_channel = Column(String, default="whatsapp_text")
    status = Column(String, default="active")
    apscheduler_job_id = Column(String)
    created_at = Column(String, nullable=False)
    updated_at = Column(String)

    def to_dict(self) -> dict:
        import json
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "task_instructions": self.task_instructions,
            "trigger_type": self.trigger_type,
            "trigger_config": json.loads(self.trigger_config) if self.trigger_config else {},
            "notification_channel": self.notification_channel,
            "status": self.status,
            "apscheduler_job_id": self.apscheduler_job_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ──────────────────────────── Carousels ────────────────────────────


class Carousel(Base):
    __tablename__ = "carousels"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False)
    title = Column(String)
    status = Column(String, default="generating")
    slides = Column(Text, default="[]")
    reference_images = Column(Text, default="[]")
    created_at = Column(String, nullable=False)
    updated_at = Column(String)

    def to_dict(self) -> dict:
        import json
        slides = self.slides or "[]"
        refs = self.reference_images or "[]"
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "status": self.status,
            "slides": json.loads(slides) if isinstance(slides, str) else slides,
            "reference_images": json.loads(refs) if isinstance(refs, str) else refs,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ──────────────────────────── Billing ────────────────────────────


class BillingPlan(Base):
    __tablename__ = "billing_plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text)
    features_json = Column(Text, default="[]")
    limits_json = Column(Text, default="{}")
    is_active = Column(Boolean, default=True)
    trial_days = Column(Integer, default=7)
    stripe_product_id = Column(String)
    stripe_price_id = Column(String)
    amount_cents = Column(Integer, nullable=False)
    currency = Column(String, default="brl")
    interval = Column(String, default="month")
    created_at = Column(String, default=lambda: _utcnow().isoformat())
    updated_at = Column(String, default=lambda: _utcnow().isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "description": self.description,
            "features_json": self.features_json,
            "limits_json": self.limits_json or "{}",
            "is_active": bool(self.is_active),
            "trial_days": self.trial_days,
            "stripe_product_id": self.stripe_product_id,
            "stripe_price_id": self.stripe_price_id,
            "amount_cents": self.amount_cents,
            "currency": self.currency,
            "interval": self.interval,
        }


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, nullable=False)
    plan_code = Column(String, nullable=False)
    provider = Column(String, default="stripe")
    provider_customer_id = Column(String, nullable=False)
    provider_subscription_id = Column(String, unique=True, nullable=False)
    status = Column(String, nullable=False, default="trialing")
    trial_start = Column(String)
    trial_end = Column(String)
    current_period_start = Column(String)
    current_period_end = Column(String)
    cancel_at_period_end = Column(Boolean, default=False)
    canceled_at = Column(String)
    ended_at = Column(String)
    payment_method_summary = Column(String)
    last_invoice_id = Column(String)
    created_at = Column(String, default=lambda: _utcnow().isoformat())
    updated_at = Column(String, default=lambda: _utcnow().isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "plan_code": self.plan_code,
            "provider_customer_id": self.provider_customer_id,
            "provider_subscription_id": self.provider_subscription_id,
            "status": self.status,
            "trial_end": self.trial_end,
            "current_period_end": self.current_period_end,
            "cancel_at_period_end": bool(self.cancel_at_period_end),
            "payment_method_summary": self.payment_method_summary,
            "last_invoice_id": self.last_invoice_id,
        }


class BillingEvent(Base):
    __tablename__ = "billing_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String, unique=True, nullable=False)
    event_type = Column(String, nullable=False)
    payload_json = Column(Text)
    processed_at = Column(String, default=lambda: _utcnow().isoformat())


class RefundLog(Base):
    __tablename__ = "refund_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    subscription_id = Column(Integer)
    stripe_refund_id = Column(String)
    amount_cents = Column(Integer)
    reason = Column(Text)
    requested_by = Column(String)
    status = Column(String, default="processed")
    created_at = Column(String, default=lambda: _utcnow().isoformat())


# ──────────────────────────── Analytics ────────────────────────────


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, nullable=False)
    channel = Column(String, nullable=False)
    event_type = Column(String, nullable=False)
    tool_name = Column(String)
    status = Column(String)
    latency_ms = Column(Integer)
    created_at = Column(String, default=lambda: _utcnow().isoformat())


# ──────────────────────────── In-app Campaigns ────────────────────────────


class InAppCampaign(Base):
    __tablename__ = "in_app_campaigns"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    image_url = Column(String)
    cta_label = Column(String)
    cta_action = Column(String, default="open_checkout")
    cta_url = Column(String)
    audience = Column(String, default="all")  # all | free_only | paid_only
    frequency = Column(String, default="once")  # once | per_session | daily
    priority = Column(Integer, default=100)
    active = Column(Boolean, default=True)
    starts_at = Column(String)
    ends_at = Column(String)
    created_at = Column(String, default=lambda: _utcnow().isoformat())
    updated_at = Column(String, default=lambda: _utcnow().isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "message": self.message,
            "image_url": self.image_url,
            "cta_label": self.cta_label,
            "cta_action": self.cta_action,
            "cta_url": self.cta_url,
            "audience": self.audience,
            "frequency": self.frequency,
            "priority": self.priority,
            "active": bool(self.active),
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ──────────────────────────── System Config ────────────────────────────


class SystemConfig(Base):
    __tablename__ = "system_config"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)
    updated_at = Column(String, default=lambda: _utcnow().isoformat())


# ──────────────────────────── Background Tasks (Queue) ────────────────────────────


class BackgroundTask(Base):
    __tablename__ = "background_tasks"
    __table_args__ = (
        Index("idx_bg_tasks_status", "status", "created_at"),
        Index("idx_bg_tasks_user", "user_id", "status"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, nullable=False)
    task_type = Column(String, nullable=False)
    channel = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    payload = Column(Text, nullable=False)
    result = Column(Text)
    attempts = Column(Integer, default=0)
    created_at = Column(String, default=lambda: _utcnow().isoformat())
    updated_at = Column(String, default=lambda: _utcnow().isoformat())
    started_at = Column(String)
    completed_at = Column(String)


# ──────────────────────────── Message Buffer ────────────────────────────


class MessageBuffer(Base):
    __tablename__ = "message_buffer"

    user_id = Column(String, primary_key=True)
    events = Column(Text, nullable=False, default="[]")
    flush_at = Column(String, nullable=False)
    created_at = Column(String, default=lambda: _utcnow().isoformat())


# ──────────────────────────── Deduplication ────────────────────────────


class ProcessedMessage(Base):
    __tablename__ = "processed_messages"

    message_id = Column(String, primary_key=True)
    created_at = Column(String, default=lambda: _utcnow().isoformat())


# ──────────────────────────── Image Sessions ────────────────────────────


class ImageSession(Base):
    __tablename__ = "image_sessions"

    session_id = Column(String, primary_key=True)
    image_type = Column(String, primary_key=True)
    image_index = Column(Integer, primary_key=True, default=0)
    image_url = Column(String, nullable=False)
    created_at = Column(String, default=lambda: _utcnow().isoformat())

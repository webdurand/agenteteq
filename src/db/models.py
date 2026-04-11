import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
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
    trend_alerts_enabled = Column(String, default="false")
    last_trend_alert_at = Column(DateTime(timezone=True))
    current_session_id = Column(String, nullable=True)

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
            "current_session_id": self.current_session_id,
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


# ──────────────────────────── OTP Codes ────────────────────────────


class OtpCode(Base):
    __tablename__ = "otp_codes"

    phone_number = Column(String, primary_key=True)
    code = Column(String(6), nullable=False)
    purpose = Column(String, nullable=False)
    attempts = Column(Integer, default=0)
    expires_at = Column(DateTime(timezone=True), nullable=False)


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
    priority = Column(String)       # high, medium, low
    category = Column(String)       # user-defined label
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
            "priority": self.priority,
            "category": self.category,
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
    workflow_id = Column(String)
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
            "workflow_id": self.workflow_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ──────────────────────────── Workflows ────────────────────────────


class Workflow(Base):
    __tablename__ = "workflows"
    __table_args__ = (
        Index("idx_workflows_user_status", "user_id", "status"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, nullable=False)
    title = Column(String)
    original_request = Column(Text, nullable=False)
    steps = Column(Text, nullable=False, default="[]")
    status = Column(String, nullable=False, default="draft")
    current_step = Column(Integer, default=0)
    notification_channel = Column(String)
    last_run_at = Column(String)
    created_at = Column(String, default=lambda: _utcnow().isoformat())
    updated_at = Column(String, default=lambda: _utcnow().isoformat())

    def to_dict(self) -> dict:
        import json
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "original_request": self.original_request,
            "steps": json.loads(self.steps) if isinstance(self.steps, str) else self.steps,
            "status": self.status,
            "current_step": self.current_step,
            "notification_channel": self.notification_channel,
            "last_run_at": self.last_run_at,
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
    type = Column(String, default="carousel")
    file_url = Column(String)
    generation_mode = Column(String, default="ai")  # "ai" | "html" | "hybrid"
    reference_image_url = Column(String, nullable=True)
    style_analysis = Column(Text, nullable=True)

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
            "type": self.type or "carousel",
            "file_url": self.file_url or "",
            "generation_mode": self.generation_mode or "ai",
            "reference_image_url": self.reference_image_url or "",
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
            "current_period_start": self.current_period_start,
            "current_period_end": self.current_period_end,
            "cancel_at_period_end": bool(self.cancel_at_period_end),
            "payment_method_summary": self.payment_method_summary,
            "last_invoice_id": self.last_invoice_id,
        }


class BudgetAddOn(Base):
    __tablename__ = "budget_addons"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, nullable=False)
    amount_usd = Column(Float, nullable=False)
    purchased_at = Column(String, nullable=False, default=lambda: _utcnow().isoformat())
    expires_at = Column(String, nullable=False)
    stripe_payment_id = Column(String)


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
    extra_data = Column(Text)
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


# ──────────────────────────── Brand Profiles ────────────────────────────


class BrandProfile(Base):
    __tablename__ = "brand_profiles"
    __table_args__ = (
        Index("idx_brand_profiles_user", "user_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    is_default = Column(Boolean, default=False)
    primary_color = Column(String, default="#1A1A2E")
    secondary_color = Column(String, default="#16213E")
    accent_color = Column(String, default="#E94560")
    bg_color = Column(String, default="#0F0F0F")
    text_primary_color = Column(String, default="#FFFFFF")
    text_secondary_color = Column(String, default="#D0D0D0")
    font_heading = Column(String, default="Inter Bold")
    font_body = Column(String, default="Inter")
    logo_url = Column(String)
    style_description = Column(Text)
    tone_of_voice = Column(String)
    target_audience = Column(String)
    created_at = Column(String, default=lambda: _utcnow().isoformat())
    updated_at = Column(String, default=lambda: _utcnow().isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "is_default": bool(self.is_default),
            "primary_color": self.primary_color,
            "secondary_color": self.secondary_color,
            "accent_color": self.accent_color,
            "bg_color": self.bg_color,
            "text_primary_color": self.text_primary_color,
            "text_secondary_color": self.text_secondary_color,
            "font_heading": self.font_heading,
            "font_body": self.font_body,
            "logo_url": self.logo_url,
            "style_description": self.style_description,
            "tone_of_voice": self.tone_of_voice,
            "target_audience": self.target_audience,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ──────────────────────────── Carousel Presets ────────────────────────────


class CarouselPreset(Base):
    __tablename__ = "carousel_presets"
    __table_args__ = (
        Index("idx_carousel_presets_user", "user_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    brand_profile_id = Column(Integer, nullable=True)  # optional link to brand_profiles.id
    style_anchor = Column(Text)                        # shared visual identity descriptor
    color_palette_json = Column(Text, default="{}")    # JSON: {primary, accent, text_primary, text_secondary}
    default_format = Column(String, default="1350x1080")
    default_slide_count = Column(Integer, default=5)
    sequential_slides = Column(Boolean, default=True)
    created_at = Column(String, default=lambda: _utcnow().isoformat())
    updated_at = Column(String, default=lambda: _utcnow().isoformat())

    def to_dict(self) -> dict:
        import json
        palette = {}
        try:
            palette = json.loads(self.color_palette_json or "{}")
        except Exception:
            pass
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "brand_profile_id": self.brand_profile_id,
            "style_anchor": self.style_anchor,
            "color_palette": palette,
            "default_format": self.default_format,
            "default_slide_count": self.default_slide_count,
            "sequential_slides": bool(self.sequential_slides),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ──────────────────────────── Style References ────────────────────────────


class StyleReference(Base):
    __tablename__ = "style_references"
    __table_args__ = (
        Index("idx_style_references_user", "user_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, nullable=False)
    brand_profile_id = Column(Integer, nullable=True)
    image_url = Column(String, nullable=False)
    title = Column(String, default="")
    source_url = Column(String, default="")
    extracted_colors = Column(Text, default="{}")
    style_description = Column(Text, default="")
    tags = Column(String, default="")
    created_at = Column(String, default=lambda: _utcnow().isoformat())

    def to_dict(self) -> dict:
        import json
        colors = {}
        try:
            colors = json.loads(self.extracted_colors or "{}")
        except Exception:
            pass
        return {
            "id": self.id,
            "user_id": self.user_id,
            "brand_profile_id": self.brand_profile_id,
            "image_url": self.image_url,
            "title": self.title,
            "source_url": self.source_url,
            "extracted_colors": colors,
            "style_description": self.style_description,
            "tags": self.tags,
            "created_at": self.created_at,
        }


# ──────────────────────────── Image Sessions ────────────────────────────


# ──────────────────────────── Social Monitoring ────────────────────────────


class TrackedAccount(Base):
    __tablename__ = "tracked_accounts"
    __table_args__ = (
        Index("idx_tracked_accounts_user", "user_id", "platform"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, nullable=False)
    platform = Column(String, nullable=False)           # "instagram", "youtube", "tiktok"
    username = Column(String, nullable=False)
    display_name = Column(String)
    profile_url = Column(String)
    profile_pic_url = Column(String)
    bio = Column(Text)
    followers_count = Column(Integer)
    posts_count = Column(Integer)
    metadata_json = Column(Text, default="{}")
    status = Column(String, default="active")           # active, paused, error
    last_fetched_at = Column(String)
    created_at = Column(String, nullable=False)
    updated_at = Column(String)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "platform": self.platform,
            "username": self.username,
            "display_name": self.display_name,
            "profile_url": self.profile_url,
            "profile_pic_url": self.profile_pic_url,
            "bio": self.bio,
            "followers_count": self.followers_count,
            "posts_count": self.posts_count,
            "status": self.status,
            "last_fetched_at": self.last_fetched_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class SocialContent(Base):
    __tablename__ = "social_content"
    __table_args__ = (
        Index("idx_social_content_account", "tracked_account_id", "posted_at"),
        Index("idx_social_content_platform_id", "platform", "platform_post_id", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tracked_account_id = Column(Integer, ForeignKey("tracked_accounts.id"), nullable=False)
    user_id = Column(String, nullable=False)
    platform = Column(String, nullable=False)
    platform_post_id = Column(String, nullable=False)
    content_type = Column(String)                        # image, video, carousel, reel
    caption = Column(Text)
    hashtags_json = Column(Text, default="[]")
    media_urls_json = Column(Text, default="[]")
    thumbnail_url = Column(String)
    likes_count = Column(Integer, default=0)
    comments_count = Column(Integer, default=0)
    views_count = Column(Integer, default=0)
    posted_at = Column(String)
    fetched_at = Column(String, nullable=False)
    analysis_summary = Column(Text)

    def to_dict(self) -> dict:
        import json
        return {
            "id": self.id,
            "tracked_account_id": self.tracked_account_id,
            "platform": self.platform,
            "platform_post_id": self.platform_post_id,
            "content_type": self.content_type,
            "caption": self.caption,
            "hashtags": json.loads(self.hashtags_json) if self.hashtags_json else [],
            "media_urls": json.loads(self.media_urls_json) if self.media_urls_json else [],
            "thumbnail_url": self.thumbnail_url,
            "likes_count": self.likes_count,
            "comments_count": self.comments_count,
            "views_count": self.views_count,
            "posted_at": self.posted_at,
            "fetched_at": self.fetched_at,
            "analysis_summary": self.analysis_summary,
        }


class ContentPlan(Base):
    __tablename__ = "content_plans"
    __table_args__ = (
        Index("idx_content_plans_user", "user_id", "status"),
        Index("idx_content_plans_schedule", "user_id", "scheduled_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text)
    content_type = Column(String, default="post")       # post, carousel, video, reels, blog
    platforms = Column(Text, default="[]")               # JSON array
    scheduled_at = Column(String)                        # ISO 8601
    status = Column(String, default="idea")              # idea, planned, producing, ready, published
    carousel_id = Column(Integer)                        # optional link to carousel
    notes = Column(Text)
    created_at = Column(String, nullable=False)
    updated_at = Column(String)

    def to_dict(self) -> dict:
        import json
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "description": self.description,
            "content_type": self.content_type,
            "platforms": json.loads(self.platforms) if self.platforms else [],
            "scheduled_at": self.scheduled_at,
            "status": self.status,
            "carousel_id": self.carousel_id,
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ImageSession(Base):
    __tablename__ = "image_sessions"

    session_id = Column(String, primary_key=True)
    image_type = Column(String, primary_key=True)
    image_index = Column(Integer, primary_key=True, default=0)
    image_url = Column(String, nullable=False)
    created_at = Column(String, default=lambda: _utcnow().isoformat())


# ──────────────────────────── Canvas Sessions ────────────────────────────


class SharedAsset(Base):
    __tablename__ = "shared_assets"
    __table_args__ = (
        Index("idx_shared_assets_category", "category", "asset_type"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    tags = Column(String, nullable=False, default="")          # comma-separated
    category = Column(String, default="icon")                  # icon, image, pattern
    asset_type = Column(String, default="svg")                 # svg, png, webp
    source = Column(String, default="seed")                    # seed, generated, uploaded
    url = Column(String, nullable=False)                       # Cloudinary URL
    thumbnail_url = Column(String)
    metadata_json = Column(Text, default="{}")                 # svg_content, etc.
    created_by = Column(String)                                # user_id or null for seed
    usage_count = Column(Integer, default=0)
    created_at = Column(String, default=lambda: _utcnow().isoformat())

    def to_dict(self) -> dict:
        import json
        meta = {}
        try:
            meta = json.loads(self.metadata_json or "{}")
        except Exception:
            pass
        return {
            "id": self.id,
            "name": self.name,
            "tags": [t.strip() for t in (self.tags or "").split(",") if t.strip()],
            "category": self.category,
            "asset_type": self.asset_type,
            "source": self.source,
            "url": self.url,
            "thumbnail_url": self.thumbnail_url,
            "metadata": meta,
            "created_by": self.created_by,
            "usage_count": self.usage_count,
            "created_at": self.created_at,
        }


class CanvasSession(Base):
    __tablename__ = "canvas_sessions"
    __table_args__ = (
        Index("idx_canvas_sessions_user", "user_id", "status"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, nullable=False)
    title = Column(String, default="")
    canvas_json = Column(Text, nullable=False)
    thumbnail_url = Column(String)
    status = Column(String, default="active")       # active, archived
    format = Column(String, default="1080x1080")
    created_at = Column(String, default=lambda: _utcnow().isoformat())
    updated_at = Column(String, default=lambda: _utcnow().isoformat())

    def to_dict(self) -> dict:
        import json
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "canvas_json": json.loads(self.canvas_json) if isinstance(self.canvas_json, str) else self.canvas_json,
            "thumbnail_url": self.thumbnail_url,
            "status": self.status,
            "format": self.format,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ──────────────────────────── Video Creation ────────────────────────────


class UserAvatar(Base):
    """User's avatar for video generation (Kling I2V and HeyGen).
    Stores reference photos/video frames and HeyGen avatar IDs for
    generating realistic clips of the user in different scenarios."""
    __tablename__ = "user_avatars"
    __table_args__ = (
        Index("idx_user_avatars_user_active", "user_id", "is_active"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, nullable=False)
    media_type = Column(String)          # "photo" | "video"
    media_urls = Column(Text)            # JSON array: original upload URLs (1-4 photos or 1 video)
    reference_frames = Column(Text)      # JSON array: image URLs used for I2V generation
    #   photo → same as media_urls
    #   video → auto-extracted key frames
    is_active = Column(Boolean, default=True)
    label = Column(String)               # optional label, e.g. "professional look"
    voice_id = Column(String)            # ElevenLabs cloned voice ID
    voice_sample_url = Column(String)    # URL of the first audio used to clone the voice
    voice_samples = Column(Text)         # JSON array: all audio sample URLs used for voice cloning
    voice_name = Column(String)          # Display name of the cloned voice
    # HeyGen-specific fields
    heygen_group_id = Column(String)     # HeyGen photo avatar group ID
    heygen_avatar_id = Column(String)    # HeyGen talking_photo_id or digital_twin_id
    heygen_avatar_type = Column(String)  # "photo_avatar" | "digital_twin"
    heygen_voice_id = Column(String)     # HeyGen cloned/selected voice ID
    heygen_training_status = Column(String)  # "pending" | "training" | "completed" | "failed"
    heygen_training_video_url = Column(String)   # Cloudinary URL of training footage (2-5 min)
    heygen_consent_video_url = Column(String)    # Cloudinary URL of consent video
    created_at = Column(String, default=lambda: _utcnow().isoformat())

    def to_dict(self) -> dict:
        import json
        return {
            "id": self.id,
            "user_id": self.user_id,
            "media_type": self.media_type,
            "media_urls": json.loads(self.media_urls or "[]"),
            "reference_frames": json.loads(self.reference_frames or "[]"),
            "is_active": self.is_active,
            "label": self.label,
            "voice_id": self.voice_id,
            "voice_sample_url": self.voice_sample_url,
            "voice_samples": json.loads(self.voice_samples or "[]"),
            "voice_name": self.voice_name,
            "has_voice": bool(self.voice_id or self.heygen_voice_id),
            "heygen_group_id": self.heygen_group_id,
            "heygen_avatar_id": self.heygen_avatar_id,
            "heygen_avatar_type": self.heygen_avatar_type or "photo_avatar",
            "heygen_voice_id": self.heygen_voice_id,
            "heygen_training_status": self.heygen_training_status,
            "has_heygen": bool(self.heygen_avatar_id),
            "has_digital_twin": self.heygen_avatar_type == "digital_twin",
            "created_at": self.created_at,
        }


class VideoScript(Base):
    __tablename__ = "video_scripts"
    __table_args__ = (
        Index("idx_video_scripts_user", "user_id"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, nullable=False)
    topic = Column(String)
    style = Column(String)              # template: tutorial, storytelling, listicle, etc.
    framework = Column(String)          # PAS, BAB, AIDA, STAR
    duration_target = Column(Integer)   # seconds
    script_json = Column(Text)          # JSON: {hook, scenes[], cta, config}
    reference_account = Column(String)
    created_at = Column(String, default=lambda: _utcnow().isoformat())

    def to_dict(self) -> dict:
        import json
        script = {}
        try:
            script = json.loads(self.script_json or "{}")
        except (json.JSONDecodeError, TypeError):
            pass
        return {
            "id": self.id,
            "user_id": self.user_id,
            "topic": self.topic,
            "style": self.style,
            "framework": self.framework,
            "duration_target": self.duration_target,
            "script": script,
            "reference_account": self.reference_account,
            "created_at": self.created_at,
        }


class VideoProject(Base):
    __tablename__ = "video_projects"
    __table_args__ = (
        Index("idx_video_projects_user", "user_id", "status"),
    )

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id = Column(String, unique=True, nullable=True)  # idempotency key → BackgroundTask.id
    user_id = Column(String, nullable=False)
    script_id = Column(String)
    source_type = Column(String)        # "avatar" | "real" | "ai_motion"
    source_url = Column(String)         # foto ou video upload URL
    status = Column(String, default="draft")
    # draft -> generating_voice -> syncing_captions -> generating_avatar ->
    # generating_broll -> assembling -> encoding -> uploading -> done | failed
    current_step = Column(String)
    voice_url = Column(String)
    captions_json = Column(Text)        # word-level timestamps JSON
    assets_json = Column(Text)          # {talking_head_url, broll_urls[], overlay_urls[]}
    video_url = Column(String)          # URL final (Instagram quality)
    video_url_whatsapp = Column(String) # versao otimizada WhatsApp (<16MB)
    thumbnail_url = Column(String)
    share_token = Column(String)        # token para link publico
    duration = Column(Integer)          # duracao real em segundos
    cost_cents = Column(Integer, default=0)
    content_plan_id = Column(Integer)
    error_message = Column(String)
    created_at = Column(String, default=lambda: _utcnow().isoformat())
    updated_at = Column(String, default=lambda: _utcnow().isoformat())

    def to_dict(self) -> dict:
        import json
        assets = {}
        try:
            assets = json.loads(self.assets_json or "{}")
        except (json.JSONDecodeError, TypeError):
            pass
        return {
            "id": self.id,
            "user_id": self.user_id,
            "script_id": self.script_id,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "status": self.status,
            "current_step": self.current_step,
            "video_url": self.video_url,
            "video_url_whatsapp": self.video_url_whatsapp,
            "thumbnail_url": self.thumbnail_url,
            "share_token": self.share_token,
            "duration": self.duration,
            "cost_cents": self.cost_cents,
            "error_message": self.error_message,
            "assets": assets,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

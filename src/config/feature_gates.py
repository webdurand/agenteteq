"""
Feature gates e limites diários genéricos por plano.

Limites são lidos de BillingPlan.limits_json (editável pelo admin no tab Planos).
"""
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func

from src.config.system_config import get_config
from src.db.models import BackgroundTask, UsageEvent
from src.db.session import get_db
from src.memory.identity import get_user
from src.models.subscriptions import FREE_LIMITS


# ---------------------------------------------------------------------------
# Plan resolution
# ---------------------------------------------------------------------------

def get_user_plan(user_id: str) -> dict:
    """
    Retorna o BillingPlan dict efetivo do usuário.
    - Admin com bypass → {"code": "admin", "limits_json": "{}", ...}
    - Subscription ativa → plano da subscription
    - Sem subscription → plano "free"
    """
    user = get_user(user_id)
    if not user:
        return _get_free_plan()

    if user.get("role") == "admin":
        bypass = get_config("admin_bypass_limits", "true").lower() in ("true", "1", "yes")
        if bypass:
            return {"code": "admin", "name": "Admin", "limits_json": "{}", "_is_admin": True}

    from src.billing.service import is_subscription_active
    from src.models.subscriptions import get_active_subscription, get_plan

    if is_subscription_active(user_id):
        sub = get_active_subscription(user_id)
        if sub and sub.get("plan_code"):
            plan = get_plan(sub["plan_code"], initialize=False)
            if plan:
                return plan

    return _get_free_plan()


def _get_free_plan() -> dict:
    from src.models.subscriptions import get_plan
    plan = get_plan("free", initialize=False)
    if plan:
        return plan
    # Fallback if "free" plan not yet in DB
    return {
        "code": "free",
        "name": "Free",
        "limits_json": json.dumps(FREE_LIMITS),
    }


def get_user_plan_type(user_id: str) -> str:
    """Backward compat: returns 'admin', 'paid', or 'trial'/'free'."""
    plan = get_user_plan(user_id)
    if plan.get("_is_admin"):
        return "admin"
    code = plan.get("code", "free")
    if code == "free":
        return "trial"
    return "paid"


def _get_limit(plan: dict, key: str, default: Any = 0) -> Any:
    """Read a limit value from plan's limits_json."""
    if plan.get("_is_admin"):
        # Admin bypass: booleans → True, numbers → very large
        if "enabled" in key:
            return True
        return 999999
    try:
        limits = json.loads(plan.get("limits_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        limits = {}
    return limits.get(key, default)


# ---------------------------------------------------------------------------
# Feature enabled/disabled
# ---------------------------------------------------------------------------

def is_feature_enabled(user_id: str, feature_key: str) -> bool:
    """Checa se uma feature on/off está habilitada para o plano do usuário."""
    plan = get_user_plan(user_id)
    val = _get_limit(plan, feature_key, False)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes")
    return bool(val)


# ---------------------------------------------------------------------------
# Daily numeric limits
# ---------------------------------------------------------------------------

def _count_usage_events(user_id: str, tool_name: str, hours: int = 24) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with get_db() as session:
        count = (
            session.query(func.count(UsageEvent.id))
            .filter(
                UsageEvent.user_id == user_id,
                UsageEvent.tool_name == tool_name,
                UsageEvent.created_at >= cutoff,
            )
            .scalar()
        ) or 0
    return int(count)


def _count_background_tasks(user_id: str, hours: int = 24) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with get_db() as session:
        count = (
            session.query(func.count(BackgroundTask.id))
            .filter(
                BackgroundTask.user_id == user_id,
                BackgroundTask.created_at >= cutoff,
            )
            .scalar()
        ) or 0
    return int(count)


def _sum_voice_minutes(user_id: str, hours: int = 24) -> float:
    """Soma minutos de sessões voice_live nas últimas N horas."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with get_db() as session:
        total_ms = (
            session.query(func.sum(UsageEvent.latency_ms))
            .filter(
                UsageEvent.user_id == user_id,
                UsageEvent.event_type == "voice_live_session",
                UsageEvent.created_at >= cutoff,
            )
            .scalar()
        ) or 0
    return int(total_ms) / 60_000.0


def check_daily_feature_limit(
    user_id: str, feature_key: str
) -> Optional[str]:
    """
    Retorna mensagem de erro se o limite diário foi atingido, None se pode prosseguir.
    feature_key é a chave em limits_json, ex: "max_searches_daily".
    """
    plan = get_user_plan(user_id)
    if plan.get("_is_admin"):
        return None

    limit = int(_get_limit(plan, feature_key, 0))

    if limit <= 0:
        return f"Essa funcionalidade não está disponível no seu plano atual."

    used = _count_usage_events(user_id, feature_key)
    if used >= limit:
        return (
            f"Você atingiu seu limite diário de {limit} uso(s) para esta funcionalidade. "
            f"Tente novamente amanhã!"
        )
    return None


def check_voice_live_minutes(user_id: str) -> Optional[str]:
    """Checa se o usuário ainda tem minutos de voz live disponíveis hoje."""
    plan = get_user_plan(user_id)
    if plan.get("_is_admin"):
        return None

    limit = int(_get_limit(plan, "voice_live_max_minutes_daily", 0))

    if limit <= 0:
        return "O modo voz real-time não está disponível no seu plano atual."

    used = _sum_voice_minutes(user_id)
    if used >= limit:
        return (
            f"Você usou seus {limit} minutos diários de voz real-time. "
            f"Tente novamente amanhã!"
        )
    return None


def log_feature_usage(user_id: str, feature_key: str, channel: str = "web"):
    """Registra uma utilização de feature para contabilizar limites."""
    from src.memory.analytics import log_event
    log_event(
        user_id=user_id,
        channel=channel,
        event_type="feature_usage",
        tool_name=feature_key,
        status="success",
    )


# ---------------------------------------------------------------------------
# Limit helpers for task_queue (concurrent + daily)
# ---------------------------------------------------------------------------

def get_plan_limit(user_id: str, key: str, default: int = 0) -> int:
    """Get a numeric limit from the user's plan. Used by task_queue."""
    plan = get_user_plan(user_id)
    return int(_get_limit(plan, key, default))


def is_admin_unlimited(user_id: str) -> bool:
    """Check if user is admin with bypass."""
    plan = get_user_plan(user_id)
    return bool(plan.get("_is_admin"))


# ---------------------------------------------------------------------------
# Usage summary (para o endpoint /api/usage/limits)
# ---------------------------------------------------------------------------

_COUNTABLE_FEATURES = {
    "max_tasks_per_user_daily": {
        "key": "images",
        "label": "Geração de imagens",
        "counter": "background_tasks",
    },
    "max_searches_daily": {
        "key": "web_search",
        "label": "Buscas na web",
        "counter": "usage_events",
    },
    "max_deep_research_daily": {
        "key": "deep_research",
        "label": "Pesquisa profunda",
        "counter": "usage_events",
    },
}

_TOGGLEABLE_FEATURES = {
    "voice_live_enabled": {
        "key": "voice_live",
        "label": "Voz real-time",
    },
    "tts_enabled": {
        "key": "tts",
        "label": "Síntese de voz (TTS)",
    },
}

_MINUTE_FEATURES = {
    "voice_live_max_minutes_daily": {
        "key": "voice_live_minutes",
        "label": "Minutos de voz real-time",
    },
}


def get_all_usage_summary(user_id: str) -> dict:
    """
    Retorna o resumo completo de uso de todas as features limitadas.
    Formato:
    {
      "plan_name": "Free" | "Plano Premium",
      "plan_code": "free" | "premium",
      "resets_at": "...",
      "features": {
        "images": { "enabled": True, "limit": 5, "used": 3, "remaining": 2, "label": "..." },
        "voice_live": { "enabled": False, "label": "..." },
        ...
      }
    }
    """
    plan = get_user_plan(user_id)
    is_admin = bool(plan.get("_is_admin"))
    plan_name = plan.get("name", "Free")
    plan_code = plan.get("code", "free")

    now = datetime.now(timezone.utc)
    resets_at = (now + timedelta(hours=24)).isoformat()

    features: dict = {}

    # Countable features (com limite numérico diário)
    for config_key, meta in _COUNTABLE_FEATURES.items():
        fkey = meta["key"]
        label = meta["label"]

        if is_admin:
            features[fkey] = {"enabled": True, "limit": -1, "used": 0, "remaining": -1, "label": label, "unlimited": True}
            continue

        limit = int(_get_limit(plan, config_key, 0))

        if meta["counter"] == "background_tasks":
            used = _count_background_tasks(user_id)
        else:
            used = _count_usage_events(user_id, config_key)

        remaining = max(0, limit - used)
        features[fkey] = {
            "enabled": limit > 0,
            "limit": limit,
            "used": used,
            "remaining": remaining,
            "label": label,
        }

    # Toggleable features (on/off por plano)
    for config_key, meta in _TOGGLEABLE_FEATURES.items():
        fkey = meta["key"]
        label = meta["label"]

        if is_admin:
            features[fkey] = {"enabled": True, "label": label}
            continue

        val = _get_limit(plan, config_key, False)
        enabled = val if isinstance(val, bool) else str(val).lower() in ("true", "1", "yes")
        features[fkey] = {"enabled": enabled, "label": label}

    # Minute-based features
    for config_key, meta in _MINUTE_FEATURES.items():
        fkey = meta["key"]
        label = meta["label"]

        if is_admin:
            features[fkey] = {"enabled": True, "limit": -1, "used": 0, "remaining": -1, "label": label, "unlimited": True}
            continue

        limit = int(_get_limit(plan, config_key, 0))

        used_min = round(_sum_voice_minutes(user_id), 1)
        remaining = max(0, round(limit - used_min, 1))
        features[fkey] = {
            "enabled": limit > 0,
            "limit": limit,
            "used": used_min,
            "remaining": remaining,
            "label": label,
            "unit": "min",
        }

    return {
        "plan_name": plan_name,
        "plan_code": plan_code,
        "resets_at": resets_at,
        "features": features,
    }


# ---------------------------------------------------------------------------
# Enriched limits context for agent prompt injection
# ---------------------------------------------------------------------------

def get_limits_context(user_id: str) -> str:
    """
    Builds an enriched [STATUS LIMITES] block with all features,
    80% threshold warnings, and upgrade link when relevant.
    Injected into every user message so the agent has accurate limit info.
    """
    plan = get_user_plan(user_id)
    is_admin = bool(plan.get("_is_admin"))

    if is_admin:
        return (
            "[STATUS LIMITES: Usuario admin com bypass ativo. "
            "Limites diarios NAO se aplicam agora. "
            "Ignore mensagens antigas sobre limite atingido.]"
        )

    plan_name = plan.get("name", "Free")
    plan_code = plan.get("code", "free")

    parts = []
    any_near = False
    any_hit = False

    # Countable features (images, searches, deep research)
    for config_key, meta in _COUNTABLE_FEATURES.items():
        limit = int(_get_limit(plan, config_key, 0))
        if limit <= 0:
            continue
        if meta["counter"] == "background_tasks":
            used = _count_background_tasks(user_id)
        else:
            used = _count_usage_events(user_id, config_key)
        remaining = max(0, limit - used)
        ratio = used / limit

        if remaining == 0:
            parts.append(f"{meta['label']}: LIMITE ATINGIDO ({used}/{limit})")
            any_hit = True
        elif ratio >= 0.8:
            parts.append(f"{meta['label']}: {remaining}/{limit} restantes (⚠️ quase no limite)")
            any_near = True
        else:
            parts.append(f"{meta['label']}: {remaining}/{limit} restantes")

    # Minute-based features (voice live)
    for config_key, meta in _MINUTE_FEATURES.items():
        limit = int(_get_limit(plan, config_key, 0))
        if limit <= 0:
            continue
        used = round(_sum_voice_minutes(user_id), 1)
        remaining = max(0, round(limit - used, 1))
        ratio = used / limit if limit > 0 else 0

        if remaining <= 0:
            parts.append(f"{meta['label']}: LIMITE ATINGIDO ({used}/{limit}min)")
            any_hit = True
        elif ratio >= 0.8:
            parts.append(f"{meta['label']}: {remaining}/{limit}min restantes (⚠️)")
            any_near = True
        else:
            parts.append(f"{meta['label']}: {remaining}/{limit}min restantes")

    # Disabled features worth mentioning
    disabled = []
    for config_key, meta in _TOGGLEABLE_FEATURES.items():
        val = _get_limit(plan, config_key, False)
        enabled = val if isinstance(val, bool) else str(val).lower() in ("true", "1", "yes")
        if not enabled:
            disabled.append(meta["label"])
    if disabled:
        parts.append(f"Nao disponivel no plano: {', '.join(disabled)}")

    features_str = ". ".join(parts) if parts else "Uso normal"

    # Upgrade hint
    upgrade_hint = ""
    if plan_code == "free":
        frontend_url = os.getenv("FRONTEND_URL", os.getenv("FRONTEND_ORIGIN", "http://localhost:5173"))
        upgrade_url = f"{frontend_url}/dashboard?tab=account"
        if any_hit or any_near:
            upgrade_hint = f" Usuario no plano gratuito perto/no limite — sugira upgrade naturalmente. Link: {upgrade_url}"
        else:
            upgrade_hint = f" Plano gratuito — link de upgrade disponivel: {upgrade_url}"

    return f"[STATUS LIMITES: Plano {plan_name}. {features_str}.{upgrade_hint}]"

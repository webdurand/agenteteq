"""
Feature gates e limites diários genéricos por plano.

Permite habilitar/desabilitar features e impor limites numéricos diários
usando as configs de system_config (editáveis pelo admin).
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func

from src.billing.service import is_subscription_active
from src.config.system_config import get_config, get_config_for_plan
from src.db.models import BackgroundTask, UsageEvent
from src.db.session import get_db
from src.memory.identity import get_user


# ---------------------------------------------------------------------------
# Plan type helpers (mesma lógica de task_queue._get_plan_type)
# ---------------------------------------------------------------------------

def get_user_plan_type(user_id: str) -> str:
    user = get_user(user_id)
    if not user:
        return "trial"
    if user.get("role") == "admin":
        return "admin"
    if is_subscription_active(user_id):
        return "paid"
    return "trial"


def _effective_plan(plan_type: str) -> str:
    if plan_type == "admin":
        bypass = get_config("admin_bypass_limits", "true").lower() in ("true", "1", "yes")
        return "admin" if bypass else "trial"
    return plan_type


# ---------------------------------------------------------------------------
# Feature enabled/disabled
# ---------------------------------------------------------------------------

def is_feature_enabled(user_id: str, feature_key: str) -> bool:
    """Checa se uma feature on/off está habilitada para o plano do usuário."""
    plan_type = get_user_plan_type(user_id)
    effective = _effective_plan(plan_type)
    if effective == "admin":
        return True
    val = get_config_for_plan(feature_key, effective, "true")
    return val.lower() in ("true", "1", "yes")


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

    feature_key mapeia para a config `{feature_key}:{plan_type}`.
    Ex: "max_searches_daily" -> "max_searches_daily:paid" = "50"
    """
    plan_type = get_user_plan_type(user_id)
    effective = _effective_plan(plan_type)
    if effective == "admin":
        return None

    limit_str = get_config_for_plan(feature_key, effective, "0")
    try:
        limit = int(limit_str)
    except ValueError:
        return None

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
    plan_type = get_user_plan_type(user_id)
    effective = _effective_plan(plan_type)
    if effective == "admin":
        return None

    limit_str = get_config_for_plan("voice_live_max_minutes_daily", effective, "0")
    try:
        limit = int(limit_str)
    except ValueError:
        return None

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
# Usage summary (para o endpoint /api/usage/limits)
# ---------------------------------------------------------------------------

# Mapa de features limitadas: config_key -> (label, counting_method)
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
      "plan_name": "free" | "premium",
      "resets_at": "...",
      "features": {
        "images": { "enabled": True, "limit": 5, "used": 3, "remaining": 2, "label": "..." },
        "voice_live": { "enabled": False, "label": "..." },
        ...
      }
    }
    """
    plan_type = get_user_plan_type(user_id)
    effective = _effective_plan(plan_type)
    is_admin = effective == "admin"
    plan_name = "premium" if effective in ("paid", "admin") else "free"

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

        limit_str = get_config_for_plan(config_key, effective, "0")
        try:
            limit = int(limit_str)
        except ValueError:
            limit = 0

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

        val = get_config_for_plan(config_key, effective, "false")
        enabled = val.lower() in ("true", "1", "yes")
        features[fkey] = {"enabled": enabled, "label": label}

    # Minute-based features
    for config_key, meta in _MINUTE_FEATURES.items():
        fkey = meta["key"]
        label = meta["label"]

        if is_admin:
            features[fkey] = {"enabled": True, "limit": -1, "used": 0, "remaining": -1, "label": label, "unlimited": True}
            continue

        limit_str = get_config_for_plan(config_key, effective, "0")
        try:
            limit = int(limit_str)
        except ValueError:
            limit = 0

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
        "resets_at": resets_at,
        "features": features,
    }

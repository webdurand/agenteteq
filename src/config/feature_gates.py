"""
Feature gates e budget único por plano.

Modelo:
  1. Feature gates (boolean) — quais features o plano dá acesso
  2. Budget único (0-100%) — quanto o usuário pode usar no período

Internamente o budget é em USD. O usuário só vê a porcentagem.
Reset alinha com ciclo Stripe (paid) ou mês calendário (free).
"""
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func

from src.config.system_config import get_config
from src.db.models import BackgroundTask, BudgetAddOn, UsageEvent
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
        if "enabled" in key:
            return True
        return 999999
    try:
        limits = json.loads(plan.get("limits_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        limits = {}
    return limits.get(key, default)


# ---------------------------------------------------------------------------
# Feature gates (boolean on/off por plano)
# ---------------------------------------------------------------------------

_FEATURE_GATES = {
    "voice_live_enabled": {"label": "Voz real-time"},
    "tts_enabled": {"label": "Síntese de voz (TTS)"},
    "social_monitoring_enabled": {"label": "Monitoramento social"},
    "deep_research_enabled": {"label": "Pesquisa profunda"},
    "canvas_editor_enabled": {"label": "Editor Canvas"},
    "video_creation_enabled": {"label": "Criação de vídeo"},
    "ai_motion_enabled": {"label": "AI Motion (vídeo realista com cenários)"},
}


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
# Budget system — single monthly budget (0-100%)
# ---------------------------------------------------------------------------

_COST_EVENT_TYPES = (
    "llm_usage", "whisper_transcription", "apify_call", "rapidapi_call",
    "cloudinary_upload", "web_search_cost", "image_generation",
    "tts_synthesis", "voice_live_session",
    "video_voice", "video_talking_head", "video_broll", "video_render",
)


def _get_billing_period(user_id: str) -> tuple[str, str]:
    """
    Retorna (start, end) do período de billing atual.
    - Paid: ciclo Stripe (current_period_start/end)
    - Free: mês calendário (1º ao 1º)
    """
    try:
        from src.models.subscriptions import get_active_subscription
        sub = get_active_subscription(user_id)
        if sub and sub.get("current_period_start") and sub.get("current_period_end"):
            return sub["current_period_start"], sub["current_period_end"]
    except Exception:
        pass

    # Fallback: calendar month
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    next_month = (now.replace(day=28) + timedelta(days=4)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    return month_start, next_month.isoformat()


def _sum_costs_in_period(user_id: str, start: str, end: str) -> float:
    """Soma TODOS os custos rastreados (USD) entre start e end."""
    with get_db() as session:
        rows = (
            session.query(UsageEvent.extra_data)
            .filter(
                UsageEvent.user_id == user_id,
                UsageEvent.event_type.in_(_COST_EVENT_TYPES),
                UsageEvent.created_at >= start,
                UsageEvent.created_at <= end,
            )
            .all()
        )
    total = 0.0
    for (ed,) in rows:
        try:
            meta = json.loads(ed) if ed else {}
            total += meta.get("cost_usd", 0) or 0
        except (json.JSONDecodeError, TypeError):
            pass
    return round(total, 4)


def _get_addon_budget(user_id: str) -> float:
    """Soma amount_usd de todos os add-ons não expirados do usuário."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        with get_db() as session:
            total = (
                session.query(func.sum(BudgetAddOn.amount_usd))
                .filter(
                    BudgetAddOn.user_id == user_id,
                    BudgetAddOn.expires_at > now,
                )
                .scalar()
            )
        return float(total) if total else 0.0
    except Exception:
        # Table may not exist yet (pre-migration)
        return 0.0


def _get_budget_info(user_id: str) -> tuple[float, float, float, str]:
    """
    Returns (used, total_budget, pct, resets_at).
    Central helper for budget calculations.
    """
    plan = get_user_plan(user_id)
    if plan.get("_is_admin"):
        return 0.0, 999999.0, 0.0, ""

    base_budget = float(_get_limit(plan, "monthly_budget_usd", 0))
    addon_budget = _get_addon_budget(user_id)
    total_budget = base_budget + addon_budget

    if total_budget <= 0:
        return 0.0, 0.0, 0.0, ""

    start, end = _get_billing_period(user_id)
    used = _sum_costs_in_period(user_id, start, end)
    pct = min(100.0, round(used / total_budget * 100, 1))

    return used, total_budget, pct, end


def check_budget(user_id: str) -> Optional[str]:
    """
    Checa se o usuário tem budget disponível.
    Retorna mensagem de erro se esgotado, None se pode prosseguir.
    """
    plan = get_user_plan(user_id)
    if plan.get("_is_admin"):
        return None

    used, total_budget, pct, resets_at = _get_budget_info(user_id)

    if total_budget <= 0:
        return None

    if used >= total_budget:
        return (
            "Seu limite mensal encerrou! "
            "Assine o premium para mais cota. "
            "Caso ja seja premium, contate a nossa equipe para conseguir mais limite."
        )
    return None


# Backward compat aliases
check_monthly_total_budget = check_budget


def get_budget_summary(user_id: str) -> dict:
    """
    Retorna resumo do budget para o frontend (barra 0-100%).
    """
    plan = get_user_plan(user_id)
    is_admin = bool(plan.get("_is_admin"))
    plan_name = plan.get("name", "Free")
    plan_code = plan.get("code", "free")

    used, total_budget, pct, resets_at = _get_budget_info(user_id)

    # Feature gates
    features: dict = {}
    for config_key, meta in _FEATURE_GATES.items():
        if is_admin:
            features[config_key] = {"enabled": True, "label": meta["label"]}
        else:
            val = _get_limit(plan, config_key, False)
            enabled = val if isinstance(val, bool) else str(val).lower() in ("true", "1", "yes")
            features[config_key] = {"enabled": enabled, "label": meta["label"]}

    return {
        "plan_name": plan_name,
        "plan_code": plan_code,
        "budget": {
            "used_pct": pct if not is_admin else 0.0,
            "unlimited": is_admin,
            "resets_at": resets_at,
        },
        "features": features,
    }


def get_all_usage_summary(user_id: str) -> dict:
    """
    Retorna resumo de uso para o endpoint /api/usage/limits.
    Formato simplificado: barra única + feature gates.
    """
    summary = get_budget_summary(user_id)

    budget = summary["budget"]

    # Build features dict in the format the frontend expects
    features: dict = {}

    # Single budget bar
    features["budget"] = {
        "enabled": True,
        "limit": 100,
        "used": budget["used_pct"],
        "remaining": round(100 - budget["used_pct"], 1),
        "label": "Uso mensal",
        "unlimited": budget.get("unlimited", False),
        "period": "monthly",
    }

    # Feature gates (show as enabled/disabled)
    for config_key, meta in summary["features"].items():
        features[config_key] = {
            "enabled": meta["enabled"],
            "label": meta["label"],
        }

    return {
        "plan_name": summary["plan_name"],
        "plan_code": summary["plan_code"],
        "resets_at": budget["resets_at"],
        "monthly_resets_at": budget["resets_at"],
        "features": features,
    }


# ---------------------------------------------------------------------------
# Agent prompt context (simplified)
# ---------------------------------------------------------------------------

def get_limits_context(user_id: str) -> str:
    """
    Builds [STATUS LIMITES] block for agent prompt injection.
    Simplified to single budget percentage.
    """
    plan = get_user_plan(user_id)
    is_admin = bool(plan.get("_is_admin"))

    if is_admin:
        return (
            "[STATUS LIMITES: Usuario admin com bypass ativo. "
            "Limites NAO se aplicam. "
            "Ignore mensagens antigas sobre limite atingido.]"
        )

    plan_name = plan.get("name", "Free")
    plan_code = plan.get("code", "free")

    used, total_budget, pct, resets_at = _get_budget_info(user_id)

    parts = []

    # Budget status
    if total_budget > 0:
        if used >= total_budget:
            parts.append(
                f"Limite mensal ATINGIDO ({pct:.0f}%). "
                "O usuario NAO pode usar funcionalidades que geram custo. "
                "Informe de forma gentil e sugira comprar mais limite ou aguardar o reset."
            )
        elif pct >= 80:
            parts.append(f"Uso mensal: {pct:.0f}% (quase no limite)")
        else:
            parts.append(f"Uso mensal: {pct:.0f}%")

    # Disabled features
    disabled = []
    for config_key, meta in _FEATURE_GATES.items():
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
        if pct >= 80 or used >= total_budget:
            upgrade_hint = f" Usuario no plano gratuito perto/no limite — sugira upgrade naturalmente. Link: {upgrade_url}"
        else:
            upgrade_hint = f" Plano gratuito — link de upgrade disponivel: {upgrade_url}"

    return f"[STATUS LIMITES: Plano {plan_name}. {features_str}.{upgrade_hint}]"


# ---------------------------------------------------------------------------
# Backward compat helpers (used by task_queue for concurrent limits)
# ---------------------------------------------------------------------------

def get_plan_limit(user_id: str, key: str, default: int = 0) -> int:
    """Get a numeric limit from the user's plan. Used by task_queue."""
    plan = get_user_plan(user_id)
    return int(_get_limit(plan, key, default))


def is_admin_unlimited(user_id: str) -> bool:
    """Check if user is admin with bypass."""
    plan = get_user_plan(user_id)
    return bool(plan.get("_is_admin"))


def get_monthly_total_cost(user_id: str) -> tuple[float, float]:
    """Backward compat: retorna (custo_usado, budget_limite)."""
    used, total_budget, _, _ = _get_budget_info(user_id)
    return used, total_budget


# ---------------------------------------------------------------------------
# Deprecated stubs (prevent ImportError in not-yet-updated code)
# ---------------------------------------------------------------------------

def check_daily_feature_limit(user_id: str, feature_key: str) -> Optional[str]:
    """DEPRECATED: Use check_budget() instead. Kept to prevent ImportError."""
    return check_budget(user_id)


def log_feature_usage(user_id: str, feature_key: str, channel: str = "web"):
    """DEPRECATED: No-op. Cost is tracked via log_event(cost_usd=X)."""
    pass


def check_voice_live_minutes(user_id: str) -> Optional[str]:
    """DEPRECATED: Use check_budget() instead."""
    return check_budget(user_id)


def check_video_analysis_limit(user_id: str) -> Optional[str]:
    """DEPRECATED: Use check_budget() instead."""
    return check_budget(user_id)


def get_video_minutes_remaining(user_id: str) -> float:
    """DEPRECATED: Budget-based now. Returns large number if budget available."""
    msg = check_budget(user_id)
    return 0.0 if msg else 999999.0


def log_video_analysis(user_id: str, duration_seconds: float, channel: str = "web"):
    """DEPRECATED: No-op. Cost tracked via log_event."""
    pass


def check_monthly_llm_budget(user_id: str) -> bool:
    """DEPRECATED: Returns True if budget exceeded."""
    msg = check_budget(user_id)
    return msg is not None

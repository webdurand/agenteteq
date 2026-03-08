import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, text

from src.auth.deps import require_admin
from src.db.models import (
    BackgroundTask,
    BillingPlan,
    InAppCampaign,
    Subscription,
    Task,
    UsageEvent,
    User,
)
from src.db.session import _is_sqlite, get_db
from src.memory.identity import get_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminCreateRequest(BaseModel):
    phone_number: str


class CampaignCreateRequest(BaseModel):
    title: str
    message: str
    image_url: str | None = None
    cta_label: str | None = "Experimentar Premium"
    cta_action: str | None = "open_checkout"
    cta_url: str | None = None
    audience: str | None = "all"
    frequency: str | None = "once"
    priority: int | None = 100
    active: bool = True
    starts_at: str | None = None
    ends_at: str | None = None


class CampaignUpdateRequest(BaseModel):
    title: str | None = None
    message: str | None = None
    image_url: str | None = None
    cta_label: str | None = None
    cta_action: str | None = None
    cta_url: str | None = None
    audience: str | None = None
    frequency: str | None = None
    priority: int | None = None
    active: bool | None = None
    starts_at: str | None = None
    ends_at: str | None = None


def _validate_campaign_fields(campaign: dict):
    if campaign.get("audience") not in ("all", "free_only", "paid_only"):
        raise HTTPException(status_code=400, detail="audience inválida")
    if campaign.get("frequency") not in ("once", "per_session", "daily"):
        raise HTTPException(status_code=400, detail="frequency inválida")
    if campaign.get("cta_action") not in ("open_checkout", "open_account", "external_url"):
        raise HTTPException(status_code=400, detail="cta_action inválida")
    if campaign.get("cta_action") == "external_url" and not campaign.get("cta_url"):
        raise HTTPException(status_code=400, detail="cta_url é obrigatório para external_url")


@router.get("/business/summary")
def get_business_summary(user: dict = Depends(require_admin)):
    try:
        with get_db() as session:
            total_users = session.query(func.count(User.phone_number)).scalar()
            verified_users = (
                session.query(func.count(User.phone_number))
                .filter(User.whatsapp_verified == True)  # noqa: E712
                .scalar()
            )
            total_msgs = (
                session.query(func.count(UsageEvent.id))
                .filter(UsageEvent.event_type == "message_received")
                .scalar()
            )
        return {
            "total_users": total_users,
            "verified_users": verified_users,
            "total_messages": total_msgs,
        }
    except Exception:
        logger.exception("Erro ao buscar resumo de negócios")
        raise HTTPException(status_code=500, detail="Erro interno do servidor")


@router.get("/business/users")
def list_users(user: dict = Depends(require_admin)):
    try:
        pg_query = """
            SELECT
                u.phone_number, u.name, u.email, u.role, u.last_seen_at,
                u.trial_started_at, u.trial_ends_at,
                s.status, s.plan_code, s.current_period_end
            FROM users u
            LEFT JOIN LATERAL (
                SELECT status, plan_code, current_period_end
                FROM subscriptions
                WHERE user_id = u.phone_number
                ORDER BY updated_at DESC NULLS LAST
                LIMIT 1
            ) s ON true
            ORDER BY u.trial_started_at DESC NULLS LAST
        """

        sqlite_query = """
            SELECT
                u.phone_number, u.name, u.email, u.role, u.last_seen_at,
                u.trial_started_at, u.trial_ends_at,
                s.status, s.plan_code, s.current_period_end
            FROM users u
            LEFT JOIN (
                SELECT s1.user_id, s1.status, s1.plan_code, s1.current_period_end
                FROM subscriptions s1
                INNER JOIN (
                    SELECT user_id, MAX(updated_at) AS max_updated
                    FROM subscriptions
                    GROUP BY user_id
                ) latest ON s1.user_id = latest.user_id AND s1.updated_at = latest.max_updated
            ) s ON u.phone_number = s.user_id
            ORDER BY u.trial_started_at DESC
        """

        with get_db() as session:
            query = sqlite_query if _is_sqlite() else pg_query
            rows = session.execute(text(query)).fetchall()

        now_utc = datetime.now(timezone.utc)
        users = []
        for row in rows:
            stripe_status = row[7]
            trial_ends_at = row[6]

            if stripe_status:
                eff_status = "pro_trial" if stripe_status == "trialing" else stripe_status
            elif trial_ends_at:
                if isinstance(trial_ends_at, str):
                    try:
                        trial_dt = datetime.fromisoformat(trial_ends_at)
                        if trial_dt.tzinfo is None:
                            trial_dt = trial_dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        trial_dt = None
                else:
                    trial_dt = trial_ends_at
                    if trial_dt and trial_dt.tzinfo is None:
                        trial_dt = trial_dt.replace(tzinfo=timezone.utc)
                eff_status = "trialing" if trial_dt and now_utc < trial_dt else "expired"
            else:
                eff_status = "none"

            users.append({
                "phone_number": row[0],
                "name": row[1],
                "email": row[2],
                "role": row[3],
                "last_seen_at": str(row[4]) if row[4] else None,
                "created_at": str(row[5]) if row[5] else None,
                "trial_ends_at": str(row[6]) if row[6] else None,
                "subscription_status": eff_status,
                "plan_code": row[8],
                "current_period_end": str(row[9]) if row[9] else None,
            })
        return users
    except Exception:
        logger.exception("Erro ao listar usuários")
        raise HTTPException(status_code=500, detail="Erro interno do servidor")


@router.get("/business/tools")
def get_tools_summary(user: dict = Depends(require_admin)):
    try:
        with get_db() as session:
            rows = (
                session.query(UsageEvent.tool_name, func.count(UsageEvent.id))
                .filter(UsageEvent.event_type == "tool_called")
                .group_by(UsageEvent.tool_name)
                .order_by(func.count(UsageEvent.id).desc())
                .all()
            )
        return [{"name": r[0], "calls": r[1]} for r in rows]
    except Exception:
        logger.exception("Erro ao buscar resumo de ferramentas")
        raise HTTPException(status_code=500, detail="Erro interno do servidor")


@router.get("/health/summary")
def get_health_summary(user: dict = Depends(require_admin)):
    db_status = "ok"
    try:
        with get_db() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Health check do banco falhou")
        db_status = "error"
    return {
        "status": "online" if db_status == "ok" else "degraded",
        "database": db_status,
    }


@router.post("/admins")
def add_admin(req: AdminCreateRequest, current_user: dict = Depends(require_admin)):
    from src.memory.identity import promote_user_to_admin

    target = get_user(req.phone_number)
    if not target:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    promote_user_to_admin(req.phone_number)
    return {"message": f"Usuário {req.phone_number} promovido a admin com sucesso"}


@router.delete("/admins/{phone_number}")
def remove_admin(phone_number: str, current_user: dict = Depends(require_admin)):
    from src.memory.identity import demote_admin

    target = get_user(phone_number)
    if not target:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if current_user.get("phone_number") == phone_number:
        raise HTTPException(
            status_code=400,
            detail="Você não pode remover seu próprio acesso de admin",
        )
    demote_admin(phone_number)
    return {"message": f"Usuário {phone_number} rebaixado para user com sucesso"}


# ============================================================================
# IN-APP CAMPAIGNS
# ============================================================================


@router.get("/campaigns")
def list_campaigns(current_user: dict = Depends(require_admin)):
    with get_db() as session:
        rows = session.query(InAppCampaign).order_by(
            InAppCampaign.priority.asc(),
            InAppCampaign.updated_at.desc(),
        ).all()
        return [row.to_dict() for row in rows]


@router.post("/campaigns")
def create_campaign(req: CampaignCreateRequest, current_user: dict = Depends(require_admin)):
    payload = req.dict()
    _validate_campaign_fields(payload)
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_db() as session:
        row = InAppCampaign(
            title=req.title,
            message=req.message,
            image_url=req.image_url,
            cta_label=req.cta_label,
            cta_action=req.cta_action,
            cta_url=req.cta_url,
            audience=req.audience,
            frequency=req.frequency,
            priority=req.priority,
            active=req.active,
            starts_at=req.starts_at,
            ends_at=req.ends_at,
            created_at=now_iso,
            updated_at=now_iso,
        )
        session.add(row)
        session.flush()
        return row.to_dict()


@router.put("/campaigns/{campaign_id}")
def update_campaign(campaign_id: int, req: CampaignUpdateRequest, current_user: dict = Depends(require_admin)):
    updates = {k: v for k, v in req.dict(exclude_unset=True).items() if v is not None}

    with get_db() as session:
        row = session.query(InAppCampaign).filter(InAppCampaign.id == campaign_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Campanha não encontrada")

        preview = {**row.to_dict(), **updates}
        _validate_campaign_fields(preview)

        for key, value in updates.items():
            setattr(row, key, value)

        row.updated_at = datetime.now(timezone.utc).isoformat()
        session.flush()
        return row.to_dict()


@router.delete("/campaigns/{campaign_id}")
def delete_campaign(campaign_id: int, current_user: dict = Depends(require_admin)):
    with get_db() as session:
        row = session.query(InAppCampaign).filter(InAppCampaign.id == campaign_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Campanha não encontrada")
        session.delete(row)
    return {"message": "Campanha removida com sucesso"}


# ============================================================================
# SYSTEM & QUEUE ENDPOINTS
# ============================================================================

from src.config.system_config import get_all_configs, set_config


@router.get("/system/queue")
def get_queue_status(user: dict = Depends(require_admin)):
    status_counts = {
        "pending": 0,
        "processing": 0,
        "done_today": 0,
        "failed_today": 0,
        "avg_wait": 0,
    }
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    with get_db() as session:
        counts = (
            session.query(BackgroundTask.status, func.count(BackgroundTask.id))
            .filter(
                or_(
                    BackgroundTask.created_at >= cutoff,
                    BackgroundTask.status.in_(["pending", "processing"]),
                )
            )
            .group_by(BackgroundTask.status)
            .all()
        )
        for row in counts:
            if row[0] == "pending":
                status_counts["pending"] = row[1]
            elif row[0] == "processing":
                status_counts["processing"] = row[1]
            elif row[0] == "done":
                status_counts["done_today"] = row[1]
            elif row[0] == "failed":
                status_counts["failed_today"] = row[1]

    from src.queue.task_queue import _get_avg_processing_time

    status_counts["avg_wait"] = round(_get_avg_processing_time(), 1)
    return status_counts


@router.get("/system/config")
def list_system_configs(user: dict = Depends(require_admin)):
    return get_all_configs()


class ConfigUpdateRequest(BaseModel):
    key: str
    value: str


@router.put("/system/config")
def update_system_config(
    req: ConfigUpdateRequest, user: dict = Depends(require_admin)
):
    set_config(req.key, req.value)
    return {"message": "Configuração atualizada com sucesso"}


@router.get("/system/tasks")
def list_system_tasks(
    status: str = None, limit: int = 50, user: dict = Depends(require_admin)
):
    with get_db() as session:
        q = session.query(BackgroundTask)
        if status:
            q = q.filter(BackgroundTask.status == status)
        rows = q.order_by(BackgroundTask.created_at.desc()).limit(limit).all()
        tasks = []
        for t in rows:
            tasks.append({
                "id": t.id,
                "user_id": t.user_id,
                "task_type": t.task_type,
                "channel": t.channel,
                "status": t.status,
                "created_at": t.created_at,
                "started_at": t.started_at,
                "completed_at": t.completed_at,
                "attempts": t.attempts,
                "result": t.result,
            })
    return {"tasks": tasks}


@router.post("/system/tasks/{task_id}/retry")
def retry_task(task_id: str, user: dict = Depends(require_admin)):
    with get_db() as session:
        task = session.query(BackgroundTask).filter_by(id=task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task não encontrada")
        task.status = "pending"
        task.attempts = 0
    return {"message": "Task enviada para retry"}


@router.post("/system/tasks/{task_id}/cancel")
def cancel_task(task_id: str, user: dict = Depends(require_admin)):
    with get_db() as session:
        task = session.query(BackgroundTask).filter_by(id=task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task não encontrada")
        if task.status not in ("pending", "processing"):
            raise HTTPException(
                status_code=400, detail="Task não pode ser cancelada neste estado"
            )
        task.status = "failed"
        task.result = '{"error": "cancelled by admin"}'
    return {"message": "Task cancelada"}


@router.get("/system/metrics")
def get_system_metrics(days: int = 7, user: dict = Depends(require_admin)):
    if _is_sqlite():
        return {"error": "Not supported on SQLite"}

    metrics: dict = {"tools_usage": [], "user_usage": [], "plan_avg": []}

    with get_db() as session:
        rows = session.execute(
            text("""
                SELECT tool_name, COUNT(*) as count
                FROM usage_events
                WHERE event_type = 'tool_called'
                      AND created_at >= NOW() - make_interval(days => :d)
                GROUP BY tool_name ORDER BY count DESC LIMIT 10
            """),
            {"d": days},
        ).fetchall()
        metrics["tools_usage"] = [{"name": r[0], "count": r[1]} for r in rows]

        rows = session.execute(
            text("""
                SELECT user_id, COUNT(*) as count
                FROM background_tasks
                WHERE task_type = 'carousel' AND status = 'done'
                      AND created_at >= NOW() - make_interval(days => :d)
                GROUP BY user_id ORDER BY count DESC LIMIT 10
            """),
            {"d": days},
        ).fetchall()
        metrics["user_usage"] = [{"user_id": r[0], "generates": r[1]} for r in rows]

    return metrics


@router.get("/business/analytics")
def get_business_analytics(days: int = 30, user: dict = Depends(require_admin)):
    analytics: dict = {
        "financial": {
            "mrr_cents": 0,
            "active_subs": 0,
            "status_distribution": [],
            "conversion_rate": 0,
            "churn_rate": 0,
        },
        "engagement": {
            "dau": [],
            "new_users_by_day": [],
            "messages_by_day": [],
            "channel_distribution": [],
        },
        "features": {
            "tools_ranking": [],
            "tools_trend_by_day": [],
            "tasks_stats": {},
            "reminders_active": 0,
            "carousel_stats": {},
            "image_edit_stats": {},
        },
        "operational": {"error_rate": 0, "latency_by_tool": []},
    }

    try:
        with get_db() as session:
            if _is_sqlite():
                _fill_analytics_sqlite(session, analytics, days)
            else:
                _fill_analytics_pg(session, analytics, days)
    except Exception:
        logger.exception("Erro ao buscar analytics")

    return analytics


# ---------------------------------------------------------------------------
# Analytics helpers (raw SQL kept for PG-specific constructs)
# ---------------------------------------------------------------------------


def _fill_analytics_pg(session, analytics: dict, days: int) -> None:
    mrr = session.execute(text(
        "SELECT COALESCE(SUM(amount_cents), 0) "
        "FROM subscriptions s JOIN billing_plans p ON s.plan_code = p.code "
        "WHERE s.status = 'active'"
    )).scalar()
    analytics["financial"]["mrr_cents"] = int(mrr or 0)

    active_subs = session.execute(text(
        "SELECT COUNT(*) FROM subscriptions WHERE status IN ('active', 'trialing')"
    )).scalar()
    analytics["financial"]["active_subs"] = int(active_subs or 0)

    rows = session.execute(text(
        "SELECT status, COUNT(*) FROM subscriptions GROUP BY status"
    )).fetchall()
    analytics["financial"]["status_distribution"] = [
        {"name": r[0] or "unknown", "value": r[1]} for r in rows
    ]

    rows = session.execute(text("""
        SELECT DATE(created_at) as d, COUNT(DISTINCT user_id)
        FROM usage_events
        WHERE created_at >= NOW() - make_interval(days => :d)
        GROUP BY d ORDER BY d
    """), {"d": days}).fetchall()
    analytics["engagement"]["dau"] = [
        {"date": str(r[0]), "users": r[1]} for r in rows
    ]

    rows = session.execute(text("""
        SELECT DATE(created_at) as d,
               SUM(CASE WHEN event_type = 'message_received' THEN 1 ELSE 0 END) as received,
               SUM(CASE WHEN event_type = 'message_sent' THEN 1 ELSE 0 END) as sent
        FROM usage_events
        WHERE created_at >= NOW() - make_interval(days => :d)
        GROUP BY d ORDER BY d
    """), {"d": days}).fetchall()
    analytics["engagement"]["messages_by_day"] = [
        {"date": str(r[0]), "received": r[1], "sent": r[2]} for r in rows
    ]

    rows = session.execute(text(
        "SELECT channel, COUNT(DISTINCT user_id) FROM usage_events "
        "WHERE created_at >= NOW() - make_interval(days => :d) GROUP BY channel"
    ), {"d": days}).fetchall()
    analytics["engagement"]["channel_distribution"] = [
        {"name": r[0] or "unknown", "value": r[1]} for r in rows
    ]

    rows = session.execute(text("""
        SELECT tool_name, COUNT(*)
        FROM usage_events
        WHERE event_type = 'tool_called'
              AND created_at >= NOW() - make_interval(days => :d)
        GROUP BY tool_name ORDER BY COUNT(*) DESC LIMIT 10
    """), {"d": days}).fetchall()
    analytics["features"]["tools_ranking"] = [
        {"name": r[0] or "unknown", "calls": r[1]} for r in rows
    ]

    top_tools = [r[0] for r in rows[:5] if r[0]]
    if top_tools:
        placeholders = ",".join([f":t{i}" for i in range(len(top_tools))])
        tool_params = {f"t{i}": t for i, t in enumerate(top_tools)}
        trend_rows = session.execute(text(f"""
            SELECT DATE(created_at) as d, tool_name, COUNT(*)
            FROM usage_events
            WHERE event_type = 'tool_called'
                  AND tool_name IN ({placeholders})
                  AND created_at >= NOW() - make_interval(days => :d)
            GROUP BY d, tool_name ORDER BY d
        """), {**tool_params, "d": days}).fetchall()
        trend: dict = {}
        for r in trend_rows:
            dt = str(r[0])
            if dt not in trend:
                trend[dt] = {"date": dt}
            trend[dt][r[1]] = r[2]
        analytics["features"]["tools_trend_by_day"] = list(trend.values())

    rows = session.execute(text(
        "SELECT status, COUNT(*) FROM tasks GROUP BY status"
    )).fetchall()
    for r in rows:
        analytics["features"]["tasks_stats"][r[0] or "unknown"] = r[1]

    rows = session.execute(text(
        "SELECT status, COUNT(*) FROM background_tasks "
        "WHERE task_type = 'carousel' GROUP BY status"
    )).fetchall()
    for r in rows:
        analytics["features"]["carousel_stats"][r[0] or "unknown"] = r[1]

    failed = session.execute(text(
        "SELECT COUNT(*) FROM usage_events "
        "WHERE event_type = 'tool_failed' "
        "AND created_at >= NOW() - make_interval(days => :d)"
    ), {"d": days}).scalar() or 0
    called = session.execute(text(
        "SELECT COUNT(*) FROM usage_events "
        "WHERE event_type = 'tool_called' "
        "AND created_at >= NOW() - make_interval(days => :d)"
    ), {"d": days}).scalar() or 1
    if called == 0:
        called = 1
    analytics["operational"]["error_rate"] = round((failed / called) * 100, 2)

    rows = session.execute(text("""
        SELECT tool_name, AVG(latency_ms)
        FROM usage_events
        WHERE event_type = 'tool_called' AND latency_ms IS NOT NULL
              AND created_at >= NOW() - make_interval(days => :d)
        GROUP BY tool_name ORDER BY AVG(latency_ms) DESC LIMIT 10
    """), {"d": days}).fetchall()
    analytics["operational"]["latency_by_tool"] = [
        {"name": r[0] or "unknown", "avg_ms": int(r[1] or 0)} for r in rows
    ]


def _fill_analytics_sqlite(session, analytics: dict, days: int) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    mrr = session.execute(text(
        "SELECT COALESCE(SUM(amount_cents), 0) "
        "FROM subscriptions s JOIN billing_plans p ON s.plan_code = p.code "
        "WHERE s.status = 'active'"
    )).scalar()
    analytics["financial"]["mrr_cents"] = int(mrr or 0)

    active_subs = session.execute(text(
        "SELECT COUNT(*) FROM subscriptions WHERE status IN ('active', 'trialing')"
    )).scalar()
    analytics["financial"]["active_subs"] = int(active_subs or 0)

    rows = session.execute(text(
        "SELECT status, COUNT(*) FROM subscriptions GROUP BY status"
    )).fetchall()
    analytics["financial"]["status_distribution"] = [
        {"name": r[0] or "unknown", "value": r[1]} for r in rows
    ]

    rows = session.execute(text(
        "SELECT date(created_at) as d, COUNT(DISTINCT user_id) "
        "FROM usage_events WHERE created_at >= :cutoff GROUP BY d ORDER BY d"
    ), {"cutoff": cutoff}).fetchall()
    analytics["engagement"]["dau"] = [
        {"date": r[0], "users": r[1]} for r in rows
    ]

    rows = session.execute(text("""
        SELECT date(created_at) as d,
               SUM(CASE WHEN event_type = 'message_received' THEN 1 ELSE 0 END),
               SUM(CASE WHEN event_type = 'message_sent' THEN 1 ELSE 0 END)
        FROM usage_events WHERE created_at >= :cutoff GROUP BY d ORDER BY d
    """), {"cutoff": cutoff}).fetchall()
    analytics["engagement"]["messages_by_day"] = [
        {"date": r[0], "received": r[1] or 0, "sent": r[2] or 0} for r in rows
    ]

    rows = session.execute(text(
        "SELECT channel, COUNT(DISTINCT user_id) FROM usage_events "
        "WHERE created_at >= :cutoff GROUP BY channel"
    ), {"cutoff": cutoff}).fetchall()
    analytics["engagement"]["channel_distribution"] = [
        {"name": r[0] or "unknown", "value": r[1]} for r in rows
    ]

    rows = session.execute(text(
        "SELECT tool_name, COUNT(*) FROM usage_events "
        "WHERE event_type = 'tool_called' AND created_at >= :cutoff "
        "GROUP BY tool_name ORDER BY COUNT(*) DESC LIMIT 10"
    ), {"cutoff": cutoff}).fetchall()
    analytics["features"]["tools_ranking"] = [
        {"name": r[0] or "unknown", "calls": r[1]} for r in rows
    ]

    top_tools = [r[0] for r in rows[:5] if r[0]]
    if top_tools:
        placeholders = ",".join([f":t{i}" for i in range(len(top_tools))])
        tool_params = {f"t{i}": t for i, t in enumerate(top_tools)}
        trend_rows = session.execute(text(f"""
            SELECT date(created_at) as d, tool_name, COUNT(*)
            FROM usage_events
            WHERE event_type = 'tool_called'
                  AND tool_name IN ({placeholders})
                  AND created_at >= :cutoff
            GROUP BY d, tool_name ORDER BY d
        """), {**tool_params, "cutoff": cutoff}).fetchall()
        trend: dict = {}
        for r in trend_rows:
            dt = r[0]
            if dt not in trend:
                trend[dt] = {"date": dt}
            trend[dt][r[1]] = r[2]
        analytics["features"]["tools_trend_by_day"] = list(trend.values())

    rows = session.execute(text(
        "SELECT status, COUNT(*) FROM tasks GROUP BY status"
    )).fetchall()
    for r in rows:
        analytics["features"]["tasks_stats"][r[0] or "unknown"] = r[1]

    rows = session.execute(text(
        "SELECT status, COUNT(*) FROM background_tasks "
        "WHERE task_type = 'carousel' GROUP BY status"
    )).fetchall()
    for r in rows:
        analytics["features"]["carousel_stats"][r[0] or "unknown"] = r[1]

    failed = session.execute(text(
        "SELECT COUNT(*) FROM usage_events "
        "WHERE event_type = 'tool_failed' AND created_at >= :cutoff"
    ), {"cutoff": cutoff}).scalar() or 0
    called = session.execute(text(
        "SELECT COUNT(*) FROM usage_events "
        "WHERE event_type = 'tool_called' AND created_at >= :cutoff"
    ), {"cutoff": cutoff}).scalar() or 1
    if called == 0:
        called = 1
    analytics["operational"]["error_rate"] = round((failed / called) * 100, 2)

    rows = session.execute(text(
        "SELECT tool_name, AVG(latency_ms) FROM usage_events "
        "WHERE event_type = 'tool_called' AND latency_ms IS NOT NULL "
        "AND created_at >= :cutoff "
        "GROUP BY tool_name ORDER BY AVG(latency_ms) DESC LIMIT 10"
    ), {"cutoff": cutoff}).fetchall()
    analytics["operational"]["latency_by_tool"] = [
        {"name": r[0] or "unknown", "avg_ms": int(r[1] or 0)} for r in rows
    ]

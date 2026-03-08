from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy import func

from src.auth.deps import get_current_user
from src.billing.service import is_subscription_active
from src.config.feature_gates import get_all_usage_summary
from src.db.models import BackgroundTask, InAppCampaign
from src.db.session import get_db
from src.tools.task_manager import add_task, get_tasks, complete_task, reopen_task, delete_task
from src.tools.scheduler_tool import create_scheduler_tools
from src.models.reminders import list_user_reminders
from src.events import emit_event
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])

# --- Models ---
class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    due_date: Optional[str] = ""
    location: Optional[str] = ""
    notes: Optional[str] = ""

class TaskUpdate(BaseModel):
    status: Optional[str] = None
    # No futuro, podemos adicionar suporte para atualizar outros campos da tarefa.

class ReminderCreate(BaseModel):
    task_instructions: str
    trigger_type: str
    minutes_from_now: Optional[int] = None
    run_date: Optional[str] = None
    cron_expression: Optional[str] = None
    interval_minutes: Optional[int] = None
    title: Optional[str] = ""
    notification_channel: Optional[str] = "whatsapp_text"

def _parse_iso_dt(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

# --- Tasks ---
@router.get("/tasks")
async def api_get_tasks(
    status: str = Query("pending"),
    limit: int = Query(0, ge=0),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user)
):
    user_id = current_user["phone_number"]
    return get_tasks(user_id, status=status, limit=limit, offset=offset)

@router.post("/tasks")
async def api_create_task(task: TaskCreate, background_tasks: BackgroundTasks, current_user: dict = Depends(get_current_user)):
    user_id = current_user["phone_number"]
    result = add_task(
        user_id=user_id,
        title=task.title,
        description=task.description,
        due_date=task.due_date,
        location=task.location,
        notes=task.notes
    )
    background_tasks.add_task(emit_event, user_id, "task_updated")
    return {"message": result}

@router.put("/tasks/{task_id}")
async def api_update_task(task_id: int, task: TaskUpdate, background_tasks: BackgroundTasks, current_user: dict = Depends(get_current_user)):
    user_id = current_user["phone_number"]
    if task.status == "done":
        result = complete_task(user_id, task_id)
    elif task.status == "pending":
        result = reopen_task(user_id, task_id)
    else:
        raise HTTPException(status_code=400, detail="Invalid status")
    
    background_tasks.add_task(emit_event, user_id, "task_updated")
    return {"message": result}

@router.delete("/tasks/{task_id}")
async def api_delete_task(task_id: int, background_tasks: BackgroundTasks, current_user: dict = Depends(get_current_user)):
    user_id = current_user["phone_number"]
    result = delete_task(user_id, task_id)
    background_tasks.add_task(emit_event, user_id, "task_updated")
    return {"message": result}

# --- Reminders ---
@router.get("/reminders")
async def api_get_reminders(
    status: str = Query("active"),
    limit: int = Query(0, ge=0),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user)
):
    user_id = current_user["phone_number"]

    if status == "all":
        from src.models.reminders import list_user_reminders as _list
        active_res = _list(user_id, status="active")
        fired_res = _list(user_id, status="fired")
        reminders = active_res.get("reminders", []) + fired_res.get("reminders", [])
        has_more = False
    else:
        result = list_user_reminders(user_id, status=status, limit=limit, offset=offset)
        reminders = result.get("reminders", [])
        has_more = result.get("has_more", False)

    try:
        from src.scheduler.engine import get_scheduler
        from src.memory.identity import get_user
        import zoneinfo

        scheduler = get_scheduler()
        user_data = get_user(user_id)
        user_tz_str = user_data.get("timezone", "America/Sao_Paulo") if user_data else "America/Sao_Paulo"
        user_tz = zoneinfo.ZoneInfo(user_tz_str)

        for r in reminders:
            job_id = r.get("apscheduler_job_id")
            r["next_run_str"] = None
            if job_id and r.get("status") == "active":
                job = scheduler.get_job(job_id)
                if job and job.next_run_time:
                    next_dt = job.next_run_time.astimezone(user_tz)
                    r["next_run_str"] = next_dt.isoformat()
    except Exception as e:
        logger.error("Erro ao enriquecer reminders com next_run: %s", e)

    return {"reminders": reminders, "has_more": has_more}

@router.post("/reminders")
async def api_create_reminder(rem: ReminderCreate, background_tasks: BackgroundTasks, current_user: dict = Depends(get_current_user)):
    user_id = current_user["phone_number"]
    schedule_message, _, _ = create_scheduler_tools(user_id)
    
    result = schedule_message(
        task_instructions=rem.task_instructions,
        trigger_type=rem.trigger_type,
        minutes_from_now=rem.minutes_from_now,
        run_date=rem.run_date,
        cron_expression=rem.cron_expression,
        interval_minutes=rem.interval_minutes,
        title=rem.title,
        notification_channel=rem.notification_channel
    )
    background_tasks.add_task(emit_event, user_id, "reminder_updated")
    return {"message": result}

@router.delete("/reminders/{reminder_id}")
async def api_delete_reminder(reminder_id: int, background_tasks: BackgroundTasks, current_user: dict = Depends(get_current_user)):
    user_id = current_user["phone_number"]
    _, _, cancel_schedule = create_scheduler_tools(user_id)
    result = cancel_schedule(str(reminder_id))
    background_tasks.add_task(emit_event, user_id, "reminder_updated")
    return {"message": result}

# --- Usage Limits ---
@router.get("/usage/limits")
async def api_get_usage_limits(current_user: dict = Depends(get_current_user)):
    user_id = current_user["phone_number"]
    return get_all_usage_summary(user_id)

# --- Plan Features (for frontend gating) ---
@router.get("/plan/features")
async def api_get_plan_features(current_user: dict = Depends(get_current_user)):
    user_id = current_user["phone_number"]
    summary = get_all_usage_summary(user_id)
    features = summary.get("features", {})
    result: dict = {"plan_name": summary["plan_name"]}
    for key, info in features.items():
        if "limit" in info:
            result[key] = info["enabled"]
            result[f"{key}_limit"] = info.get("limit", 0)
            result[f"{key}_remaining"] = info.get("remaining", 0)
        else:
            result[key] = info["enabled"]
    return result

# --- Campaign Popup ---
@router.get("/campaigns/active")
async def api_get_active_campaign(current_user: dict = Depends(get_current_user)):
    user_id = current_user["phone_number"]
    is_premium = is_subscription_active(user_id)
    audience = "paid_only" if is_premium else "free_only"
    now = datetime.now(timezone.utc)

    with get_db() as session:
        campaigns = (
            session.query(InAppCampaign)
            .filter(InAppCampaign.active == True)  # noqa: E712
            .order_by(InAppCampaign.priority.asc(), InAppCampaign.updated_at.desc())
            .all()
        )

    for campaign in campaigns:
        if campaign.audience not in ("all", audience):
            continue

        starts_at = _parse_iso_dt(campaign.starts_at)
        ends_at = _parse_iso_dt(campaign.ends_at)

        if starts_at and now < starts_at:
            continue
        if ends_at and now > ends_at:
            continue

        return {"campaign": campaign.to_dict()}

    return {"campaign": None}

# --- Chat History ---
@router.get("/chat/history")
async def api_get_chat_history(
    limit: int = Query(20, ge=1, le=100),
    before_id: Optional[int] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    from src.models.chat_messages import get_messages

    user_id = current_user["phone_number"]
    return get_messages(user_id=user_id, limit=limit, before_id=before_id)


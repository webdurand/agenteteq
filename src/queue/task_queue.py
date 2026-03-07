import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

from sqlalchemy import func, text

from src.db.session import get_db, _is_sqlite
from src.db.models import BackgroundTask
from src.config.system_config import get_config, get_config_for_plan
from src.memory.identity import get_user
from src.billing.service import is_subscription_active


def _get_plan_type(user_id: str) -> str:
    user = get_user(user_id)
    if not user:
        return "trial"
    if user.get("role") == "admin":
        return "admin"
    if is_subscription_active(user_id):
        return "paid"
    return "trial"


def enqueue_task(user_id: str, task_type: str, channel: str, payload: Dict[str, Any]) -> dict:
    plan_type = _get_plan_type(user_id)

    with get_db() as session:
        now_iso = datetime.now(timezone.utc).isoformat()

        if plan_type != "admin":
            max_concurrent = int(get_config_for_plan("max_tasks_per_user", plan_type, "2"))
            max_daily = int(get_config_for_plan("max_tasks_per_user_daily", plan_type, "5"))

            concurrent = session.query(func.count(BackgroundTask.id)).filter(
                BackgroundTask.user_id == user_id,
                BackgroundTask.status.in_(["pending", "processing"]),
            ).scalar()

            if concurrent >= max_concurrent:
                return {"status": "limit_reached", "pending_count": concurrent}

            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            daily = session.query(func.count(BackgroundTask.id)).filter(
                BackgroundTask.user_id == user_id,
                BackgroundTask.created_at >= cutoff,
            ).scalar()

            if daily >= max_daily:
                return {"status": "daily_limit", "daily_limit": max_daily}

        cancelled = session.query(BackgroundTask).filter(
            BackgroundTask.user_id == user_id,
            BackgroundTask.task_type == task_type,
            BackgroundTask.status == "pending",
        ).update({"status": "cancelled", "updated_at": now_iso}, synchronize_session=False)

        if cancelled:
            print(f"[QUEUE] Canceladas {cancelled} tasks pendentes ({task_type}) do usuario {user_id}")

        task = BackgroundTask(
            id=str(uuid.uuid4()),
            user_id=user_id,
            task_type=task_type,
            channel=channel,
            payload=json.dumps(payload),
            status="pending",
            created_at=now_iso,
            updated_at=now_iso,
        )
        session.add(task)
        session.flush()

        position = session.query(func.count(BackgroundTask.id)).filter(
            BackgroundTask.status == "pending",
            BackgroundTask.id != task.id,
        ).scalar() + 1

    avg_time = _get_avg_processing_time()
    max_global = int(get_config("max_global_processing", "3"))
    estimated_seconds = (position * avg_time) / max_global

    if estimated_seconds < 60:
        est_str = "menos de 1 minuto"
    elif estimated_seconds < 120:
        est_str = "1 a 2 minutos"
    else:
        est_str = f"~{int(estimated_seconds // 60)} minutos"

    return {
        "status": "queued",
        "task_id": task.id,
        "position": position,
        "estimated_wait": est_str,
    }


def _get_avg_processing_time() -> float:
    with get_db() as session:
        tasks = (
            session.query(BackgroundTask.started_at, BackgroundTask.completed_at)
            .filter(
                BackgroundTask.status == "done",
                BackgroundTask.completed_at.isnot(None),
                BackgroundTask.started_at.isnot(None),
            )
            .order_by(BackgroundTask.completed_at.desc())
            .limit(20)
            .all()
        )

    if not tasks:
        return 90.0

    total = 0.0
    count = 0
    for started, completed in tasks:
        try:
            s = datetime.fromisoformat(started)
            c = datetime.fromisoformat(completed)
            total += (c - s).total_seconds()
            count += 1
        except (ValueError, TypeError):
            continue

    return total / count if count > 0 else 90.0


def claim_next_task() -> Optional[dict]:
    with get_db() as session:
        if _is_sqlite():
            task = (
                session.query(BackgroundTask)
                .filter(BackgroundTask.status == "pending")
                .order_by(BackgroundTask.created_at.asc())
                .first()
            )
            if task:
                task.status = "processing"
                task.started_at = datetime.now(timezone.utc).isoformat()
                task.attempts = (task.attempts or 0) + 1
                session.flush()
                return {
                    "id": task.id,
                    "user_id": task.user_id,
                    "task_type": task.task_type,
                    "channel": task.channel,
                    "payload": json.loads(task.payload) if isinstance(task.payload, str) else task.payload,
                    "attempts": task.attempts,
                }
        else:
            row = session.execute(text(
                "UPDATE background_tasks "
                "SET status = 'processing', started_at = NOW(), attempts = attempts + 1 "
                "WHERE id = ("
                "  SELECT id FROM background_tasks "
                "  WHERE status = 'pending' "
                "  ORDER BY created_at ASC "
                "  FOR UPDATE SKIP LOCKED "
                "  LIMIT 1"
                ") "
                "RETURNING id, user_id, task_type, channel, payload, attempts"
            )).fetchone()
            if row:
                return {
                    "id": str(row[0]),
                    "user_id": row[1],
                    "task_type": row[2],
                    "channel": row[3],
                    "payload": json.loads(row[4]) if isinstance(row[4], str) else row[4],
                    "attempts": row[5],
                }
    return None


def complete_task(task_id: str, result: dict):
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as session:
        task = session.get(BackgroundTask, task_id)
        if task:
            task.status = "done"
            task.result = json.dumps(result)
            task.completed_at = now_iso
            task.updated_at = now_iso


def fail_task(task_id: str, error: str):
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as session:
        task = session.get(BackgroundTask, task_id)
        if task:
            if (task.attempts or 0) < 3:
                task.status = "pending"
            else:
                task.status = "failed"
                task.result = json.dumps({"error": error})
            task.updated_at = now_iso


def is_task_cancelled(task_id: str) -> bool:
    with get_db() as session:
        task = session.get(BackgroundTask, task_id)
        if not task:
            return False
        return task.status == "cancelled"


def count_processing_tasks() -> int:
    with get_db() as session:
        return session.query(func.count(BackgroundTask.id)).filter(
            BackgroundTask.status == "processing",
        ).scalar()


def recover_stale_tasks():
    timeout = int(get_config("task_timeout_minutes", "5"))
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=timeout)).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_db() as session:
        session.execute(
            text(
                "UPDATE background_tasks "
                "SET status = CASE WHEN attempts < 3 THEN 'pending' ELSE 'failed' END, "
                "    result = CASE WHEN attempts >= 3 THEN :error_json ELSE result END, "
                "    updated_at = :now "
                "WHERE status = 'processing' AND started_at < :cutoff"
            ),
            {"now": now_iso, "cutoff": cutoff, "error_json": '{"error": "timeout"}'},
        )

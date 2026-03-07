import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from src.db.session import get_db
from src.db.models import Reminder


def init_db():
    pass


def create_reminder(
    user_id: str,
    task_instructions: str,
    trigger_type: str,
    trigger_config: dict,
    title: str = "",
    notification_channel: str = "whatsapp_text",
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    config_json = json.dumps(trigger_config)

    with get_db() as db:
        reminder = Reminder(
            user_id=user_id,
            title=title,
            task_instructions=task_instructions,
            trigger_type=trigger_type,
            trigger_config=config_json,
            notification_channel=notification_channel,
            status="active",
            created_at=now,
        )
        db.add(reminder)
        db.flush()
        return reminder.id


def get_reminder(reminder_id: int) -> Optional[Dict[str, Any]]:
    with get_db() as db:
        reminder = db.get(Reminder, reminder_id)
        if not reminder:
            return None
        return reminder.to_dict()


def update_apscheduler_job_id(reminder_id: int, job_id: str):
    updated_at = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        reminder = db.get(Reminder, reminder_id)
        if not reminder:
            return
        reminder.apscheduler_job_id = job_id
        reminder.updated_at = updated_at


def update_status(reminder_id: int, status: str):
    updated_at = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        reminder = db.get(Reminder, reminder_id)
        if not reminder:
            return
        reminder.status = status
        reminder.updated_at = updated_at


def cancel_reminder(reminder_id: int):
    update_status(reminder_id, "cancelled")


def mark_fired(reminder_id: int):
    update_status(reminder_id, "fired")


def list_user_reminders(user_id: str, status: str = "active", limit: int = 0, offset: int = 0) -> dict:
    fetch_limit = limit + 1 if limit > 0 else None

    with get_db() as db:
        q = (
            db.query(Reminder)
            .filter(Reminder.user_id == user_id, Reminder.status == status)
            .order_by(Reminder.created_at.desc())
        )
        if fetch_limit:
            q = q.limit(fetch_limit).offset(offset)
        rows = q.all()

    reminders = [r.to_dict() for r in rows]
    has_more = False
    if limit > 0 and len(reminders) > limit:
        has_more = True
        reminders = reminders[:limit]

    return {"reminders": reminders, "has_more": has_more}


def list_all_active_reminders() -> List[Dict[str, Any]]:
    with get_db() as db:
        rows = (
            db.query(Reminder)
            .filter(Reminder.status == "active")
            .order_by(Reminder.created_at.asc())
            .all()
        )
    return [r.to_dict() for r in rows]

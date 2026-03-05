from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
from src.auth.deps import get_current_user
from src.tools.task_manager import add_task, get_tasks, complete_task, reopen_task, delete_task
from src.tools.scheduler_tool import create_scheduler_tools
from src.models.reminders import list_user_reminders
from src.events import emit_event

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

# --- Tasks ---
@router.get("/tasks")
async def api_get_tasks(status: str = Query("pending"), current_user: dict = Depends(get_current_user)):
    user_id = current_user["phone_number"]
    tasks = get_tasks(user_id, status=status)
    return {"tasks": tasks}

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
async def api_get_reminders(status: str = Query("active"), current_user: dict = Depends(get_current_user)):
    user_id = current_user["phone_number"]
    reminders = list_user_reminders(user_id, status=status)
    return {"reminders": reminders}

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

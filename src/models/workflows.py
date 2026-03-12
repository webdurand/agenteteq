import json
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from src.db.session import get_db
from src.db.models import Workflow


def create_workflow(
    user_id: str,
    original_request: str,
    steps: List[Dict[str, Any]],
    title: str = "",
    notification_channel: str = "",
    status: str = "draft",
) -> str:
    now = datetime.now(timezone.utc).isoformat()
    workflow_id = str(uuid.uuid4())

    formatted_steps = []
    for i, step in enumerate(steps):
        formatted_steps.append({
            "index": i,
            "instructions": step.get("instructions", ""),
            "status": "pending",
            "output": None,
            "error": None,
            "started_at": None,
            "completed_at": None,
        })

    with get_db() as db:
        workflow = Workflow(
            id=workflow_id,
            user_id=user_id,
            title=title,
            original_request=original_request,
            steps=json.dumps(formatted_steps),
            status=status,
            current_step=0,
            notification_channel=notification_channel or None,
            created_at=now,
            updated_at=now,
        )
        db.add(workflow)
        db.flush()

    return workflow_id


def get_workflow(workflow_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as db:
        workflow = db.get(Workflow, workflow_id)
        if not workflow:
            return None
        return workflow.to_dict()


def update_workflow_status(workflow_id: str, status: str):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        workflow = db.get(Workflow, workflow_id)
        if not workflow:
            return
        workflow.status = status
        workflow.updated_at = now


def update_workflow_step(workflow_id: str, step_index: int, updates: Dict[str, Any]):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        workflow = db.get(Workflow, workflow_id)
        if not workflow:
            return
        steps = json.loads(workflow.steps) if isinstance(workflow.steps, str) else workflow.steps
        if step_index < 0 or step_index >= len(steps):
            return
        steps[step_index].update(updates)
        workflow.steps = json.dumps(steps)
        workflow.current_step = step_index
        workflow.updated_at = now


def reset_workflow_steps(workflow_id: str):
    """Reseta todos os steps pra pending (usado em execucoes recorrentes)."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        workflow = db.get(Workflow, workflow_id)
        if not workflow:
            return
        steps = json.loads(workflow.steps) if isinstance(workflow.steps, str) else workflow.steps
        for step in steps:
            step["status"] = "pending"
            step["output"] = None
            step["error"] = None
            step["started_at"] = None
            step["completed_at"] = None
        workflow.steps = json.dumps(steps)
        workflow.current_step = 0
        workflow.status = "running"
        workflow.last_run_at = now
        workflow.updated_at = now


def mark_workflow_done(workflow_id: str):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        workflow = db.get(Workflow, workflow_id)
        if not workflow:
            return
        workflow.status = "done"
        workflow.last_run_at = now
        workflow.updated_at = now


def mark_workflow_failed(workflow_id: str):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        workflow = db.get(Workflow, workflow_id)
        if not workflow:
            return
        workflow.status = "failed"
        workflow.updated_at = now


def list_user_workflows(user_id: str, status: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    with get_db() as db:
        q = db.query(Workflow).filter(Workflow.user_id == user_id)
        if status:
            q = q.filter(Workflow.status == status)
        q = q.order_by(Workflow.created_at.desc()).limit(limit)
        rows = q.all()
    return [r.to_dict() for r in rows]


def cancel_workflow(workflow_id: str, user_id: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        workflow = db.get(Workflow, workflow_id)
        if not workflow or workflow.user_id != user_id:
            return False
        workflow.status = "cancelled"
        workflow.updated_at = now
    return True

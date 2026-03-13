"""CRUD functions for content plans (content calendar)."""

import json
from datetime import datetime, timezone
from typing import Optional

from src.db.session import get_db
from src.db.models import ContentPlan


def create_content_plan(
    user_id: str,
    title: str,
    content_type: str = "post",
    platforms: list[str] | None = None,
    scheduled_at: str = "",
    description: str = "",
    notes: str = "",
) -> dict:
    """Create a new content plan entry."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        plan = ContentPlan(
            user_id=user_id,
            title=title,
            description=description,
            content_type=content_type,
            platforms=json.dumps(platforms or []),
            scheduled_at=scheduled_at or None,
            status="idea",
            notes=notes,
            created_at=now,
        )
        db.add(plan)
        db.flush()
        return plan.to_dict()


def list_content_plans(
    user_id: str,
    status: str = "",
    from_date: str = "",
    to_date: str = "",
    limit: int = 30,
    offset: int = 0,
) -> tuple[list[dict], bool]:
    """List content plans with optional filters. Returns (plans, has_more)."""
    with get_db() as db:
        q = db.query(ContentPlan).filter(ContentPlan.user_id == user_id)
        if status:
            q = q.filter(ContentPlan.status == status)
        if from_date:
            q = q.filter(ContentPlan.scheduled_at >= from_date)
        if to_date:
            q = q.filter(ContentPlan.scheduled_at <= to_date)
        q = q.order_by(ContentPlan.scheduled_at.asc().nullslast(), ContentPlan.created_at.desc())
        rows = q.offset(offset).limit(limit + 1).all()
        has_more = len(rows) > limit
        return [r.to_dict() for r in rows[:limit]], has_more


def get_content_plan(plan_id: int) -> Optional[dict]:
    """Get a single content plan by ID."""
    with get_db() as db:
        plan = db.get(ContentPlan, plan_id)
        if not plan:
            return None
        return plan.to_dict()


def update_content_plan(plan_id: int, user_id: str, **kwargs) -> Optional[dict]:
    """Update a content plan. Returns updated plan or None if not found."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        plan = db.get(ContentPlan, plan_id)
        if not plan or plan.user_id != user_id:
            return None

        for key, value in kwargs.items():
            if key == "platforms" and isinstance(value, list):
                setattr(plan, key, json.dumps(value))
            elif hasattr(plan, key) and key not in ("id", "user_id", "created_at"):
                setattr(plan, key, value)

        plan.updated_at = now
        db.flush()
        return plan.to_dict()


def delete_content_plan(plan_id: int, user_id: str) -> bool:
    """Delete a content plan. Returns True if deleted."""
    with get_db() as db:
        plan = db.get(ContentPlan, plan_id)
        if not plan or plan.user_id != user_id:
            return False
        db.delete(plan)
    return True

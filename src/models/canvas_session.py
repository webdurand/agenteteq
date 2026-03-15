"""CRUD operations for CanvasSession."""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from src.db.session import get_db
from src.db.models import CanvasSession


def create_canvas_session(
    user_id: str,
    canvas_doc: dict,
    title: str = "",
    fmt: str = "1080x1080",
) -> str:
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        session = CanvasSession(
            id=session_id,
            user_id=user_id,
            title=title,
            canvas_json=json.dumps(canvas_doc),
            status="active",
            format=fmt,
            created_at=now,
            updated_at=now,
        )
        db.add(session)

    return session_id


def get_canvas_session(session_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as db:
        session = db.get(CanvasSession, session_id)
        if not session:
            return None
        return session.to_dict()


def get_active_canvas(user_id: str) -> Optional[Dict[str, Any]]:
    """Return the most recent active canvas for a user."""
    with get_db() as db:
        session = (
            db.query(CanvasSession)
            .filter(
                CanvasSession.user_id == user_id,
                CanvasSession.status == "active",
            )
            .order_by(CanvasSession.updated_at.desc())
            .first()
        )
        if not session:
            return None
        return session.to_dict()


def update_canvas_json(session_id: str, canvas_doc: dict, thumbnail_url: str = None):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        session = db.get(CanvasSession, session_id)
        if not session:
            return
        session.canvas_json = json.dumps(canvas_doc)
        session.updated_at = now
        if thumbnail_url:
            session.thumbnail_url = thumbnail_url


def update_canvas_title(session_id: str, title: str):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        session = db.get(CanvasSession, session_id)
        if not session:
            return
        session.title = title
        session.updated_at = now


def archive_canvas(session_id: str, user_id: str) -> bool:
    with get_db() as db:
        session = db.get(CanvasSession, session_id)
        if not session or session.user_id != user_id:
            return False
        session.status = "archived"
        session.updated_at = datetime.now(timezone.utc).isoformat()
    return True


def list_user_canvases(user_id: str, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
    with get_db() as db:
        rows = (
            db.query(CanvasSession)
            .filter(CanvasSession.user_id == user_id)
            .order_by(CanvasSession.updated_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )
    return [r.to_dict() for r in rows]

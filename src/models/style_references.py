"""CRUD operations for style references."""

import json
from datetime import datetime, timezone
from typing import Optional

from src.db.session import get_db
from src.db.models import StyleReference


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_style_reference(
    user_id: str,
    image_url: str,
    title: str = "",
    source_url: str = "",
    brand_profile_id: int | None = None,
    extracted_colors: dict | None = None,
    style_description: str = "",
    tags: str = "",
) -> dict:
    with get_db() as db:
        ref = StyleReference(
            user_id=user_id,
            image_url=image_url,
            title=title or "",
            source_url=source_url or "",
            brand_profile_id=brand_profile_id,
            extracted_colors=json.dumps(extracted_colors or {}),
            style_description=style_description or "",
            tags=tags or "",
            created_at=_now_iso(),
        )
        db.add(ref)
        db.flush()
        return ref.to_dict()


def list_style_references(
    user_id: str,
    brand_profile_id: int | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    with get_db() as db:
        q = db.query(StyleReference).filter(StyleReference.user_id == user_id)
        if brand_profile_id is not None:
            q = q.filter(StyleReference.brand_profile_id == brand_profile_id)
        rows = (
            q.order_by(StyleReference.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
    return [r.to_dict() for r in rows]


def get_style_reference(ref_id: int) -> Optional[dict]:
    with get_db() as db:
        ref = db.get(StyleReference, ref_id)
        if not ref:
            return None
        return ref.to_dict()


def delete_style_reference(ref_id: int, user_id: str) -> bool:
    with get_db() as db:
        ref = db.get(StyleReference, ref_id)
        if not ref or ref.user_id != user_id:
            return False
        db.delete(ref)
        db.flush()
    return True


def count_style_references(user_id: str) -> int:
    with get_db() as db:
        return db.query(StyleReference).filter(
            StyleReference.user_id == user_id,
        ).count()

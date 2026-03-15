import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from src.db.session import get_db
from src.db.models import Carousel


def init_db():
    pass


def create_carousel(
    user_id: str,
    title: str,
    slides: list,
    reference_images: list = [],
) -> str:
    carousel_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        carousel = Carousel(
            id=carousel_id,
            user_id=user_id,
            title=title,
            status="generating",
            slides=json.dumps(slides),
            reference_images=json.dumps(reference_images),
            created_at=now,
        )
        db.add(carousel)

    return carousel_id


def get_carousel(carousel_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as db:
        carousel = db.get(Carousel, carousel_id)
        if not carousel:
            return None
        return carousel.to_dict()


def update_carousel_status(carousel_id: str, status: str, slides: Optional[list] = None):
    updated_at = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        carousel = db.get(Carousel, carousel_id)
        if not carousel:
            return
        carousel.status = status
        carousel.updated_at = updated_at
        if slides is not None:
            carousel.slides = json.dumps(slides)


def delete_carousel(carousel_id: str, user_id: str) -> bool:
    with get_db() as db:
        carousel = db.get(Carousel, carousel_id)
        if not carousel or carousel.user_id != user_id:
            return False
        db.delete(carousel)
    return True


def list_user_carousels(user_id: str, limit: int = 0, offset: int = 0) -> dict:
    fetch_limit = limit + 1 if limit > 0 else None

    with get_db() as db:
        q = db.query(Carousel).filter(Carousel.user_id == user_id).order_by(Carousel.created_at.desc())
        if fetch_limit:
            q = q.limit(fetch_limit).offset(offset)
        rows = q.all()

    carousels = [r.to_dict() for r in rows]
    has_more = False
    if limit > 0 and len(carousels) > limit:
        has_more = True
        carousels = carousels[:limit]

    return {"carousels": carousels, "has_more": has_more}


def create_pdf_entry(user_id: str, title: str, file_url: str) -> str:
    """Create a gallery entry for a PDF file (already uploaded)."""
    carousel_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        carousel = Carousel(
            id=carousel_id,
            user_id=user_id,
            title=title,
            type="pdf",
            status="done",
            file_url=file_url,
            slides="[]",
            reference_images="[]",
            created_at=now,
        )
        db.add(carousel)

    return carousel_id

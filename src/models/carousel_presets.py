"""CRUD operations for carousel presets (style templates)."""

import json
from datetime import datetime, timezone
from typing import Optional

from src.db.session import get_db
from src.db.models import CarouselPreset


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_preset(
    user_id: str,
    name: str,
    style_anchor: str = "",
    color_palette: dict | None = None,
    default_format: str = "1350x1080",
    default_slide_count: int = 5,
    sequential_slides: bool = True,
    brand_profile_id: int | None = None,
) -> dict:
    """Create or update a carousel preset by name (upsert)."""
    now = _now_iso()
    palette_json = json.dumps(color_palette or {})

    with get_db() as db:
        existing = (
            db.query(CarouselPreset)
            .filter(
                CarouselPreset.user_id == user_id,
                CarouselPreset.name.ilike(name),
            )
            .first()
        )

        if existing:
            if style_anchor:
                existing.style_anchor = style_anchor
            if color_palette:
                existing.color_palette_json = palette_json
            if default_format:
                existing.default_format = default_format
            if default_slide_count:
                existing.default_slide_count = default_slide_count
            existing.sequential_slides = sequential_slides
            if brand_profile_id is not None:
                existing.brand_profile_id = brand_profile_id
            existing.updated_at = now
            db.flush()
            return existing.to_dict()

        preset = CarouselPreset(
            user_id=user_id,
            name=name,
            brand_profile_id=brand_profile_id,
            style_anchor=style_anchor or None,
            color_palette_json=palette_json,
            default_format=default_format,
            default_slide_count=default_slide_count,
            sequential_slides=sequential_slides,
            created_at=now,
            updated_at=now,
        )
        db.add(preset)
        db.flush()
        return preset.to_dict()


def list_presets(user_id: str) -> list[dict]:
    """List all carousel presets for a user."""
    with get_db() as db:
        rows = (
            db.query(CarouselPreset)
            .filter(CarouselPreset.user_id == user_id)
            .order_by(CarouselPreset.created_at.desc())
            .all()
        )
    return [r.to_dict() for r in rows]


def get_preset_by_name(user_id: str, name: str) -> Optional[dict]:
    """Get a preset by name (case-insensitive)."""
    with get_db() as db:
        preset = (
            db.query(CarouselPreset)
            .filter(
                CarouselPreset.user_id == user_id,
                CarouselPreset.name.ilike(name),
            )
            .first()
        )
        if not preset:
            return None
        return preset.to_dict()


def delete_preset(preset_id: int, user_id: str) -> bool:
    """Delete a preset by ID."""
    with get_db() as db:
        preset = db.get(CarouselPreset, preset_id)
        if not preset or preset.user_id != user_id:
            return False
        db.delete(preset)
    return True

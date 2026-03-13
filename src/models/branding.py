"""CRUD operations for brand profiles."""

from datetime import datetime, timezone
from typing import Optional

from src.db.session import get_db
from src.db.models import BrandProfile


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_brand_profile(
    user_id: str,
    name: str,
    is_default: bool = False,
    primary_color: str = "#1A1A2E",
    secondary_color: str = "#16213E",
    accent_color: str = "#E94560",
    bg_color: str = "#0F0F0F",
    text_primary_color: str = "#FFFFFF",
    text_secondary_color: str = "#D0D0D0",
    font_heading: str = "Inter Bold",
    font_body: str = "Inter",
    logo_url: str = "",
    style_description: str = "",
    tone_of_voice: str = "",
    target_audience: str = "",
) -> dict:
    now = _now_iso()
    with get_db() as db:
        # If this is set as default, unset any existing default
        if is_default:
            db.query(BrandProfile).filter(
                BrandProfile.user_id == user_id,
                BrandProfile.is_default == True,  # noqa: E712
            ).update({"is_default": False, "updated_at": now})

        # If user has no profiles yet, make this the default
        existing_count = db.query(BrandProfile).filter(
            BrandProfile.user_id == user_id,
        ).count()
        if existing_count == 0:
            is_default = True

        profile = BrandProfile(
            user_id=user_id,
            name=name,
            is_default=is_default,
            primary_color=primary_color,
            secondary_color=secondary_color,
            accent_color=accent_color,
            bg_color=bg_color,
            text_primary_color=text_primary_color,
            text_secondary_color=text_secondary_color,
            font_heading=font_heading,
            font_body=font_body,
            logo_url=logo_url or None,
            style_description=style_description or None,
            tone_of_voice=tone_of_voice or None,
            target_audience=target_audience or None,
            created_at=now,
            updated_at=now,
        )
        db.add(profile)
        db.flush()
        return profile.to_dict()


def update_brand_profile(profile_id: int, user_id: str, **kwargs) -> Optional[dict]:
    now = _now_iso()
    with get_db() as db:
        profile = db.get(BrandProfile, profile_id)
        if not profile or profile.user_id != user_id:
            return None

        # Handle default flag
        if kwargs.get("is_default"):
            db.query(BrandProfile).filter(
                BrandProfile.user_id == user_id,
                BrandProfile.is_default == True,  # noqa: E712
                BrandProfile.id != profile_id,
            ).update({"is_default": False, "updated_at": now})

        allowed_fields = {
            "name", "is_default", "primary_color", "secondary_color",
            "accent_color", "bg_color", "text_primary_color", "text_secondary_color",
            "font_heading", "font_body", "logo_url", "style_description",
            "tone_of_voice", "target_audience",
        }
        for key, value in kwargs.items():
            if key in allowed_fields and value is not None:
                setattr(profile, key, value)

        profile.updated_at = now
        db.flush()
        return profile.to_dict()


def delete_brand_profile(profile_id: int, user_id: str) -> bool:
    with get_db() as db:
        profile = db.get(BrandProfile, profile_id)
        if not profile or profile.user_id != user_id:
            return False

        was_default = profile.is_default
        db.delete(profile)
        db.flush()

        # If deleted profile was default, promote the next one
        if was_default:
            next_profile = (
                db.query(BrandProfile)
                .filter(BrandProfile.user_id == user_id)
                .order_by(BrandProfile.created_at.asc())
                .first()
            )
            if next_profile:
                next_profile.is_default = True
                next_profile.updated_at = _now_iso()

    return True


def list_brand_profiles(user_id: str) -> list[dict]:
    with get_db() as db:
        rows = (
            db.query(BrandProfile)
            .filter(BrandProfile.user_id == user_id)
            .order_by(BrandProfile.is_default.desc(), BrandProfile.created_at.asc())
            .all()
        )
    return [r.to_dict() for r in rows]


def get_default_brand_profile(user_id: str) -> Optional[dict]:
    with get_db() as db:
        profile = (
            db.query(BrandProfile)
            .filter(
                BrandProfile.user_id == user_id,
                BrandProfile.is_default == True,  # noqa: E712
            )
            .first()
        )
        if not profile:
            # Fallback: return any profile
            profile = (
                db.query(BrandProfile)
                .filter(BrandProfile.user_id == user_id)
                .first()
            )
        if not profile:
            return None
        return profile.to_dict()


def get_brand_profile_by_name(user_id: str, name: str) -> Optional[dict]:
    with get_db() as db:
        profile = (
            db.query(BrandProfile)
            .filter(
                BrandProfile.user_id == user_id,
                BrandProfile.name.ilike(name),
            )
            .first()
        )
        if not profile:
            return None
        return profile.to_dict()

"""CRUD + search operations for SharedAsset (shared icon/image library)."""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from sqlalchemy import or_

from src.db.session import get_db
from src.db.models import SharedAsset


def create_asset(
    name: str,
    url: str,
    tags: str = "",
    category: str = "icon",
    asset_type: str = "svg",
    source: str = "seed",
    thumbnail_url: str = "",
    metadata: Optional[dict] = None,
    created_by: Optional[str] = None,
) -> str:
    """Create a shared asset and return its ID."""
    asset_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as db:
        asset = SharedAsset(
            id=asset_id,
            name=name,
            tags=tags,
            category=category,
            asset_type=asset_type,
            source=source,
            url=url,
            thumbnail_url=thumbnail_url,
            metadata_json=json.dumps(metadata or {}),
            created_by=created_by,
            usage_count=0,
            created_at=now,
        )
        db.add(asset)

    return asset_id


def search_assets(
    query: str,
    category: str = "",
    asset_type: str = "",
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Search assets by name or tags (case-insensitive LIKE)."""
    with get_db() as db:
        q = db.query(SharedAsset)
        if category:
            q = q.filter(SharedAsset.category == category)
        if asset_type:
            q = q.filter(SharedAsset.asset_type == asset_type)

        pattern = f"%{query.lower()}%"
        q = q.filter(
            or_(
                SharedAsset.name.ilike(pattern),
                SharedAsset.tags.ilike(pattern),
            )
        )
        q = q.order_by(SharedAsset.usage_count.desc())
        rows = q.limit(limit).all()
    return [r.to_dict() for r in rows]


def get_asset(asset_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as db:
        asset = db.get(SharedAsset, asset_id)
        if not asset:
            return None
        return asset.to_dict()


def get_asset_by_name(name: str, category: str = "icon") -> Optional[Dict[str, Any]]:
    """Find asset by exact name within a category."""
    with get_db() as db:
        asset = (
            db.query(SharedAsset)
            .filter(SharedAsset.name == name, SharedAsset.category == category)
            .first()
        )
        if not asset:
            return None
        return asset.to_dict()


def increment_usage(asset_id: str):
    """Bump usage_count by 1."""
    with get_db() as db:
        asset = db.get(SharedAsset, asset_id)
        if asset:
            asset.usage_count = (asset.usage_count or 0) + 1


def list_assets(
    category: str = "",
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """List assets, optionally filtered by category."""
    with get_db() as db:
        q = db.query(SharedAsset)
        if category:
            q = q.filter(SharedAsset.category == category)
        rows = (
            q.order_by(SharedAsset.usage_count.desc(), SharedAsset.name.asc())
            .limit(limit)
            .offset(offset)
            .all()
        )
    return [r.to_dict() for r in rows]

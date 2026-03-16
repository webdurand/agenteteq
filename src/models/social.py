import json
from datetime import datetime, timezone
from typing import Optional

from src.db.session import get_db
from src.db.models import TrackedAccount, SocialContent, User


def track_account(
    user_id: str,
    platform: str,
    username: str,
    display_name: str = "",
    profile_url: str = "",
    profile_pic_url: str = "",
    bio: str = "",
    followers_count: int = 0,
    posts_count: int = 0,
    metadata: dict | None = None,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    username = username.lstrip("@").lower()

    with get_db() as db:
        existing = (
            db.query(TrackedAccount)
            .filter(
                TrackedAccount.user_id == user_id,
                TrackedAccount.platform == platform,
                TrackedAccount.username == username,
            )
            .first()
        )
        if existing:
            if existing.status == "active":
                return existing.id
            existing.status = "active"
            existing.updated_at = now
            existing.display_name = display_name or existing.display_name
            existing.bio = bio or existing.bio
            existing.followers_count = followers_count or existing.followers_count
            existing.posts_count = posts_count or existing.posts_count
            existing.profile_pic_url = profile_pic_url or existing.profile_pic_url
            existing.profile_url = profile_url or existing.profile_url
            db.flush()
            return existing.id

        account = TrackedAccount(
            user_id=user_id,
            platform=platform,
            username=username,
            display_name=display_name,
            profile_url=profile_url,
            profile_pic_url=profile_pic_url,
            bio=bio,
            followers_count=followers_count,
            posts_count=posts_count,
            metadata_json=json.dumps(metadata or {}),
            status="active",
            created_at=now,
        )
        db.add(account)
        db.flush()
        return account.id


def untrack_account(account_id: int, user_id: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        account = db.get(TrackedAccount, account_id)
        if not account or account.user_id != user_id:
            return False
        account.status = "inactive"
        account.updated_at = now
    return True


def untrack_account_by_username(user_id: str, platform: str, username: str) -> bool:
    username = username.lstrip("@").lower()
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        account = (
            db.query(TrackedAccount)
            .filter(
                TrackedAccount.user_id == user_id,
                TrackedAccount.platform == platform,
                TrackedAccount.username == username,
                TrackedAccount.status == "active",
            )
            .first()
        )
        if not account:
            return False
        account.status = "inactive"
        account.updated_at = now
    return True


def list_tracked_accounts(user_id: str, platform: str | None = None) -> list[dict]:
    with get_db() as db:
        q = db.query(TrackedAccount).filter(
            TrackedAccount.user_id == user_id,
            TrackedAccount.status == "active",
        )
        if platform:
            q = q.filter(TrackedAccount.platform == platform)
        rows = q.order_by(TrackedAccount.created_at.desc()).all()
    return [r.to_dict() for r in rows]


def get_tracked_account(account_id: int) -> Optional[dict]:
    with get_db() as db:
        account = db.get(TrackedAccount, account_id)
        if not account:
            return None
        return account.to_dict()


def get_tracked_account_by_username(user_id: str, platform: str, username: str) -> Optional[dict]:
    username = username.lstrip("@").lower()
    with get_db() as db:
        account = (
            db.query(TrackedAccount)
            .filter(
                TrackedAccount.user_id == user_id,
                TrackedAccount.platform == platform,
                TrackedAccount.username == username,
                TrackedAccount.status == "active",
            )
            .first()
        )
        if not account:
            return None
        return account.to_dict()


def update_account_metadata(
    account_id: int,
    display_name: str = "",
    bio: str = "",
    followers_count: int = 0,
    posts_count: int = 0,
    profile_pic_url: str = "",
):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as db:
        account = db.get(TrackedAccount, account_id)
        if not account:
            return
        if display_name:
            account.display_name = display_name
        if bio:
            account.bio = bio
        if followers_count:
            account.followers_count = followers_count
        if posts_count:
            account.posts_count = posts_count
        if profile_pic_url:
            account.profile_pic_url = profile_pic_url
        account.last_fetched_at = now
        account.updated_at = now


def save_content_batch(
    tracked_account_id: int,
    user_id: str,
    platform: str,
    posts: list[dict],
) -> list[dict]:
    """Upsert posts by platform_post_id. Returns list of NEW post dicts inserted."""
    now = datetime.now(timezone.utc).isoformat()
    new_posts: list[dict] = []

    with get_db() as db:
        for post in posts:
            platform_post_id = post.get("platform_post_id", "")
            if not platform_post_id:
                continue

            existing = (
                db.query(SocialContent)
                .filter(
                    SocialContent.platform == platform,
                    SocialContent.platform_post_id == platform_post_id,
                )
                .first()
            )

            if existing:
                existing.likes_count = post.get("likes_count", existing.likes_count)
                existing.comments_count = post.get("comments_count", existing.comments_count)
                existing.views_count = post.get("views_count", existing.views_count)
                existing.fetched_at = now
            else:
                content = SocialContent(
                    tracked_account_id=tracked_account_id,
                    user_id=user_id,
                    platform=platform,
                    platform_post_id=platform_post_id,
                    content_type=post.get("content_type", ""),
                    caption=post.get("caption", ""),
                    hashtags_json=json.dumps(post.get("hashtags", [])),
                    media_urls_json=json.dumps(post.get("media_urls", [])),
                    thumbnail_url=post.get("thumbnail_url", ""),
                    likes_count=post.get("likes_count", 0),
                    comments_count=post.get("comments_count", 0),
                    views_count=post.get("views_count", 0),
                    posted_at=post.get("posted_at", ""),
                    fetched_at=now,
                )
                db.add(content)
                new_posts.append(post)

    return new_posts


def get_top_content(
    tracked_account_id: int,
    sort_by: str = "likes_count",
    limit: int = 10,
) -> list[dict]:
    sort_col = getattr(SocialContent, sort_by, SocialContent.likes_count)
    with get_db() as db:
        rows = (
            db.query(SocialContent)
            .filter(SocialContent.tracked_account_id == tracked_account_id)
            .order_by(sort_col.desc())
            .limit(limit)
            .all()
        )
    return [r.to_dict() for r in rows]


def get_recent_content(tracked_account_id: int, limit: int = 20) -> list[dict]:
    with get_db() as db:
        rows = (
            db.query(SocialContent)
            .filter(SocialContent.tracked_account_id == tracked_account_id)
            .order_by(SocialContent.posted_at.desc())
            .limit(limit)
            .all()
        )
    return [r.to_dict() for r in rows]


def list_all_active_tracked_accounts() -> list[dict]:
    """For the scheduler: returns all active tracked accounts across all users."""
    with get_db() as db:
        rows = (
            db.query(TrackedAccount)
            .filter(TrackedAccount.status == "active")
            .all()
        )
    return [r.to_dict() for r in rows]


# ──────────────── Trend alerts (user-level) ────────────────


def get_trend_alerts_enabled(user_id: str) -> bool:
    """Check if user has trend alerts enabled."""
    with get_db() as db:
        user = db.query(User).filter(User.phone_number == user_id).first()
        if not user:
            return False
        return getattr(user, "trend_alerts_enabled", "false") == "true"


def set_trend_alerts_enabled(user_id: str, enabled: bool) -> bool:
    """Toggle trend alerts for a user. Returns True if updated."""
    now = datetime.now(timezone.utc)
    with get_db() as db:
        user = db.query(User).filter(User.phone_number == user_id).first()
        if not user:
            return False
        user.trend_alerts_enabled = "true" if enabled else "false"
    return True


def get_last_trend_alert_at(user_id: str) -> Optional[datetime]:
    """Get timestamp of last trend alert sent to user."""
    with get_db() as db:
        user = db.query(User).filter(User.phone_number == user_id).first()
        if not user:
            return None
        return getattr(user, "last_trend_alert_at", None)


def update_last_trend_alert(user_id: str) -> None:
    """Update last trend alert timestamp to now."""
    now = datetime.now(timezone.utc)
    with get_db() as db:
        user = db.query(User).filter(User.phone_number == user_id).first()
        if user:
            user.last_trend_alert_at = now

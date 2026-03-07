import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Dict

from src.db.session import get_db, get_engine, _is_sqlite
from src.db.models import SystemConfig

_cache: Dict[str, tuple[str, float]] = {}
CACHE_TTL = 60

DEFAULT_CONFIG = {
    "max_concurrent_images": "3",
    "max_image_workers": "4",
    "max_global_processing": "3",
    "task_timeout_minutes": "5",
    "max_tasks_per_user:trial": "2",
    "max_tasks_per_user:paid": "5",
    "max_tasks_per_user_daily:trial": "5",
    "max_tasks_per_user_daily:paid": "50",
}


def init_system_config_table():
    pass


def _get_pg_engine():
    return get_engine()


def _get_sqlite_conn():
    db_path = os.path.join(os.getcwd(), "app.db")
    return sqlite3.connect(db_path)


def get_config(key: str, default: str = "") -> str:
    global _cache
    now = time.time()

    if key in _cache:
        val, timestamp = _cache[key]
        if now - timestamp < CACHE_TTL:
            return val

    with get_db() as session:
        row = session.get(SystemConfig, key)
        val = row.value if row else None

    if val is None:
        val = DEFAULT_CONFIG.get(key, default)

    _cache[key] = (val, now)
    return val


def get_config_for_plan(key: str, plan_type: str, default: str = "") -> str:
    plan_key = f"{key}:{plan_type}"
    val = get_config(plan_key, "")
    if val:
        return val
    return get_config(key, default)


def set_config(key: str, value: str):
    global _cache
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_db() as session:
        row = session.get(SystemConfig, key)
        if row:
            row.value = value
            row.updated_at = now_iso
        else:
            session.add(SystemConfig(key=key, value=value, updated_at=now_iso))

    _cache[key] = (value, time.time())


def get_all_configs() -> Dict[str, str]:
    with get_db() as session:
        rows = session.query(SystemConfig).all()
        configs = {r.key: r.value for r in rows}

    merged = DEFAULT_CONFIG.copy()
    merged.update(configs)
    return merged

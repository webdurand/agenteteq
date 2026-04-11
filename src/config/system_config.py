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
    "admin_bypass_limits": "true",
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


ALLOWED_CONFIG_KEYS = {
    "max_concurrent_images",
    "max_image_workers",
    "max_global_processing",
    "task_timeout_minutes",
    "admin_bypass_limits",
    "maintenance_mode",
    "default_tts_provider",
    "default_llm_provider",
    "video_max_duration_s",
    "carousel_default_format",
}


def set_config(key: str, value: str):
    # [SEC] Validate key against allowlist (CWE-15)
    if key not in ALLOWED_CONFIG_KEYS and not key.startswith(tuple(f"{k}:" for k in ALLOWED_CONFIG_KEYS)):
        raise ValueError(f"Config key '{key}' nao permitida. Keys validas: {sorted(ALLOWED_CONFIG_KEYS)}")

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

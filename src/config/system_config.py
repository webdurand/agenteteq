import os
import time
from typing import Optional, Dict

_cache: Dict[str, tuple[str, float]] = {}
CACHE_TTL = 60  # seconds

# Default values for the system configuration
DEFAULT_CONFIG = {
    "max_concurrent_images": "3",
    "max_image_workers": "4",
    "max_global_processing": "3",
    "task_timeout_minutes": "5",
    "max_tasks_per_user:trial": "2",
    "max_tasks_per_user:paid": "5",
    "max_tasks_per_user_daily:trial": "5",
    "max_tasks_per_user_daily:paid": "50"
}

def _get_db_url() -> Optional[str]:
    url = os.getenv("DATABASE_URL")
    if not url:
        return None
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://")
    return url

_pg_engine = None

def _get_pg_engine():
    global _pg_engine
    if _pg_engine is None:
        url = _get_db_url()
        if url:
            from sqlalchemy import create_engine
            _pg_engine = create_engine(
                url,
                pool_pre_ping=True,
                pool_recycle=300,
            )
    return _pg_engine

def _get_sqlite_conn():
    import sqlite3
    db_path = os.path.join(os.getcwd(), "system_config.db")
    return sqlite3.connect(db_path)

def init_system_config_table():
    engine = _get_pg_engine()
    if engine:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS system_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.commit()
    else:
        with _get_sqlite_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

def get_config(key: str, default: str = "") -> str:
    global _cache
    now = time.time()
    
    # Check cache
    if key in _cache:
        val, timestamp = _cache[key]
        if now - timestamp < CACHE_TTL:
            return val

    # Fetch from DB
    engine = _get_pg_engine()
    val = None
    if engine:
        from sqlalchemy import text
        with engine.connect() as conn:
            result = conn.execute(text("SELECT value FROM system_config WHERE key = :k"), {"k": key}).fetchone()
            if result:
                val = result[0]
    else:
        with _get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_config WHERE key = ?", (key,))
            result = cursor.fetchone()
            if result:
                val = result[0]

    # If not found, use default or provided default
    if val is None:
        val = DEFAULT_CONFIG.get(key, default)

    _cache[key] = (val, now)
    return val

def get_config_for_plan(key: str, plan_type: str, default: str = "") -> str:
    """
    Tries to get a plan-specific config like `max_tasks_per_user_daily:trial`
    If it doesn't exist, falls back to the global key `max_tasks_per_user_daily`.
    """
    plan_key = f"{key}:{plan_type}"
    # Try fetching plan specific
    val = get_config(plan_key, "")
    if val:
        return val
    # Fallback to global
    return get_config(key, default)

def set_config(key: str, value: str):
    global _cache
    engine = _get_pg_engine()
    if engine:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO system_config (key, value, updated_at) 
                VALUES (:k, :v, NOW())
                ON CONFLICT (key) DO UPDATE SET value = :v, updated_at = NOW()
            """), {"k": key, "v": value})
            conn.commit()
    else:
        with _get_sqlite_conn() as conn:
            conn.execute("""
                INSERT INTO system_config (key, value, updated_at) 
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
            """, (key, value))
            conn.commit()
    
    # Invalidate cache
    _cache[key] = (value, time.time())

def get_all_configs() -> Dict[str, str]:
    engine = _get_pg_engine()
    configs = {}
    if engine:
        from sqlalchemy import text
        with engine.connect() as conn:
            results = conn.execute(text("SELECT key, value FROM system_config")).fetchall()
            configs = {r[0]: r[1] for r in results}
    else:
        with _get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM system_config")
            results = cursor.fetchall()
            configs = {r[0]: r[1] for r in results}
            
    # Merge with defaults for keys not in DB
    merged = DEFAULT_CONFIG.copy()
    merged.update(configs)
    return merged

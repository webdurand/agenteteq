import json
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy import text
from src.config.system_config import _get_pg_engine, _get_sqlite_conn, get_config_for_plan, get_config
from src.memory.identity import get_user
from src.billing.service import is_subscription_active

def _get_plan_type(user_id: str) -> str:
    user = get_user(user_id)
    if not user:
        return "trial"
    if user.get("role") == "admin":
        return "admin"
    if is_subscription_active(user_id):
        return "paid"
    return "trial"

def enqueue_task(user_id: str, task_type: str, channel: str, payload: Dict[str, Any]) -> dict:
    plan_type = _get_plan_type(user_id)
    
    if plan_type != "admin":
        max_concurrent = int(get_config_for_plan("max_tasks_per_user", plan_type, "2"))
        max_daily = int(get_config_for_plan("max_tasks_per_user_daily", plan_type, "5"))
        
        # Check current pending/processing tasks
        engine = _get_pg_engine()
        if engine:
            with engine.connect() as conn:
                # Concurrent limit
                concurrent = conn.execute(text("""
                    SELECT COUNT(*) FROM background_tasks 
                    WHERE user_id = :uid AND status IN ('pending', 'processing')
                """), {"uid": user_id}).scalar()
                
                if concurrent >= max_concurrent:
                    return {"status": "limit_reached", "pending_count": concurrent}
                    
                # Daily limit
                daily = conn.execute(text("""
                    SELECT COUNT(*) FROM background_tasks 
                    WHERE user_id = :uid AND created_at >= NOW() - INTERVAL '24 hours'
                """), {"uid": user_id}).scalar()
                
                if daily >= max_daily:
                    return {"status": "daily_limit", "daily_limit": max_daily}
        else:
            with _get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM background_tasks WHERE user_id = ? AND status IN ('pending', 'processing')", (user_id,))
                concurrent = cursor.fetchone()[0]
                if concurrent >= max_concurrent:
                    return {"status": "limit_reached", "pending_count": concurrent}
                    
                cursor.execute("SELECT COUNT(*) FROM background_tasks WHERE user_id = ? AND created_at >= datetime('now', '-24 hours')", (user_id,))
                daily = cursor.fetchone()[0]
                if daily >= max_daily:
                    return {"status": "daily_limit", "daily_limit": max_daily}

    # Insert into queue
    engine = _get_pg_engine()
    task_id = None
    if engine:
        with engine.connect() as conn:
            result = conn.execute(text("""
                INSERT INTO background_tasks (user_id, task_type, channel, payload, status)
                VALUES (:uid, :ttype, :chan, :payload, 'pending')
                RETURNING id
            """), {"uid": user_id, "ttype": task_type, "chan": channel, "payload": json.dumps(payload)})
            task_id = str(result.scalar())
            
            # Get position
            position = conn.execute(text("""
                SELECT COUNT(*) FROM background_tasks WHERE status = 'pending' AND id != :id
            """), {"id": task_id}).scalar() + 1
            
            conn.commit()
    else:
        import uuid
        task_id = str(uuid.uuid4())
        with _get_sqlite_conn() as conn:
            conn.execute("""
                INSERT INTO background_tasks (id, user_id, task_type, channel, payload, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
            """, (task_id, user_id, task_type, channel, json.dumps(payload)))
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM background_tasks WHERE status = 'pending' AND id != ?", (task_id,))
            position = cursor.fetchone()[0] + 1
            conn.commit()

    # Estimate wait time
    avg_time = _get_avg_processing_time()
    max_global = int(get_config("max_global_processing", "3"))
    estimated_seconds = (position * avg_time) / max_global
    
    if estimated_seconds < 60:
        est_str = "menos de 1 minuto"
    elif estimated_seconds < 120:
        est_str = "1 a 2 minutos"
    else:
        est_str = f"~{int(estimated_seconds // 60)} minutos"

    return {
        "status": "queued",
        "task_id": task_id,
        "position": position,
        "estimated_wait": est_str
    }

def _get_avg_processing_time() -> float:
    """Returns avg processing time in seconds, defaults to 90s"""
    engine = _get_pg_engine()
    if engine:
        with engine.connect() as conn:
            avg = conn.execute(text("""
                SELECT AVG(EXTRACT(EPOCH FROM (completed_at - started_at))) 
                FROM (
                    SELECT started_at, completed_at FROM background_tasks 
                    WHERE status = 'done' AND completed_at IS NOT NULL AND started_at IS NOT NULL
                    ORDER BY completed_at DESC LIMIT 20
                ) t
            """)).scalar()
            return float(avg) if avg else 90.0
    else:
        with _get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT AVG((julianday(completed_at) - julianday(started_at)) * 86400.0) 
                FROM (
                    SELECT started_at, completed_at FROM background_tasks 
                    WHERE status = 'done' AND completed_at IS NOT NULL AND started_at IS NOT NULL
                    ORDER BY completed_at DESC LIMIT 20
                )
            """)
            avg = cursor.fetchone()[0]
            return float(avg) if avg else 90.0

def claim_next_task() -> Optional[dict]:
    engine = _get_pg_engine()
    if engine:
        with engine.connect() as conn:
            row = conn.execute(text("""
                UPDATE background_tasks 
                SET status = 'processing', started_at = NOW(), attempts = attempts + 1
                WHERE id = (
                    SELECT id FROM background_tasks 
                    WHERE status = 'pending' 
                    ORDER BY created_at ASC 
                    FOR UPDATE SKIP LOCKED 
                    LIMIT 1
                )
                RETURNING id, user_id, task_type, channel, payload, attempts
            """)).fetchone()
            conn.commit()
            if row:
                return {
                    "id": str(row[0]),
                    "user_id": row[1],
                    "task_type": row[2],
                    "channel": row[3],
                    "payload": json.loads(row[4]) if isinstance(row[4], str) else row[4],
                    "attempts": row[5]
                }
    else:
        with _get_sqlite_conn() as conn:
            cursor = conn.cursor()
            # SQLite doesnt have SKIP LOCKED, so we just pick the oldest
            cursor.execute("SELECT id, user_id, task_type, channel, payload, attempts FROM background_tasks WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1")
            row = cursor.fetchone()
            if row:
                task_id = row[0]
                cursor.execute("UPDATE background_tasks SET status = 'processing', started_at = CURRENT_TIMESTAMP, attempts = attempts + 1 WHERE id = ?", (task_id,))
                conn.commit()
                return {
                    "id": task_id,
                    "user_id": row[1],
                    "task_type": row[2],
                    "channel": row[3],
                    "payload": json.loads(row[4]),
                    "attempts": row[5] + 1
                }
    return None

def complete_task(task_id: str, result: dict):
    engine = _get_pg_engine()
    if engine:
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE background_tasks 
                SET status = 'done', result = :res, completed_at = NOW(), updated_at = NOW()
                WHERE id = :id
            """), {"id": task_id, "res": json.dumps(result)})
            conn.commit()
    else:
        with _get_sqlite_conn() as conn:
            conn.execute("""
                UPDATE background_tasks 
                SET status = 'done', result = ?, completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (json.dumps(result), task_id))
            conn.commit()

def fail_task(task_id: str, error: str):
    engine = _get_pg_engine()
    if engine:
        with engine.connect() as conn:
            attempts = conn.execute(text("SELECT attempts FROM background_tasks WHERE id = :id"), {"id": task_id}).scalar()
            if attempts < 3:
                conn.execute(text("UPDATE background_tasks SET status = 'pending', updated_at = NOW() WHERE id = :id"), {"id": task_id})
            else:
                conn.execute(text("UPDATE background_tasks SET status = 'failed', result = :res, updated_at = NOW() WHERE id = :id"), {"id": task_id, "res": json.dumps({"error": error})})
            conn.commit()
    else:
        with _get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT attempts FROM background_tasks WHERE id = ?", (task_id,))
            attempts = cursor.fetchone()[0]
            if attempts < 3:
                conn.execute("UPDATE background_tasks SET status = 'pending', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (task_id,))
            else:
                conn.execute("UPDATE background_tasks SET status = 'failed', result = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (json.dumps({"error": error}), task_id))
            conn.commit()

def count_processing_tasks() -> int:
    engine = _get_pg_engine()
    if engine:
        with engine.connect() as conn:
            return conn.execute(text("SELECT COUNT(*) FROM background_tasks WHERE status = 'processing'")).scalar()
    else:
        with _get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM background_tasks WHERE status = 'processing'")
            return cursor.fetchone()[0]

def recover_stale_tasks():
    timeout = int(get_config("task_timeout_minutes", "5"))
    engine = _get_pg_engine()
    if engine:
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE background_tasks 
                SET status = CASE WHEN attempts < 3 THEN 'pending' ELSE 'failed' END,
                    result = CASE WHEN attempts >= 3 THEN '{"error": "timeout"}' ELSE result END,
                    updated_at = NOW()
                WHERE status = 'processing' AND started_at < NOW() - make_interval(mins => :t)
            """), {"t": timeout})
            conn.commit()
    else:
        with _get_sqlite_conn() as conn:
            conn.execute(f"""
                UPDATE background_tasks 
                SET status = CASE WHEN attempts < 3 THEN 'pending' ELSE 'failed' END,
                    result = CASE WHEN attempts >= 3 THEN '{{"error": "timeout"}}' ELSE result END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'processing' AND started_at < datetime('now', '-{timeout} minutes')
            """)
            conn.commit()

import os
import sqlite3
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

def _get_db_url() -> Optional[str]:
    url = os.getenv("DATABASE_URL")
    if not url:
        return None
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://")
    return url

def _use_postgres() -> bool:
    return bool(os.getenv("DATABASE_URL"))

_pg_engine = None

def _get_pg_engine():
    global _pg_engine
    if _pg_engine is None:
        from sqlalchemy import create_engine
        _pg_engine = create_engine(
            _get_db_url(),
            pool_pre_ping=True,
            pool_recycle=300,
        )
    return _pg_engine

def _get_sqlite_conn():
    return sqlite3.connect("scheduler.db")

def init_db():
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS reminders (
                        id SERIAL PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        title TEXT,
                        task_instructions TEXT NOT NULL,
                        trigger_type TEXT NOT NULL,
                        trigger_config TEXT NOT NULL,
                        notification_channel TEXT DEFAULT 'whatsapp_text',
                        status TEXT DEFAULT 'active',
                        apscheduler_job_id TEXT,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ
                    )
                """))
                conn.commit()
        else:
            conn = _get_sqlite_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    title TEXT,
                    task_instructions TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    trigger_config TEXT NOT NULL,
                    notification_channel TEXT DEFAULT 'whatsapp_text',
                    status TEXT DEFAULT 'active',
                    apscheduler_job_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT
                )
            """)
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[REMINDERS] Erro ao inicializar banco: {e}")

def _row_to_dict(row) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    return {
        "id": row[0],
        "user_id": row[1],
        "title": row[2],
        "task_instructions": row[3],
        "trigger_type": row[4],
        "trigger_config": json.loads(row[5]) if row[5] else {},
        "notification_channel": row[6],
        "status": row[7],
        "apscheduler_job_id": row[8],
        "created_at": row[9],
        "updated_at": row[10]
    }

def create_reminder(
    user_id: str,
    task_instructions: str,
    trigger_type: str,
    trigger_config: dict,
    title: str = "",
    notification_channel: str = "whatsapp_text"
) -> int:
    init_db()
    created_at = datetime.now(timezone.utc).isoformat()
    config_json = json.dumps(trigger_config)
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                result = conn.execute(
                    text("""
                        INSERT INTO reminders 
                        (user_id, title, task_instructions, trigger_type, trigger_config, notification_channel, status, created_at)
                        VALUES (:user_id, :title, :task_instructions, :trigger_type, :trigger_config, :notification_channel, 'active', :created_at)
                        RETURNING id
                    """),
                    {
                        "user_id": user_id,
                        "title": title,
                        "task_instructions": task_instructions,
                        "trigger_type": trigger_type,
                        "trigger_config": config_json,
                        "notification_channel": notification_channel,
                        "created_at": created_at
                    }
                )
                reminder_id = result.fetchone()[0]
                conn.commit()
                return reminder_id
        else:
            conn = _get_sqlite_conn()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO reminders 
                (user_id, title, task_instructions, trigger_type, trigger_config, notification_channel, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
                """,
                (user_id, title, task_instructions, trigger_type, config_json, notification_channel, created_at)
            )
            reminder_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return reminder_id
    except Exception as e:
        print(f"[REMINDERS] Erro ao criar reminder: {e}")
        raise e

def get_reminder(reminder_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT id, user_id, title, task_instructions, trigger_type, trigger_config, notification_channel, status, apscheduler_job_id, created_at, updated_at FROM reminders WHERE id = :id"),
                    {"id": reminder_id}
                ).fetchone()
                return _row_to_dict(row)
        else:
            conn = _get_sqlite_conn()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, user_id, title, task_instructions, trigger_type, trigger_config, notification_channel, status, apscheduler_job_id, created_at, updated_at FROM reminders WHERE id = ?",
                (reminder_id,)
            )
            row = cursor.fetchone()
            conn.close()
            return _row_to_dict(row)
    except Exception as e:
        print(f"[REMINDERS] Erro ao buscar reminder {reminder_id}: {e}")
        return None

def update_apscheduler_job_id(reminder_id: int, job_id: str):
    init_db()
    updated_at = datetime.now(timezone.utc).isoformat()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                conn.execute(
                    text("UPDATE reminders SET apscheduler_job_id = :job_id, updated_at = :updated_at WHERE id = :id"),
                    {"job_id": job_id, "updated_at": updated_at, "id": reminder_id}
                )
                conn.commit()
        else:
            conn = _get_sqlite_conn()
            conn.execute(
                "UPDATE reminders SET apscheduler_job_id = ?, updated_at = ? WHERE id = ?",
                (job_id, updated_at, reminder_id)
            )
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[REMINDERS] Erro ao atualizar job_id do reminder {reminder_id}: {e}")

def update_status(reminder_id: int, status: str):
    init_db()
    updated_at = datetime.now(timezone.utc).isoformat()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                conn.execute(
                    text("UPDATE reminders SET status = :status, updated_at = :updated_at WHERE id = :id"),
                    {"status": status, "updated_at": updated_at, "id": reminder_id}
                )
                conn.commit()
        else:
            conn = _get_sqlite_conn()
            conn.execute(
                "UPDATE reminders SET status = ?, updated_at = ? WHERE id = ?",
                (status, updated_at, reminder_id)
            )
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[REMINDERS] Erro ao atualizar status do reminder {reminder_id}: {e}")

def cancel_reminder(reminder_id: int):
    update_status(reminder_id, "cancelled")

def mark_fired(reminder_id: int):
    update_status(reminder_id, "fired")

def list_user_reminders(user_id: str, status: str = "active") -> List[Dict[str, Any]]:
    init_db()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT id, user_id, title, task_instructions, trigger_type, trigger_config, notification_channel, status, apscheduler_job_id, created_at, updated_at FROM reminders WHERE user_id = :user_id AND status = :status ORDER BY created_at ASC"),
                    {"user_id": user_id, "status": status}
                ).fetchall()
                return [_row_to_dict(row) for row in rows]
        else:
            conn = _get_sqlite_conn()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, user_id, title, task_instructions, trigger_type, trigger_config, notification_channel, status, apscheduler_job_id, created_at, updated_at FROM reminders WHERE user_id = ? AND status = ? ORDER BY created_at ASC",
                (user_id, status)
            )
            rows = cursor.fetchall()
            conn.close()
            return [_row_to_dict(row) for row in rows]
    except Exception as e:
        print(f"[REMINDERS] Erro ao listar reminders do usuario {user_id}: {e}")
        return []

def list_all_active_reminders() -> List[Dict[str, Any]]:
    init_db()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT id, user_id, title, task_instructions, trigger_type, trigger_config, notification_channel, status, apscheduler_job_id, created_at, updated_at FROM reminders WHERE status = 'active' ORDER BY created_at ASC")
                ).fetchall()
                return [_row_to_dict(row) for row in rows]
        else:
            conn = _get_sqlite_conn()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, user_id, title, task_instructions, trigger_type, trigger_config, notification_channel, status, apscheduler_job_id, created_at, updated_at FROM reminders WHERE status = 'active' ORDER BY created_at ASC"
            )
            rows = cursor.fetchall()
            conn.close()
            return [_row_to_dict(row) for row in rows]
    except Exception as e:
        print(f"[REMINDERS] Erro ao listar todos reminders ativos: {e}")
        return []

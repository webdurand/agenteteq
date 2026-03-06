import os
import sqlite3
import json
import uuid
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
    return sqlite3.connect("carousels.db")

def init_db():
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS carousels (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        title TEXT,
                        status TEXT DEFAULT 'generating',
                        slides JSONB DEFAULT '[]'::jsonb,
                        reference_images JSONB DEFAULT '[]'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ
                    )
                """))
                conn.commit()
        else:
            conn = _get_sqlite_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS carousels (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT,
                    status TEXT DEFAULT 'generating',
                    slides TEXT DEFAULT '[]',
                    reference_images TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT
                )
            """)
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[CAROUSELS] Erro ao inicializar banco: {e}")

def _row_to_dict(row) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    return {
        "id": row[0],
        "user_id": row[1],
        "title": row[2],
        "status": row[3],
        "slides": json.loads(row[4]) if row[4] and isinstance(row[4], str) else (row[4] if row[4] else []),
        "reference_images": json.loads(row[5]) if row[5] and isinstance(row[5], str) else (row[5] if row[5] else []),
        "created_at": row[6],
        "updated_at": row[7]
    }

def create_carousel(
    user_id: str,
    title: str,
    slides: list,
    reference_images: list = []
) -> str:
    init_db()
    carousel_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    slides_json = json.dumps(slides)
    refs_json = json.dumps(reference_images)
    
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO carousels 
                        (id, user_id, title, status, slides, reference_images, created_at)
                        VALUES (:id, :user_id, :title, 'generating', :slides, :refs, :created_at)
                    """),
                    {
                        "id": carousel_id,
                        "user_id": user_id,
                        "title": title,
                        "slides": slides_json,
                        "refs": refs_json,
                        "created_at": created_at
                    }
                )
                conn.commit()
                return carousel_id
        else:
            conn = _get_sqlite_conn()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO carousels 
                (id, user_id, title, status, slides, reference_images, created_at)
                VALUES (?, ?, ?, 'generating', ?, ?, ?)
                """,
                (carousel_id, user_id, title, slides_json, refs_json, created_at)
            )
            conn.commit()
            conn.close()
            return carousel_id
    except Exception as e:
        print(f"[CAROUSELS] Erro ao criar carousel: {e}")
        raise e

def get_carousel(carousel_id: str) -> Optional[Dict[str, Any]]:
    init_db()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT id, user_id, title, status, slides, reference_images, created_at, updated_at FROM carousels WHERE id = :id"),
                    {"id": carousel_id}
                ).fetchone()
                return _row_to_dict(row)
        else:
            conn = _get_sqlite_conn()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, user_id, title, status, slides, reference_images, created_at, updated_at FROM carousels WHERE id = ?",
                (carousel_id,)
            )
            row = cursor.fetchone()
            conn.close()
            return _row_to_dict(row)
    except Exception as e:
        print(f"[CAROUSELS] Erro ao buscar carousel {carousel_id}: {e}")
        return None

def update_carousel_status(carousel_id: str, status: str, slides: Optional[list] = None):
    init_db()
    updated_at = datetime.now(timezone.utc).isoformat()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                if slides is not None:
                    conn.execute(
                        text("UPDATE carousels SET status = :status, slides = :slides, updated_at = :updated_at WHERE id = :id"),
                        {"status": status, "slides": json.dumps(slides), "updated_at": updated_at, "id": carousel_id}
                    )
                else:
                    conn.execute(
                        text("UPDATE carousels SET status = :status, updated_at = :updated_at WHERE id = :id"),
                        {"status": status, "updated_at": updated_at, "id": carousel_id}
                    )
                conn.commit()
        else:
            conn = _get_sqlite_conn()
            if slides is not None:
                conn.execute(
                    "UPDATE carousels SET status = ?, slides = ?, updated_at = ? WHERE id = ?",
                    (status, json.dumps(slides), updated_at, carousel_id)
                )
            else:
                conn.execute(
                    "UPDATE carousels SET status = ?, updated_at = ? WHERE id = ?",
                    (status, updated_at, carousel_id)
                )
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[CAROUSELS] Erro ao atualizar status do carousel {carousel_id}: {e}")

def list_user_carousels(user_id: str) -> List[Dict[str, Any]]:
    init_db()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT id, user_id, title, status, slides, reference_images, created_at, updated_at FROM carousels WHERE user_id = :user_id ORDER BY created_at DESC"),
                    {"user_id": user_id}
                ).fetchall()
                return [_row_to_dict(row) for row in rows]
        else:
            conn = _get_sqlite_conn()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, user_id, title, status, slides, reference_images, created_at, updated_at FROM carousels WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,)
            )
            rows = cursor.fetchall()
            conn.close()
            return [_row_to_dict(row) for row in rows]
    except Exception as e:
        print(f"[CAROUSELS] Erro ao listar carousels do usuario {user_id}: {e}")
        return []

import os
import sqlite3
from typing import Optional

# ---------------------------------------------------------------------------
# Conexão: usa NeonDB (PostgreSQL) quando DATABASE_URL estiver configurado,
# caso contrário cai para SQLite local (útil para testes sem banco externo).
# Isso mantém a identidade do usuário persistente entre restarts do Koyeb.
# ---------------------------------------------------------------------------

def _get_db_url() -> Optional[str]:
    url = os.getenv("DATABASE_URL")
    if not url:
        return None
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://")
    return url


def _use_postgres() -> bool:
    return bool(os.getenv("DATABASE_URL"))


def _get_pg_engine():
    from sqlalchemy import create_engine
    url = _get_db_url()
    return create_engine(url)


def _init_pg():
    engine = _get_pg_engine()
    with engine.connect() as conn:
        conn.execute(__import__("sqlalchemy").text("""
            CREATE TABLE IF NOT EXISTS users (
                phone_number TEXT PRIMARY KEY,
                name TEXT,
                onboarding_step TEXT DEFAULT 'pending'
            )
        """))
        conn.commit()


def _get_sqlite_conn():
    conn = sqlite3.connect("users.db")
    return conn


def _init_sqlite():
    conn = _get_sqlite_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            phone_number TEXT PRIMARY KEY,
            name TEXT,
            onboarding_step TEXT DEFAULT 'pending'
        )
    """)
    conn.commit()
    conn.close()


def init_db():
    try:
        if _use_postgres():
            _init_pg()
        else:
            _init_sqlite()
    except Exception as e:
        print(f"[IDENTITY] Erro ao inicializar banco: {e}")


def get_user(phone_number: str) -> Optional[dict]:
    init_db()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT phone_number, name, onboarding_step FROM users WHERE phone_number = :p"),
                    {"p": phone_number}
                ).fetchone()
            if row:
                return {"phone_number": row[0], "name": row[1], "onboarding_step": row[2]}
        else:
            conn = _get_sqlite_conn()
            c = conn.cursor()
            c.execute("SELECT phone_number, name, onboarding_step FROM users WHERE phone_number = ?", (phone_number,))
            row = c.fetchone()
            conn.close()
            if row:
                return {"phone_number": row[0], "name": row[1], "onboarding_step": row[2]}
    except Exception as e:
        print(f"[IDENTITY] Erro ao buscar usuário {phone_number}: {e}")
    return None


def create_user(phone_number: str):
    init_db()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                conn.execute(
                    text("INSERT INTO users (phone_number, onboarding_step) VALUES (:p, 'asking_name') ON CONFLICT (phone_number) DO NOTHING"),
                    {"p": phone_number}
                )
                conn.commit()
        else:
            conn = _get_sqlite_conn()
            conn.execute("INSERT OR IGNORE INTO users (phone_number, onboarding_step) VALUES (?, 'asking_name')", (phone_number,))
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[IDENTITY] Erro ao criar usuário {phone_number}: {e}")


def update_user_name(phone_number: str, name: str):
    init_db()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                conn.execute(
                    text("UPDATE users SET name = :n, onboarding_step = 'completed' WHERE phone_number = :p"),
                    {"n": name, "p": phone_number}
                )
                conn.commit()
        else:
            conn = _get_sqlite_conn()
            conn.execute("UPDATE users SET name = ?, onboarding_step = 'completed' WHERE phone_number = ?", (name, phone_number))
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[IDENTITY] Erro ao atualizar nome de {phone_number}: {e}")

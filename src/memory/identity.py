import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Conexao: usa NeonDB (PostgreSQL) quando DATABASE_URL estiver configurado,
# caso contrario cai para SQLite local (util para testes sem banco externo).
# Isso mantem a identidade do usuario persistente entre restarts do Koyeb.
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
                onboarding_step TEXT DEFAULT 'pending',
                last_seen_at TIMESTAMPTZ
            )
        """))
        # Adiciona coluna se ja existir a tabela sem ela (migracao segura)
        conn.execute(__import__("sqlalchemy").text("""
            ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ
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
            onboarding_step TEXT DEFAULT 'pending',
            last_seen_at TEXT
        )
    """)
    # Adiciona coluna se ja existir a tabela sem ela (migracao segura)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN last_seen_at TEXT")
    except Exception:
        pass
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
                    text("SELECT phone_number, name, onboarding_step, last_seen_at FROM users WHERE phone_number = :p"),
                    {"p": phone_number}
                ).fetchone()
            if row:
                return {
                    "phone_number": row[0],
                    "name": row[1],
                    "onboarding_step": row[2],
                    "last_seen_at": row[3],
                }
        else:
            conn = _get_sqlite_conn()
            c = conn.cursor()
            c.execute("SELECT phone_number, name, onboarding_step, last_seen_at FROM users WHERE phone_number = ?", (phone_number,))
            row = c.fetchone()
            conn.close()
            if row:
                return {
                    "phone_number": row[0],
                    "name": row[1],
                    "onboarding_step": row[2],
                    "last_seen_at": row[3],
                }
    except Exception as e:
        print(f"[IDENTITY] Erro ao buscar usuario {phone_number}: {e}")
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
        print(f"[IDENTITY] Erro ao criar usuario {phone_number}: {e}")


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


def update_last_seen(phone_number: str):
    """Atualiza o timestamp da ultima mensagem recebida do usuario."""
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                conn.execute(
                    text("UPDATE users SET last_seen_at = :t WHERE phone_number = :p"),
                    {"t": now_iso, "p": phone_number}
                )
                conn.commit()
        else:
            conn = _get_sqlite_conn()
            conn.execute("UPDATE users SET last_seen_at = ? WHERE phone_number = ?", (now_iso, phone_number))
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[IDENTITY] Erro ao atualizar last_seen_at de {phone_number}: {e}")


def is_new_session(user: dict, threshold_hours: int = 4) -> bool:
    """
    Retorna True se o usuario ficou mais de threshold_hours sem enviar mensagens
    (ou se nao tem last_seen_at registrado), indicando que deve receber uma saudacao.
    """
    last_seen = user.get("last_seen_at")
    if not last_seen:
        return True
    try:
        if isinstance(last_seen, str):
            last_dt = datetime.fromisoformat(last_seen)
        else:
            last_dt = last_seen
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        elapsed_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
        return elapsed_hours >= threshold_hours
    except Exception as e:
        print(f"[IDENTITY] Erro ao calcular is_new_session: {e}")
        return False

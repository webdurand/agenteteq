import os
import sqlite3
from datetime import datetime, timezone, timedelta
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


def _init_pg():
    engine = _get_pg_engine()
    with engine.connect() as conn:
        conn.execute(__import__("sqlalchemy").text("""
            CREATE TABLE IF NOT EXISTS users (
                phone_number TEXT PRIMARY KEY,
                name TEXT,
                onboarding_step TEXT DEFAULT 'pending',
                last_seen_at TIMESTAMPTZ,
                username TEXT UNIQUE,
                email TEXT UNIQUE,
                birth_date TEXT,
                password_hash TEXT,
                whatsapp_verified BOOLEAN DEFAULT FALSE,
                google_id TEXT,
                auth_provider TEXT DEFAULT 'local',
                plan_type TEXT DEFAULT 'trial',
                trial_started_at TIMESTAMPTZ,
                trial_ends_at TIMESTAMPTZ,
                timezone TEXT DEFAULT 'America/Sao_Paulo',
                role TEXT DEFAULT 'user',
                stripe_customer_id TEXT
            )
        """))
        # Adiciona colunas se ja existir a tabela sem ela (migracao segura)
        for col in [
            "last_seen_at TIMESTAMPTZ",
            "username TEXT UNIQUE",
            "email TEXT UNIQUE",
            "birth_date TEXT",
            "password_hash TEXT",
            "whatsapp_verified BOOLEAN DEFAULT FALSE",
            "google_id TEXT",
            "auth_provider TEXT DEFAULT 'local'",
            "plan_type TEXT DEFAULT 'trial'",
            "trial_started_at TIMESTAMPTZ",
            "trial_ends_at TIMESTAMPTZ",
            "timezone TEXT DEFAULT 'America/Sao_Paulo'",
            "role TEXT DEFAULT 'user'",
            "stripe_customer_id TEXT"
        ]:
            try:
                conn.execute(__import__("sqlalchemy").text(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col}"))
            except Exception:
                pass
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
            last_seen_at TEXT,
            username TEXT,
            email TEXT,
            birth_date TEXT,
            password_hash TEXT,
            whatsapp_verified BOOLEAN DEFAULT 0,
            google_id TEXT,
            auth_provider TEXT DEFAULT 'local',
            plan_type TEXT DEFAULT 'trial',
            trial_started_at TEXT,
            trial_ends_at TEXT,
            timezone TEXT DEFAULT 'America/Sao_Paulo',
            role TEXT DEFAULT 'user',
            stripe_customer_id TEXT
        )
    """)
    # Adiciona colunas se ja existir a tabela sem ela (migracao segura)
    for col in [
        "last_seen_at TEXT",
        "username TEXT",
        "email TEXT",
        "birth_date TEXT",
        "password_hash TEXT",
        "whatsapp_verified BOOLEAN DEFAULT 0",
        "google_id TEXT",
        "auth_provider TEXT DEFAULT 'local'",
        "plan_type TEXT DEFAULT 'trial'",
        "trial_started_at TEXT",
        "trial_ends_at TEXT",
        "timezone TEXT DEFAULT 'America/Sao_Paulo'",
        "role TEXT DEFAULT 'user'",
        "stripe_customer_id TEXT"
    ]:
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col}")
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


def _row_to_dict(row) -> dict:
    if not row:
        return None
    return {
        "phone_number": row[0],
        "name": row[1],
        "onboarding_step": row[2],
        "last_seen_at": row[3],
        "username": row[4],
        "email": row[5],
        "birth_date": row[6],
        "password_hash": row[7],
        "whatsapp_verified": bool(row[8]),
        "google_id": row[9],
        "auth_provider": row[10],
        "plan_type": row[11],
        "trial_started_at": row[12],
        "trial_ends_at": row[13],
        "timezone": row[14] if len(row) > 14 else "America/Sao_Paulo",
        "role": row[15] if len(row) > 15 else "user",
        "stripe_customer_id": row[16] if len(row) > 16 else None,
    }


def get_user(phone_number: str) -> Optional[dict]:
    init_db()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                row = conn.execute(
                    text("""SELECT phone_number, name, onboarding_step, last_seen_at, username, email, birth_date, password_hash, whatsapp_verified, google_id, auth_provider, plan_type, trial_started_at, trial_ends_at, timezone, role, stripe_customer_id
                            FROM users WHERE phone_number = :p"""),
                    {"p": phone_number}
                ).fetchone()
            return _row_to_dict(row)
        else:
            conn = _get_sqlite_conn()
            c = conn.cursor()
            c.execute("""SELECT phone_number, name, onboarding_step, last_seen_at, username, email, birth_date, password_hash, whatsapp_verified, google_id, auth_provider, plan_type, trial_started_at, trial_ends_at, timezone, role, stripe_customer_id
                         FROM users WHERE phone_number = ?""", (phone_number,))
            row = c.fetchone()
            conn.close()
            return _row_to_dict(row)
    except Exception as e:
        print(f"[IDENTITY] Erro ao buscar usuario {phone_number}: {e}")
    return None


def get_user_by_email(email: str) -> Optional[dict]:
    init_db()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                row = conn.execute(
                    text("""SELECT phone_number, name, onboarding_step, last_seen_at, username, email, birth_date, password_hash, whatsapp_verified, google_id, auth_provider, plan_type, trial_started_at, trial_ends_at, timezone, role, stripe_customer_id
                            FROM users WHERE email = :e"""),
                    {"e": email}
                ).fetchone()
            return _row_to_dict(row)
        else:
            conn = _get_sqlite_conn()
            c = conn.cursor()
            c.execute("""SELECT phone_number, name, onboarding_step, last_seen_at, username, email, birth_date, password_hash, whatsapp_verified, google_id, auth_provider, plan_type, trial_started_at, trial_ends_at, timezone, role, stripe_customer_id
                         FROM users WHERE email = ?""", (email,))
            row = c.fetchone()
            conn.close()
            return _row_to_dict(row)
    except Exception as e:
        print(f"[IDENTITY] Erro ao buscar usuario por email {email}: {e}")
    return None


def get_user_by_username(username: str) -> Optional[dict]:
    init_db()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                row = conn.execute(
                    text("""SELECT phone_number, name, onboarding_step, last_seen_at, username, email, birth_date, password_hash, whatsapp_verified, google_id, auth_provider, plan_type, trial_started_at, trial_ends_at, timezone, role, stripe_customer_id
                            FROM users WHERE username = :u"""),
                    {"u": username}
                ).fetchone()
            return _row_to_dict(row)
        else:
            conn = _get_sqlite_conn()
            c = conn.cursor()
            c.execute("""SELECT phone_number, name, onboarding_step, last_seen_at, username, email, birth_date, password_hash, whatsapp_verified, google_id, auth_provider, plan_type, trial_started_at, trial_ends_at, timezone, role, stripe_customer_id
                         FROM users WHERE username = ?""", (username,))
            row = c.fetchone()
            conn.close()
            return _row_to_dict(row)
    except Exception as e:
        print(f"[IDENTITY] Erro ao buscar usuario por username {username}: {e}")
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


def create_user_full(phone_number: str, username: str, name: str, email: str, birth_date: str, password_hash: str, google_id: str = None, auth_provider: str = 'local', role: str = 'user'):
    init_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    trial_ends = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                conn.execute(
                    text("""INSERT INTO users 
                            (phone_number, username, name, email, birth_date, password_hash, google_id, auth_provider, onboarding_step, plan_type, trial_started_at, trial_ends_at, whatsapp_verified, role) 
                            VALUES (:p, :u, :n, :e, :b, :pw, :gid, :ap, 'completed', 'trial', :t_start, :t_end, FALSE, :role) 
                            ON CONFLICT (phone_number) DO NOTHING"""),
                    {
                        "p": phone_number, "u": username, "n": name, "e": email, 
                        "b": birth_date, "pw": password_hash, "gid": google_id, "ap": auth_provider,
                        "t_start": now_iso, "t_end": trial_ends, "role": role
                    }
                )
                conn.commit()
        else:
            conn = _get_sqlite_conn()
            conn.execute("""INSERT OR IGNORE INTO users 
                            (phone_number, username, name, email, birth_date, password_hash, google_id, auth_provider, onboarding_step, plan_type, trial_started_at, trial_ends_at, whatsapp_verified, role) 
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'completed', 'trial', ?, ?, 0, ?)""",
                         (phone_number, username, name, email, birth_date, password_hash, google_id, auth_provider, now_iso, trial_ends, role))
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[IDENTITY] Erro ao criar usuario completo {phone_number}: {e}")
        raise e


def promote_user_to_admin(phone_number: str):
    init_db()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                conn.execute(
                    text("UPDATE users SET role = 'admin' WHERE phone_number = :p"),
                    {"p": phone_number}
                )
                conn.commit()
        else:
            conn = _get_sqlite_conn()
            conn.execute("UPDATE users SET role = 'admin' WHERE phone_number = ?", (phone_number,))
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[IDENTITY] Erro ao promover {phone_number} para admin: {e}")


def set_whatsapp_verified(phone_number: str):
    init_db()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                conn.execute(
                    text("UPDATE users SET whatsapp_verified = TRUE, onboarding_step = 'completed' WHERE phone_number = :p"),
                    {"p": phone_number}
                )
                conn.commit()
        else:
            conn = _get_sqlite_conn()
            conn.execute("UPDATE users SET whatsapp_verified = 1, onboarding_step = 'completed' WHERE phone_number = ?", (phone_number,))
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[IDENTITY] Erro ao marcar whatsapp_verified para {phone_number}: {e}")


def link_google_account(email: str, google_id: str):
    init_db()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                conn.execute(
                    text("UPDATE users SET google_id = :gid, auth_provider = 'google' WHERE email = :e"),
                    {"gid": google_id, "e": email}
                )
                conn.commit()
        else:
            conn = _get_sqlite_conn()
            conn.execute("UPDATE users SET google_id = ?, auth_provider = 'google' WHERE email = ?", (google_id, email))
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[IDENTITY] Erro ao vincular google_id para email {email}: {e}")


def update_stripe_customer_id(phone_number: str, customer_id: str):
    init_db()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                conn.execute(
                    text("UPDATE users SET stripe_customer_id = :c WHERE phone_number = :p"),
                    {"c": customer_id, "p": phone_number}
                )
                conn.commit()
        else:
            conn = _get_sqlite_conn()
            conn.execute("UPDATE users SET stripe_customer_id = ? WHERE phone_number = ?", (customer_id, phone_number))
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[IDENTITY] Erro ao atualizar stripe_customer_id para {phone_number}: {e}")


def change_user_phone_number(old_phone_number: str, new_phone_number: str):
    init_db()
    try:
        if get_user(new_phone_number):
            raise ValueError("Novo telefone ja cadastrado")

        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                conn.execute(
                    text("UPDATE users SET phone_number = :new_phone WHERE phone_number = :old_phone"),
                    {"new_phone": new_phone_number, "old_phone": old_phone_number},
                )
                try:
                    conn.execute(
                        text("UPDATE tasks SET user_id = :new_phone WHERE user_id = :old_phone"),
                        {"new_phone": new_phone_number, "old_phone": old_phone_number},
                    )
                except Exception:
                    pass
                try:
                    conn.execute(
                        text("UPDATE subscriptions SET user_id = :new_phone WHERE user_id = :old_phone"),
                        {"new_phone": new_phone_number, "old_phone": old_phone_number},
                    )
                except Exception:
                    pass
                try:
                    conn.execute(
                        text("UPDATE reminders SET user_id = :new_phone WHERE user_id = :old_phone"),
                        {"new_phone": new_phone_number, "old_phone": old_phone_number},
                    )
                except Exception:
                    pass
                try:
                    conn.execute(
                        text("UPDATE usage_events SET user_id = :new_phone WHERE user_id = :old_phone"),
                        {"new_phone": new_phone_number, "old_phone": old_phone_number},
                    )
                except Exception:
                    pass
                conn.commit()
        else:
            conn = _get_sqlite_conn()
            conn.execute("UPDATE users SET phone_number = ? WHERE phone_number = ?", (new_phone_number, old_phone_number))
            try:
                conn.execute("UPDATE tasks SET user_id = ? WHERE user_id = ?", (new_phone_number, old_phone_number))
            except Exception:
                pass
            try:
                conn.execute("UPDATE subscriptions SET user_id = ? WHERE user_id = ?", (new_phone_number, old_phone_number))
            except Exception:
                pass
            try:
                conn.execute("UPDATE usage_events SET user_id = ? WHERE user_id = ?", (new_phone_number, old_phone_number))
            except Exception:
                pass
            conn.commit()
            conn.close()

            try:
                rem_conn = sqlite3.connect("scheduler.db")
                rem_conn.execute("UPDATE reminders SET user_id = ? WHERE user_id = ?", (new_phone_number, old_phone_number))
                rem_conn.commit()
                rem_conn.close()
            except Exception:
                pass
    except Exception as e:
        print(f"[IDENTITY] Erro ao trocar telefone de {old_phone_number} para {new_phone_number}: {e}")
        raise e


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


def is_plan_active(user: dict) -> bool:
    """
    Verifica se o usuario tem plano ativo (não é trial ou o trial ainda não acabou).
    """
    if user.get("role") == "admin":
        return True
        
    if user.get("plan_type") == "trial":
        trial_ends = user.get("trial_ends_at")
        if not trial_ends:
            return False
            
        try:
            if isinstance(trial_ends, str):
                trial_dt = datetime.fromisoformat(trial_ends)
            else:
                trial_dt = trial_ends
                
            if trial_dt.tzinfo is None:
                trial_dt = trial_dt.replace(tzinfo=timezone.utc)
                
            if datetime.now(timezone.utc) < trial_dt:
                return True
        except Exception:
            pass
            
    from src.billing.service import is_subscription_active
    return is_subscription_active(user["phone_number"])

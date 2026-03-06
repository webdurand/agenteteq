import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from src.memory.identity import _use_postgres, _get_pg_engine, _get_sqlite_conn

_billing_initialized = False


def init_billing_db():
    global _billing_initialized
    if _billing_initialized:
        return
    _billing_initialized = True
    if _use_postgres():
        _init_pg()
    else:
        _init_sqlite()


def _init_pg():
    engine = _get_pg_engine()
    with engine.connect() as conn:
        conn.execute(__import__("sqlalchemy").text("""
            CREATE TABLE IF NOT EXISTS billing_plans (
                id SERIAL PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                features_json TEXT DEFAULT '[]',
                is_active BOOLEAN DEFAULT TRUE,
                trial_days INTEGER DEFAULT 7,
                stripe_product_id TEXT,
                stripe_price_id TEXT,
                amount_cents INTEGER NOT NULL,
                currency TEXT DEFAULT 'brl',
                interval TEXT DEFAULT 'month',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))

        conn.execute(__import__("sqlalchemy").text("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                plan_code TEXT NOT NULL,
                provider TEXT DEFAULT 'stripe',
                provider_customer_id TEXT NOT NULL,
                provider_subscription_id TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL DEFAULT 'trialing',
                trial_start TIMESTAMPTZ,
                trial_end TIMESTAMPTZ,
                current_period_start TIMESTAMPTZ,
                current_period_end TIMESTAMPTZ,
                cancel_at_period_end BOOLEAN DEFAULT FALSE,
                canceled_at TIMESTAMPTZ,
                ended_at TIMESTAMPTZ,
                payment_method_summary TEXT,
                last_invoice_id TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))

        conn.execute(__import__("sqlalchemy").text("""
            CREATE TABLE IF NOT EXISTS billing_events (
                id SERIAL PRIMARY KEY,
                event_id TEXT UNIQUE NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT,
                processed_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))

        conn.execute(__import__("sqlalchemy").text("""
            CREATE TABLE IF NOT EXISTS refund_logs (
                id SERIAL PRIMARY KEY,
                subscription_id INTEGER,
                stripe_refund_id TEXT,
                amount_cents INTEGER,
                reason TEXT,
                requested_by TEXT,
                status TEXT DEFAULT 'processed',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """))
        conn.commit()


def _init_sqlite():
    conn = _get_sqlite_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS billing_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            features_json TEXT DEFAULT '[]',
            is_active BOOLEAN DEFAULT 1,
            trial_days INTEGER DEFAULT 7,
            stripe_product_id TEXT,
            stripe_price_id TEXT,
            amount_cents INTEGER NOT NULL,
            currency TEXT DEFAULT 'brl',
            interval TEXT DEFAULT 'month',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            plan_code TEXT NOT NULL,
            provider TEXT DEFAULT 'stripe',
            provider_customer_id TEXT NOT NULL,
            provider_subscription_id TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'trialing',
            trial_start TEXT,
            trial_end TEXT,
            current_period_start TEXT,
            current_period_end TEXT,
            cancel_at_period_end BOOLEAN DEFAULT 0,
            canceled_at TEXT,
            ended_at TEXT,
            payment_method_summary TEXT,
            last_invoice_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS billing_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT,
            processed_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS refund_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subscription_id INTEGER,
            stripe_refund_id TEXT,
            amount_cents INTEGER,
            reason TEXT,
            requested_by TEXT,
            status TEXT DEFAULT 'processed',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def ensure_default_plan():
    price_id = os.getenv("STRIPE_PRICE_ID_DEFAULT", "")
    existing = get_plan("pro_mensal", initialize=False)
    if existing:
        if not existing.get("stripe_price_id") and price_id:
            update_plan("pro_mensal", stripe_price_id=price_id)
        return
    create_plan(
        code="pro_mensal",
        name="Plano Pro Mensal",
        description="Acesso completo ao Teq com tudo liberado e 7 dias gratis.",
        amount_cents=4990,
        trial_days=7,
        stripe_price_id=price_id,
        features_json='["Acesso completo","WhatsApp","Tarefas","Lembretes","Chat por voz"]',
    )

def is_event_processed(event_id: str) -> bool:
    init_billing_db()
    if _use_postgres():
        engine = _get_pg_engine()
        with engine.connect() as conn:
            row = conn.execute(__import__("sqlalchemy").text("SELECT 1 FROM billing_events WHERE event_id = :e"), {"e": event_id}).fetchone()
            return row is not None
    else:
        conn = _get_sqlite_conn()
        row = conn.execute("SELECT 1 FROM billing_events WHERE event_id = ?", (event_id,)).fetchone()
        conn.close()
        return row is not None

def record_billing_event(event_id: str, event_type: str, payload_json: str):
    init_billing_db()
    if _use_postgres():
        engine = _get_pg_engine()
        with engine.connect() as conn:
            conn.execute(
                __import__("sqlalchemy").text("INSERT INTO billing_events (event_id, event_type, payload_json) VALUES (:eid, :etype, :p) ON CONFLICT (event_id) DO NOTHING"),
                {"eid": event_id, "etype": event_type, "p": payload_json}
            )
            conn.commit()
    else:
        conn = _get_sqlite_conn()
        conn.execute("INSERT OR IGNORE INTO billing_events (event_id, event_type, payload_json) VALUES (?, ?, ?)", (event_id, event_type, payload_json))
        conn.commit()
        conn.close()

def upsert_subscription(data: dict):
    init_billing_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    # Ensure current timestamp for updated_at
    data["updated_at"] = now_iso
    
    fields = ["user_id", "plan_code", "provider", "provider_customer_id", "provider_subscription_id",
              "status", "trial_start", "trial_end", "current_period_start", "current_period_end",
              "cancel_at_period_end", "canceled_at", "ended_at", "payment_method_summary", "last_invoice_id", "updated_at"]
    
    if _use_postgres():
        engine = _get_pg_engine()
        with engine.connect() as conn:
            set_clause = ", ".join([f"{f} = :{f}" for f in fields if f in data])
            insert_fields = [f for f in fields if f in data]
            insert_placeholders = [f":{f}" for f in fields if f in data]
            
            q = f"""
                INSERT INTO subscriptions ({", ".join(insert_fields)}) 
                VALUES ({", ".join(insert_placeholders)})
                ON CONFLICT (provider_subscription_id) DO UPDATE SET {set_clause}
            """
            conn.execute(__import__("sqlalchemy").text(q), data)
            conn.commit()
    else:
        conn = _get_sqlite_conn()
        # For sqlite, check if exists
        row = conn.execute("SELECT id FROM subscriptions WHERE provider_subscription_id = ?", (data["provider_subscription_id"],)).fetchone()
        if row:
            set_fields = [f for f in fields if f in data]
            set_clause = ", ".join([f"{f} = ?" for f in set_fields])
            values = [data[f] for f in set_fields] + [data["provider_subscription_id"]]
            conn.execute(f"UPDATE subscriptions SET {set_clause} WHERE provider_subscription_id = ?", values)
        else:
            insert_fields = [f for f in fields if f in data]
            placeholders = ", ".join(["?"] * len(insert_fields))
            values = [data[f] for f in insert_fields]
            conn.execute(f"INSERT INTO subscriptions ({', '.join(insert_fields)}) VALUES ({placeholders})", values)
        conn.commit()
        conn.close()

def get_active_subscription(user_id: str) -> Optional[dict]:
    init_billing_db()
    
    query = """
        SELECT id, user_id, plan_code, provider_customer_id, provider_subscription_id,
               status, trial_end, current_period_end, cancel_at_period_end, payment_method_summary, last_invoice_id
        FROM subscriptions 
        WHERE user_id = :u AND status IN ('active', 'trialing', 'past_due')
        ORDER BY created_at DESC LIMIT 1
    """
    
    if _use_postgres():
        engine = _get_pg_engine()
        with engine.connect() as conn:
            row = conn.execute(__import__("sqlalchemy").text(query), {"u": user_id}).fetchone()
    else:
        conn = _get_sqlite_conn()
        row = conn.execute(query.replace(":u", "?"), (user_id,)).fetchone()
        conn.close()
        
    if not row:
        return None
        
    return {
        "id": row[0],
        "user_id": row[1],
        "plan_code": row[2],
        "provider_customer_id": row[3],
        "provider_subscription_id": row[4],
        "status": row[5],
        "trial_end": row[6],
        "current_period_end": row[7],
        "cancel_at_period_end": bool(row[8]),
        "payment_method_summary": row[9],
        "last_invoice_id": row[10],
    }


def get_plan(code: str, initialize: bool = True) -> Optional[dict]:
    if initialize:
        init_billing_db()
    query = """
        SELECT code, name, description, amount_cents, stripe_price_id, trial_days, currency, interval, features_json, is_active
        FROM billing_plans WHERE code = :c
    """

    if _use_postgres():
        engine = _get_pg_engine()
        with engine.connect() as conn:
            row = conn.execute(__import__("sqlalchemy").text(query), {"c": code}).fetchone()
    else:
        conn = _get_sqlite_conn()
        row = conn.execute(query.replace(":c", "?"), (code,)).fetchone()
        conn.close()
        
    if not row:
        return None
        
    return {
        "code": row[0],
        "name": row[1],
        "description": row[2],
        "amount_cents": row[3],
        "stripe_price_id": row[4],
        "trial_days": row[5],
        "currency": row[6],
        "interval": row[7],
        "features_json": row[8],
        "is_active": bool(row[9]),
    }


def get_plan_by_price_id(price_id: str) -> Optional[dict]:
    init_billing_db()
    query = """
        SELECT code, name, description, amount_cents, stripe_price_id, trial_days, currency, interval, features_json, is_active
        FROM billing_plans WHERE stripe_price_id = :p
        ORDER BY created_at DESC LIMIT 1
    """
    if _use_postgres():
        engine = _get_pg_engine()
        with engine.connect() as conn:
            row = conn.execute(__import__("sqlalchemy").text(query), {"p": price_id}).fetchone()
    else:
        conn = _get_sqlite_conn()
        row = conn.execute(query.replace(":p", "?"), (price_id,)).fetchone()
        conn.close()
    if not row:
        return None
    return {
        "code": row[0],
        "name": row[1],
        "description": row[2],
        "amount_cents": row[3],
        "stripe_price_id": row[4],
        "trial_days": row[5],
        "currency": row[6],
        "interval": row[7],
        "features_json": row[8],
        "is_active": bool(row[9]),
    }


def get_default_active_plan() -> Optional[dict]:
    init_billing_db()
    query = """
        SELECT code, name, description, amount_cents, stripe_price_id, trial_days, currency, interval, features_json, is_active
        FROM billing_plans WHERE is_active = :a
        ORDER BY id ASC LIMIT 1
    """
    active_value = True if _use_postgres() else 1
    if _use_postgres():
        engine = _get_pg_engine()
        with engine.connect() as conn:
            row = conn.execute(__import__("sqlalchemy").text(query), {"a": active_value}).fetchone()
    else:
        conn = _get_sqlite_conn()
        row = conn.execute(query.replace(":a", "?"), (active_value,)).fetchone()
        conn.close()
    if not row:
        return None
    return {
        "code": row[0],
        "name": row[1],
        "description": row[2],
        "amount_cents": row[3],
        "stripe_price_id": row[4],
        "trial_days": row[5],
        "currency": row[6],
        "interval": row[7],
        "features_json": row[8],
        "is_active": bool(row[9]),
    }


def list_plans(active_only: bool = False) -> list[dict]:
    init_billing_db()
    query = """
        SELECT id, code, name, description, features_json, is_active, trial_days, stripe_product_id, stripe_price_id, amount_cents, currency, interval
        FROM billing_plans
    """
    params = {}
    if active_only:
        query += " WHERE is_active = :a"
        params["a"] = True if _use_postgres() else 1
    query += " ORDER BY id ASC"
    if _use_postgres():
        engine = _get_pg_engine()
        with engine.connect() as conn:
            rows = conn.execute(__import__("sqlalchemy").text(query), params).fetchall()
    else:
        conn = _get_sqlite_conn()
        if active_only:
            rows = conn.execute(query.replace(":a", "?"), (1,)).fetchall()
        else:
            rows = conn.execute(query).fetchall()
        conn.close()
    return [
        {
            "id": row[0],
            "code": row[1],
            "name": row[2],
            "description": row[3],
            "features_json": row[4],
            "is_active": bool(row[5]),
            "trial_days": row[6],
            "stripe_product_id": row[7],
            "stripe_price_id": row[8],
            "amount_cents": row[9],
            "currency": row[10],
            "interval": row[11],
        }
        for row in rows
    ]


def create_plan(
    code: str,
    name: str,
    description: str,
    amount_cents: int,
    trial_days: int = 7,
    stripe_product_id: str = "",
    stripe_price_id: str = "",
    currency: str = "brl",
    interval: str = "month",
    features_json: str = "[]",
    is_active: bool = True,
):
    init_billing_db()
    now_iso = datetime.now(timezone.utc).isoformat()
    if _use_postgres():
        engine = _get_pg_engine()
        with engine.connect() as conn:
            conn.execute(
                __import__("sqlalchemy").text("""
                    INSERT INTO billing_plans
                    (code, name, description, features_json, is_active, trial_days, stripe_product_id, stripe_price_id, amount_cents, currency, interval, created_at, updated_at)
                    VALUES (:code, :name, :description, :features_json, :is_active, :trial_days, :stripe_product_id, :stripe_price_id, :amount_cents, :currency, :interval, :created_at, :updated_at)
                    ON CONFLICT (code) DO NOTHING
                """),
                {
                    "code": code,
                    "name": name,
                    "description": description,
                    "features_json": features_json,
                    "is_active": is_active,
                    "trial_days": trial_days,
                    "stripe_product_id": stripe_product_id,
                    "stripe_price_id": stripe_price_id,
                    "amount_cents": amount_cents,
                    "currency": currency,
                    "interval": interval,
                    "created_at": now_iso,
                    "updated_at": now_iso,
                },
            )
            conn.commit()
    else:
        conn = _get_sqlite_conn()
        conn.execute(
            """
            INSERT OR IGNORE INTO billing_plans
            (code, name, description, features_json, is_active, trial_days, stripe_product_id, stripe_price_id, amount_cents, currency, interval, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (code, name, description, features_json, 1 if is_active else 0, trial_days, stripe_product_id, stripe_price_id, amount_cents, currency, interval, now_iso, now_iso),
        )
        conn.commit()
        conn.close()
    return get_plan(code)


def update_plan(code: str, **fields):
    init_billing_db()
    allowed = {
        "name",
        "description",
        "features_json",
        "is_active",
        "trial_days",
        "stripe_product_id",
        "stripe_price_id",
        "amount_cents",
        "currency",
        "interval",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return get_plan(code)
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    if _use_postgres():
        assignments = ", ".join([f"{k} = :{k}" for k in updates.keys()])
        engine = _get_pg_engine()
        with engine.connect() as conn:
            conn.execute(
                __import__("sqlalchemy").text(f"UPDATE billing_plans SET {assignments} WHERE code = :code"),
                {**updates, "code": code},
            )
            conn.commit()
    else:
        conn = _get_sqlite_conn()
        assignments = ", ".join([f"{k} = ?" for k in updates.keys()])
        conn.execute(
            f"UPDATE billing_plans SET {assignments} WHERE code = ?",
            [*updates.values(), code],
        )
        conn.commit()
        conn.close()
    return get_plan(code)


def delete_plan(code: str) -> bool:
    init_billing_db()
    if _use_postgres():
        engine = _get_pg_engine()
        with engine.connect() as conn:
            conn.execute(
                __import__("sqlalchemy").text("DELETE FROM billing_plans WHERE code = :code"),
                {"code": code},
            )
            conn.commit()
    else:
        conn = _get_sqlite_conn()
        conn.execute("DELETE FROM billing_plans WHERE code = ?", (code,))
        conn.commit()
        conn.close()
    return True


def list_subscriptions(limit: int = 100) -> list[dict]:
    init_billing_db()
    query = """
        SELECT id, user_id, plan_code, provider_customer_id, provider_subscription_id, status, current_period_end, cancel_at_period_end
        FROM subscriptions ORDER BY created_at DESC LIMIT :limit
    """
    if _use_postgres():
        engine = _get_pg_engine()
        with engine.connect() as conn:
            rows = conn.execute(__import__("sqlalchemy").text(query), {"limit": limit}).fetchall()
    else:
        conn = _get_sqlite_conn()
        rows = conn.execute(query.replace(":limit", "?"), (limit,)).fetchall()
        conn.close()
    return [
        {
            "id": row[0],
            "user_id": row[1],
            "plan_code": row[2],
            "provider_customer_id": row[3],
            "provider_subscription_id": row[4],
            "status": row[5],
            "current_period_end": row[6],
            "cancel_at_period_end": bool(row[7]),
        }
        for row in rows
    ]


def update_subscription_user_id(old_user_id: str, new_user_id: str):
    init_billing_db()
    if _use_postgres():
        engine = _get_pg_engine()
        with engine.connect() as conn:
            conn.execute(
                __import__("sqlalchemy").text("UPDATE subscriptions SET user_id = :new WHERE user_id = :old"),
                {"new": new_user_id, "old": old_user_id},
            )
            conn.commit()
    else:
        conn = _get_sqlite_conn()
        conn.execute("UPDATE subscriptions SET user_id = ? WHERE user_id = ?", (new_user_id, old_user_id))
        conn.commit()
        conn.close()

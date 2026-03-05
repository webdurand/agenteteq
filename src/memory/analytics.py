import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional
from src.memory.identity import _use_postgres, _get_pg_engine, _get_sqlite_conn, init_db as init_identity_db

def _init_analytics_db():
    try:
        if _use_postgres():
            engine = _get_pg_engine()
            with engine.connect() as conn:
                conn.execute(__import__("sqlalchemy").text("""
                    CREATE TABLE IF NOT EXISTS usage_events (
                        id SERIAL PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        channel TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        tool_name TEXT,
                        status TEXT,
                        latency_ms INTEGER,
                        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                conn.commit()
        else:
            conn = _get_sqlite_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    tool_name TEXT,
                    status TEXT,
                    latency_ms INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[ANALYTICS] Erro ao inicializar banco de analytics: {e}")

# Inicializa tabela
_init_analytics_db()

def log_event(user_id: str, channel: str, event_type: str, tool_name: str = None, status: str = "success", latency_ms: int = None):
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                conn.execute(
                    text("""INSERT INTO usage_events 
                            (user_id, channel, event_type, tool_name, status, latency_ms, created_at) 
                            VALUES (:u, :c, :e, :t, :s, :l, :ca)"""),
                    {
                        "u": user_id, "c": channel, "e": event_type, 
                        "t": tool_name, "s": status, "l": latency_ms, "ca": now_iso
                    }
                )
                conn.commit()
        else:
            conn = _get_sqlite_conn()
            conn.execute("""INSERT INTO usage_events 
                            (user_id, channel, event_type, tool_name, status, latency_ms, created_at) 
                            VALUES (?, ?, ?, ?, ?, ?, ?)""",
                         (user_id, channel, event_type, tool_name, status, latency_ms, now_iso))
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[ANALYTICS] Erro ao gravar evento {event_type} para {user_id}: {e}")

def log_agent_tools(user_id: str, channel: str, agent):
    """Verifica o log do agente para extrair quais tools foram executadas na ultima interacao e logar."""
    try:
        if not agent or getattr(agent, "run_response", None) is None:
            return
            
        # Tenta pegar metrics/tools do Agno (depende da versao, vamos extrair do historico de mensagens)
        if hasattr(agent.memory, "messages"):
            # Para evitar logar todas, pegamos as mais recentes (assumindo que a ultima interacao gerou X mensagens)
            recent_msgs = agent.memory.messages[-10:] 
            for msg in recent_msgs:
                # msg pode ser ModelMessage ou dict
                role = getattr(msg, "role", "")
                if not role and isinstance(msg, dict):
                    role = msg.get("role", "")
                    
                if role == "tool" or role == "function":
                    name = getattr(msg, "name", getattr(msg, "tool_name", "unknown_tool"))
                    if isinstance(msg, dict):
                        name = msg.get("name", msg.get("tool_name", "unknown_tool"))
                    
                    log_event(user_id=user_id, channel=channel, event_type="tool_called", tool_name=name, status="success")
    except Exception as e:
        print(f"[ANALYTICS] Erro ao buscar tools: {e}")

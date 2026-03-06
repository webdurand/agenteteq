import os
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from src.memory.identity import init_db, _use_postgres, _get_pg_engine, _get_sqlite_conn

def save_message(user_id: str, session_id: str, role: str, text: str) -> None:
    init_db()
    try:
        if _use_postgres():
            from sqlalchemy import text as sqla_text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                conn.execute(
                    sqla_text("""
                        INSERT INTO chat_messages (user_id, session_id, role, text, created_at)
                        VALUES (:u, :s, :r, :t, :c)
                    """),
                    {
                        "u": user_id,
                        "s": session_id,
                        "r": role,
                        "t": text,
                        "c": datetime.now(timezone.utc).isoformat()
                    }
                )
                conn.commit()
        else:
            conn = _get_sqlite_conn()
            conn.execute(
                """
                INSERT INTO chat_messages (user_id, session_id, role, text, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, session_id, role, text, datetime.now(timezone.utc).isoformat())
            )
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[CHAT_MESSAGES] Erro ao salvar mensagem para {user_id}: {e}")

def get_messages(user_id: str, limit: int = 20, before_id: Optional[int] = None) -> Dict[str, Any]:
    init_db()
    messages = []
    has_more = False
    try:
        if _use_postgres():
            from sqlalchemy import text as sqla_text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                if before_id:
                    query = sqla_text("""
                        SELECT id, role, text, created_at
                        FROM chat_messages
                        WHERE user_id = :u AND id < :before_id
                        ORDER BY id DESC
                        LIMIT :limit
                    """)
                    params = {"u": user_id, "limit": limit + 1, "before_id": before_id}
                else:
                    query = sqla_text("""
                        SELECT id, role, text, created_at
                        FROM chat_messages
                        WHERE user_id = :u
                        ORDER BY id DESC
                        LIMIT :limit
                    """)
                    params = {"u": user_id, "limit": limit + 1}
                
                rows = conn.execute(query, params).fetchall()
        else:
            conn = _get_sqlite_conn()
            c = conn.cursor()
            if before_id:
                c.execute("""
                    SELECT id, role, text, created_at
                    FROM chat_messages
                    WHERE user_id = ? AND id < ?
                    ORDER BY id DESC
                    LIMIT ?
                """, (user_id, before_id, limit + 1))
            else:
                c.execute("""
                    SELECT id, role, text, created_at
                    FROM chat_messages
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                """, (user_id, limit + 1))
            rows = c.fetchall()
            conn.close()

        for row in rows:
            created_at = row[3]
            if isinstance(created_at, str):
                pass
            elif hasattr(created_at, "isoformat"):
                created_at = created_at.isoformat()
                
            messages.append({
                "id": str(row[0]),
                "role": row[1],
                "text": row[2],
                "timestamp": created_at
            })

        if len(messages) > limit:
            has_more = True
            messages = messages[:limit]

        # Inverte para retornar na ordem cronologica (mais antiga primeiro)
        messages.reverse()

    except Exception as e:
        print(f"[CHAT_MESSAGES] Erro ao buscar mensagens para {user_id}: {e}")

    return {
        "messages": messages,
        "has_more": has_more
    }

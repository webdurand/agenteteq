from datetime import datetime, timezone
from typing import Optional, Dict, Any

from src.db.session import get_db
from src.db.models import ChatMessage


def save_message(user_id: str, session_id: str, role: str, text: str) -> None:
    try:
        with get_db() as db:
            msg = ChatMessage(
                user_id=user_id,
                session_id=session_id,
                role=role,
                text=text,
                created_at=datetime.now(timezone.utc),
            )
            db.add(msg)
    except Exception as e:
        print(f"[CHAT_MESSAGES] Erro ao salvar mensagem para {user_id}: {e}")


def get_messages(user_id: str, limit: int = 20, before_id: Optional[int] = None) -> Dict[str, Any]:
    messages = []
    has_more = False
    try:
        with get_db() as db:
            query = db.query(ChatMessage).filter(ChatMessage.user_id == user_id)
            if before_id:
                query = query.filter(ChatMessage.id < before_id)
            rows = query.order_by(ChatMessage.id.desc()).limit(limit + 1).all()

        for row in rows:
            created_at = row.created_at
            if hasattr(created_at, "isoformat"):
                created_at = created_at.isoformat()

            messages.append({
                "id": str(row.id),
                "role": row.role,
                "text": row.text,
                "timestamp": created_at,
            })

        if len(messages) > limit:
            has_more = True
            messages = messages[:limit]

        messages.reverse()

    except Exception as e:
        print(f"[CHAT_MESSAGES] Erro ao buscar mensagens para {user_id}: {e}")

    return {"messages": messages, "has_more": has_more}

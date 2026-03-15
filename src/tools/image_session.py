import re
import logging
from datetime import datetime

from src.db.session import get_db
from src.db.models import ImageSession
from src.integrations.image_storage import upload_user_image

logger = logging.getLogger(__name__)


def _upsert_image_session(session_id: str, image_type: str, url: str, index: int = 0):
    with get_db() as session:
        row = session.query(ImageSession).filter_by(
            session_id=session_id, image_type=image_type, image_index=index
        ).first()
        if row:
            row.image_url = url
            row.created_at = datetime.utcnow().isoformat()
        else:
            session.add(ImageSession(
                session_id=session_id,
                image_type=image_type,
                image_index=index,
                image_url=url,
            ))

def _get_image_sessions(session_id: str) -> list:
    with get_db() as session:
        rows = session.query(ImageSession).filter_by(
            session_id=session_id
        ).order_by(ImageSession.image_index).all()
        return [{"image_type": r.image_type, "image_url": r.image_url} for r in rows]

def store_session_images(session_id: str, images: list[bytes]):
    """Armazena imagens enviadas pelo usuário (originais) fazendo upload pro Cloudinary."""
    for i, img_bytes in enumerate(images):
        url = upload_user_image(session_id, img_bytes)
        _upsert_image_session(session_id, "original", url, i)

def store_generated_image(session_id: str, image_url: str):
    """Armazena a URL da última imagem gerada/editada."""
    _upsert_image_session(session_id, "generated", image_url, 0)

def get_session_images(session_id: str) -> dict:
    rows = _get_image_sessions(session_id)
    return {
        "originals": [r["image_url"] for r in rows if r["image_type"] == "original"],
        "last_generated": next((r["image_url"] for r in rows if r["image_type"] == "generated"), None)
    }

def clear_session_images(session_id: str):
    with get_db() as session:
        session.query(ImageSession).filter_by(session_id=session_id).delete()

def _try_recover_last_image(user_id: str) -> str | None:
    """
    Fallback: busca a última imagem gerada/editada no histórico de chat
    e retorna a URL para permitir edições encadeadas.
    """
    try:
        from src.models.chat_messages import get_messages

        result = get_messages(user_id=user_id, limit=10)
        msgs = result.get("messages", [])

        for msg in reversed(msgs):
            if msg.get("role") != "agent":
                continue
            text = msg.get("text", "")
            urls = re.findall(r'https?://res\.cloudinary\.com/\S+', text)
            if not urls:
                urls = re.findall(r'https?://[^\s]+\.(?:jpg|jpeg|png|webp)', text)
            if urls:
                url = urls[-1]
                logger.info("Recuperando última imagem do histórico: %s", url)
                return url

    except Exception as e:
        logger.error("Falha ao recuperar imagem do histórico: %s", e)

    return None

import asyncio
import io
import os
from typing import List, Optional
from datetime import datetime
from src.tools.image_generation import get_image_provider
from src.events import emit_event_sync

import cloudinary
import cloudinary.uploader

if os.getenv("CLOUDINARY_CLOUD_NAME"):
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
        secure=True
    )

from src.config.system_config import _get_pg_engine, _get_sqlite_conn
from src.integrations.image_storage import upload_user_image

def _upsert_image_session(session_id: str, image_type: str, url: str, index: int = 0):
    engine = _get_pg_engine()
    if engine:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO image_sessions (session_id, image_type, image_url, image_index)
                VALUES (:sid, :type, :url, :idx)
                ON CONFLICT (session_id, image_type, image_index) DO UPDATE SET image_url = :url, created_at = NOW()
            """), {"sid": session_id, "type": image_type, "url": url, "idx": index})
            conn.commit()
    else:
        with _get_sqlite_conn() as conn:
            conn.execute("""
                INSERT INTO image_sessions (session_id, image_type, image_url, image_index)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (session_id, image_type, image_index) DO UPDATE SET image_url = excluded.image_url, created_at = CURRENT_TIMESTAMP
            """, (session_id, image_type, url, index))
            conn.commit()

def _get_image_sessions(session_id: str) -> list:
    engine = _get_pg_engine()
    if engine:
        from sqlalchemy import text
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT image_type, image_url FROM image_sessions WHERE session_id = :sid ORDER BY image_index ASC"), {"sid": session_id}).fetchall()
            return [{"image_type": r[0], "image_url": r[1]} for r in rows]
    else:
        with _get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT image_type, image_url FROM image_sessions WHERE session_id = ? ORDER BY image_index ASC", (session_id,))
            return [{"image_type": r[0], "image_url": r[1]} for r in cursor.fetchall()]

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
    engine = _get_pg_engine()
    if engine:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM image_sessions WHERE session_id = :sid"), {"sid": session_id})
            conn.commit()
    else:
        with _get_sqlite_conn() as conn:
            conn.execute("DELETE FROM image_sessions WHERE session_id = ?", (session_id,))
            conn.commit()


def _try_recover_last_image(user_id: str) -> str | None:
    """
    Fallback: busca a última imagem gerada/editada no histórico de chat
    e retorna a URL para permitir edições encadeadas.
    """
    try:
        import re
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
                print(f"[IMAGE_EDITOR] Recuperando última imagem do histórico: {url}")
                return url

    except Exception as e:
        print(f"[IMAGE_EDITOR] Falha ao recuperar imagem do histórico: {e}")

    return None


async def _process_edit_background(
    user_id: str,
    edit_prompt: str,
    reference_bytes: bytes,
    aspect_ratio: str = "1:1",
    channel: str = "web",
):
    try:
        provider = get_image_provider()

        from src.endpoints.web import ws_manager
        await ws_manager.send_personal_message(user_id, {
            "type": "image_editing",
            "prompt": edit_prompt[:100],
        })

        result_bytes = await provider.edit(edit_prompt, reference_bytes, aspect_ratio=aspect_ratio)

        loop = asyncio.get_event_loop()

        def _upload():
            file_obj = io.BytesIO(result_bytes)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            return cloudinary.uploader.upload(
                file_obj,
                folder=f"edited_images/{user_id}",
                public_id=f"edit_{ts}",
                overwrite=True,
            )

        upload_result = await loop.run_in_executor(None, _upload)
        image_url = upload_result.get("secure_url")
        store_generated_image(user_id, image_url)
        print(f"[IMAGE_EDITOR] Imagem editada: {image_url}")

        if channel in ("web", "web_voice", "web_text"):
            await ws_manager.send_personal_message(user_id, {
                "type": "image_edit_ready",
                "image_url": image_url,
                "prompt": edit_prompt[:100],
            })

            try:
                from src.models.chat_messages import save_message
                formatted = f"Pronto! Aqui está a imagem editada:\n{image_url}"
                await asyncio.to_thread(save_message, user_id, user_id, "agent", formatted)
            except Exception as e:
                print(f"[IMAGE_EDITOR] Erro ao persistir mensagem: {e}")

        elif channel in ("whatsapp_text", "whatsapp"):
            try:
                from src.integrations.whatsapp import whatsapp_client
                await whatsapp_client.send_image(user_id, image_url, caption="Aqui está a imagem editada!")
            except Exception as e:
                print(f"[IMAGE_EDITOR] Erro ao enviar imagem via WhatsApp: {e}")

        try:
            from src.models.carousel import create_carousel, update_carousel_status
            from src.events import emit_event_sync
            slide = [{"prompt": edit_prompt[:200], "style": "Edição de imagem", "image_url": image_url}]
            cid = create_carousel(user_id, f"Edição: {edit_prompt[:60]}", slide)
            update_carousel_status(cid, "done", slide)
            emit_event_sync(user_id, "carousel_generated")
        except Exception as e:
            print(f"[IMAGE_EDITOR] Erro ao salvar na galeria: {e}")

        from src.integrations.image_storage import describe_and_store_images
        await describe_and_store_images(user_id, [result_bytes], pre_uploaded_urls=[image_url])

    except Exception as e:
        import traceback
        print(f"[IMAGE_EDITOR] Erro na edição: {e}\n{traceback.format_exc()}")

        try:
            from src.endpoints.web import ws_manager
            await ws_manager.send_personal_message(user_id, {
                "type": "image_edit_ready",
                "image_url": None,
                "error": str(e),
                "prompt": edit_prompt[:100],
            })
        except Exception:
            pass


def create_image_editor_tools(user_id: str, channel: str = "web"):
    from src.tools.image_generation.base import resolve_aspect_ratio

    def edit_image_tool(
        edit_instructions: str,
        source: str = "auto",
        format: str = "1:1",
    ) -> str:
        """
        Edita ou transforma uma imagem que o usuário enviou ou que foi gerada recentemente.
        A imagem é processada em background e o resultado é enviado ao usuário.

        Use esta tool quando o usuário:
        - Enviar uma imagem e pedir para modificar (adicionar/remover objetos, mudar fundo, etc.)
        - Pedir para transformar o estilo (cartoon, pintura, minimalista, hiper-realista, etc.)
        - Pedir ajustes na última imagem gerada ("faz mais realista", "muda o fundo", etc.)
        - Gerar uma nova versão baseada numa imagem anterior

        Args:
            edit_instructions: Instrução detalhada do que fazer com a imagem.
                               SEJA ESPECÍFICO e inclua TODAS as modificações desejadas.
                               Ex: "Adicione um dragão voando ao fundo desta cena"
                               Ex: "Transforme esta foto em estilo aquarela"
                               Ex: "Recrie a pessoa desta foto como um mago de D&D, fotorrealista, qualidade 8K"
            source: Qual imagem usar como referência:
                    - "original": Usa a foto ORIGINAL enviada pelo usuário.
                      PREFIRA ESTA OPÇÃO quando o pedido envolve mudança de estilo radical
                      (ex: "faz mais realista", "muda o estilo", "parece um desenho, quero foto real").
                      A foto original do usuário é sempre a melhor base para recriar em outro estilo.
                    - "last_generated": Usa a ÚLTIMA imagem gerada/editada.
                      Use quando o pedido é um ajuste incremental na imagem já gerada
                      (ex: "muda o fundo", "adiciona um chapéu", "tira a barba").
                    - "auto" (padrão): Escolhe automaticamente. Se houver foto original E o pedido
                      parecer uma recriação de estilo, usa a original. Senão, usa a última gerada.
            format: Formato/dimensão da imagem de saída.
                    Exemplos: "1:1" (quadrado), "4:3" (landscape), "9:16" (stories), "16:9" (widescreen).

        Returns:
            Mensagem de confirmação. A imagem editada será enviada automaticamente.
        """
        session = get_session_images(user_id)
        originals = session["originals"]
        last_gen = session["last_generated"]

        if not originals and not last_gen:
            recovered = _try_recover_last_image(user_id)
            if recovered:
                store_generated_image(user_id, recovered)
                last_gen = recovered

        if not originals and not last_gen:
            return (
                "Não encontrei nenhuma imagem na conversa atual. "
                "O usuário precisa enviar uma imagem junto com o pedido de edição."
            )

        if source == "original":
            reference_url = originals[0] if originals else last_gen
        elif source == "last_generated":
            reference_url = last_gen if last_gen else (originals[0] if originals else None)
        else:
            reference_url = originals[0] if originals else last_gen

        if not reference_url:
            return "Não encontrei a imagem de referência solicitada."

        source_label = "original" if reference_url == (originals[0] if originals else None) else "última gerada"
        aspect_ratio = resolve_aspect_ratio(format)

        print(f"[IMAGE_EDITOR] edit_image_tool | user={user_id} | channel={channel} | source={source} ({source_label}) | format={format} ({aspect_ratio}) | prompt='{edit_instructions[:80]}'")

        import httpx
        from src.queue.task_queue import enqueue_task
        
        result = enqueue_task(user_id, "image_edit", channel, {
            "edit_instructions": edit_instructions,
            "reference_url": reference_url,
            "aspect_ratio": aspect_ratio
        })
        
        if result["status"] == "queued":
            return (
                f"Edição de imagem na fila (ref: {source_label})! "
                f"Posição {result['position']}. Estimativa: ~{result['estimated_wait']}."
            )
        elif result["status"] == "limit_reached":
            return f"Você já tem {result['pending_count']} pedidos na fila. Aguarde!"
        elif result["status"] == "daily_limit":
            return f"Limite de {result['daily_limit']} edições por dia atingido."
            
        return "Erro desconhecido ao colocar na fila."

    return edit_image_tool

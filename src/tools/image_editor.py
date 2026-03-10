import asyncio
import io
import os
from typing import List, Optional
from datetime import datetime
from src.tools.image_generation import get_image_provider
from src.events import emit_event_sync

import cloudinary
import cloudinary.uploader

from src.integrations.image_storage import upload_user_image, _ensure_cloudinary_config, convert_to_webp
from src.db.session import get_db
from src.db.models import ImageSession
import logging

logger = logging.getLogger(__name__)

_ensure_cloudinary_config()

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
                logger.info("Recuperando última imagem do histórico: %s", url)
                return url

    except Exception as e:
        logger.error("Falha ao recuperar imagem do histórico: %s", e)

    return None

async def _process_edit_background(
    user_id: str,
    edit_prompt: str,
    reference_bytes: bytes,
    aspect_ratio: str = "1:1",
    channel: str = "web",
    task_id: Optional[str] = None,
):
    try:
        provider = get_image_provider()

        # Notifica via WS apenas para canais web
        if channel in ("web", "web_voice", "web_text"):
            from src.endpoints.web import ws_manager
            await ws_manager.send_personal_message(user_id, {
                "type": "image_editing",
                "prompt": edit_prompt[:100],
            })

        # Persiste placeholder no DB para sobreviver a refresh (canais web)
        if channel in ("web", "web_voice", "web_text"):
            try:
                import json as _json
                from src.models.chat_messages import save_message
                placeholder = "__IMAGE_EDITING__" + _json.dumps({"prompt": edit_prompt[:100]})
                await asyncio.to_thread(save_message, user_id, user_id, "agent", placeholder)
            except Exception as e:
                logger.error("Erro ao persistir placeholder de edição: %s", e)

        # Check cancellation before expensive API call
        if task_id:
            from src.queue.task_queue import is_task_cancelled
            if is_task_cancelled(task_id):
                logger.info("Edição cancelada antes de iniciar — task %s", task_id)
                raise Exception("cancelled by user")

        result_bytes = await provider.edit(edit_prompt, reference_bytes, aspect_ratio=aspect_ratio)

        loop = asyncio.get_event_loop()

        def _upload():
            webp_bytes = convert_to_webp(result_bytes)
            file_obj = io.BytesIO(webp_bytes)
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
        logger.info("Imagem editada: %s", image_url)

        from src.events_broadcast import emit_action_log
        await emit_action_log(user_id, "Imagem editada", edit_prompt[:100], channel)

        if channel in ("web", "web_voice", "web_text"):
            await ws_manager.send_personal_message(user_id, {
                "type": "image_edit_ready",
                "image_url": image_url,
                "prompt": edit_prompt[:100],
            })

            # Atualiza o placeholder __IMAGE_EDITING__ com o resultado
            try:
                from src.models.chat_messages import update_message_by_prefix
                formatted = f"Pronto! Aqui está a imagem editada:\n{image_url}"
                updated = await asyncio.to_thread(
                    update_message_by_prefix, user_id,
                    "__IMAGE_EDITING__",
                    formatted,
                )
                if not updated:
                    from src.models.chat_messages import save_message
                    await asyncio.to_thread(save_message, user_id, user_id, "agent", formatted)
            except Exception as e:
                logger.error("Erro ao persistir mensagem: %s", e)

        elif channel in ("whatsapp_text", "whatsapp"):
            try:
                from src.integrations.whatsapp import whatsapp_client
                await whatsapp_client.send_image(user_id, image_url, caption="Aqui está a imagem editada!")
            except Exception as e:
                logger.error("Erro ao enviar imagem via WhatsApp: %s", e)

        try:
            from src.models.carousel import create_carousel, update_carousel_status
            from src.events import emit_event_sync
            slide = [{"prompt": edit_prompt[:200], "style": "Edição de imagem", "image_url": image_url}]
            cid = create_carousel(user_id, f"Edição: {edit_prompt[:60]}", slide)
            update_carousel_status(cid, "done", slide)
            emit_event_sync(user_id, "carousel_generated")
        except Exception as e:
            logger.error("Erro ao salvar na galeria: %s", e)

        from src.integrations.image_storage import index_user_image
        await asyncio.to_thread(index_user_image, user_id, image_url, f"Imagem editada: {edit_prompt[:200]}")

    except Exception as e:
        import traceback
        logger.error("Erro na edição: %s\n%s", e, traceback.format_exc())

        if channel in ("web", "web_voice", "web_text"):
            try:
                from src.endpoints.web import ws_manager
                await ws_manager.send_personal_message(user_id, {
                    "type": "image_edit_ready",
                    "image_url": None,
                    "error": str(e),
                    "prompt": edit_prompt[:100],
                })
                from src.models.chat_messages import update_message_by_prefix
                await asyncio.to_thread(
                    update_message_by_prefix, user_id,
                    "__IMAGE_EDITING__",
                    "❌ Erro ao editar a imagem. Tente novamente.",
                )
            except Exception:
                pass
        elif channel in ("whatsapp_text", "whatsapp"):
            try:
                from src.integrations.whatsapp import whatsapp_client
                await whatsapp_client.send_text_message(user_id, "❌ Eita, deu um erro ao editar a imagem. Tenta de novo!")
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
        Edita ou transforma uma imagem que o usuário EXPLICITAMENTE pediu para modificar.
        A imagem é processada em background e o resultado é enviado ao usuário.

        IMPORTANTE: NÃO use esta tool para gerar imagens novas do zero.
        Para gerar imagens novas sem referência, use generate_carousel com 1 slide.

        Use esta tool SOMENTE quando o usuário:
        - Enviar uma imagem e pedir para modificar (adicionar/remover objetos, mudar fundo, etc.)
        - Pedir para transformar o estilo de uma imagem existente (cartoon, pintura, etc.)
        - Pedir ajustes na última imagem gerada ("muda o fundo", "adiciona um chapéu", etc.)
        - Referir-se EXPLICITAMENTE a uma imagem anterior para editar

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
        from src.queue.task_queue import check_daily_limit
        limit_msg = check_daily_limit(user_id)
        if limit_msg:
            return limit_msg

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

        logger.info("edit_image_tool | user=%s | channel=%s | source=%s (%s) | format=%s (%s) | prompt='%s'", user_id, channel, source, source_label, format, aspect_ratio, edit_instructions[:80])

        import httpx
        from src.queue.task_queue import enqueue_task

        result = enqueue_task(user_id, "image_edit", channel, {
            "edit_instructions": edit_instructions,
            "reference_url": reference_url,
            "aspect_ratio": aspect_ratio
        })
        
        if result["status"] == "queued":
            if channel in ("web", "web_voice", "web_text"):
                # UI já exibe loading bubble e entrega a imagem — agente NÃO deve falar nada
                return (
                    "[IMAGEM EM EDICAO — NAO ESCREVA NADA SOBRE ISSO. "
                    "O usuario ja esta vendo o progresso visualmente na interface. "
                    "Responda APENAS se o usuario tiver feito uma pergunta adicional, caso contrario fique em silencio.]"
                )
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

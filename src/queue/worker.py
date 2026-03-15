import asyncio
import logging
import traceback
import ipaddress
from urllib.parse import urlparse

import httpx
from src.queue.task_queue import claim_next_task, complete_task, fail_task, count_processing_tasks, is_task_cancelled
from src.config.system_config import get_config

logger = logging.getLogger(__name__)

_ALLOWED_HOSTS = {"res.cloudinary.com", "oaidalleapiprodscus.blob.core.windows.net"}

def _validate_url(url: str):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Invalid scheme: {parsed.scheme}")
    hostname = parsed.hostname or ""
    if any(hostname == h or hostname.endswith("." + h) for h in _ALLOWED_HOSTS):
        return
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_reserved:
            raise ValueError(f"Private IP not allowed: {hostname}")
    except ValueError:
        pass

async def _download_image(url: str) -> bytes:
    _validate_url(url)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content

async def process_task_queue():
    processing = count_processing_tasks()
    max_global = int(get_config("max_global_processing", "3"))
    if processing >= max_global:
        return

    task = claim_next_task()
    if not task:
        return

    if is_task_cancelled(task["id"]):
        logger.info("Task %s foi cancelada, pulando", task['id'])
        return

    try:
        if task["task_type"] == "image":
            from src.tools.image_generator import _process_image_background
            payload = task["payload"]

            reference_bytes = None
            ref_url = payload.get("reference_image_url")
            if ref_url:
                reference_bytes = await _download_image(ref_url)

            await _process_image_background(
                carousel_id=payload["carousel_id"],
                user_id=task["user_id"],
                slides=payload["slides"],
                channel=task["channel"],
                aspect_ratio=payload.get("aspect_ratio", "4:3"),
                reference_image=reference_bytes,
                task_id=task["id"],
                sequential_slides=payload.get("sequential_slides", True),
                is_edit=payload.get("is_edit", False),
            )
            if not is_task_cancelled(task["id"]):
                complete_task(task["id"], {"status": "success", "type": "image"})
            else:
                logger.info("Task %s cancelada durante processamento, nao marcando como done", task["id"])

        elif task["task_type"] == "carousel":
            # Legacy fallback for in-flight tasks
            from src.tools.image_generator import _process_image_background
            payload = task["payload"]

            reference_bytes = None
            ref_url = payload.get("reference_image_url")
            if ref_url:
                reference_bytes = await _download_image(ref_url)

            await _process_image_background(
                carousel_id=payload["carousel_id"],
                user_id=task["user_id"],
                slides=payload["slides"],
                channel=task["channel"],
                aspect_ratio=payload.get("aspect_ratio", "4:3"),
                reference_image=reference_bytes,
                task_id=task["id"],
                sequential_slides=payload.get("sequential_slides", True),
            )
            if not is_task_cancelled(task["id"]):
                complete_task(task["id"], {"status": "success", "type": "carousel"})
            else:
                logger.info("Task %s cancelada durante processamento, nao marcando como done", task["id"])

        elif task["task_type"] == "image_edit":
            # Legacy fallback for in-flight tasks
            from src.tools.image_editor import _process_edit_background
            payload = task["payload"]

            ref_url = payload["reference_url"]
            reference_bytes = await _download_image(ref_url)

            await _process_edit_background(
                user_id=task["user_id"],
                edit_prompt=payload["edit_instructions"],
                reference_bytes=reference_bytes,
                aspect_ratio=payload.get("aspect_ratio", "1:1"),
                channel=task["channel"],
                task_id=task["id"],
            )
            if not is_task_cancelled(task["id"]):
                complete_task(task["id"], {"status": "success", "type": "image_edit"})
            else:
                logger.info("Task %s cancelada durante processamento, nao marcando como done", task["id"])
            
    except Exception as e:
        logger.error("Erro ao processar task %s: %s", task['id'], e, exc_info=True)
        final = fail_task(task["id"], str(e))
        if final:
            await _notify_task_failure(task)

async def _notify_task_failure(task: dict):
    """Notify user when a background task exhausts all retries."""
    user_id = task.get("user_id", "")
    channel = task.get("channel", "")
    if not user_id:
        return

    send_whatsapp = channel in ("whatsapp_text", "whatsapp", "web_whatsapp")
    send_web = channel in ("web", "web_voice", "web_text", "web_whatsapp")

    if send_whatsapp:
        try:
            from src.integrations.whatsapp import whatsapp_client
            await whatsapp_client.send_text_message(
                user_id,
                "❌ Não consegui completar a tarefa após várias tentativas. Tente novamente em alguns minutos.",
            )
        except Exception as e:
            logger.error("Falha ao notificar usuario %s via WhatsApp: %s", user_id[:8], e)

    if send_web:
        try:
            from src.endpoints.web import ws_manager
            payload = task.get("payload", {})
            carousel_id = payload.get("carousel_id", "")
            if carousel_id:
                await ws_manager.send_personal_message(user_id, {
                    "type": "carousel_failed",
                    "carousel_id": carousel_id,
                })
        except Exception as e:
            logger.error("Falha ao notificar usuario %s via WS: %s", user_id[:8], e)


def _run_worker_sync():
    from src.events import _main_loop
    if _main_loop and _main_loop.is_running():
        asyncio.run_coroutine_threadsafe(process_task_queue(), _main_loop)

import asyncio
import traceback
import ipaddress
from urllib.parse import urlparse

import httpx
from src.queue.task_queue import claim_next_task, complete_task, fail_task, count_processing_tasks, is_task_cancelled
from src.config.system_config import get_config

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
        print(f"[WORKER] Task {task['id']} foi cancelada, pulando")
        return

    try:
        if task["task_type"] == "carousel":
            from src.tools.carousel_generator import _process_carousel_background
            payload = task["payload"]
            
            reference_bytes = None
            ref_url = payload.get("reference_image_url")
            if ref_url:
                reference_bytes = await _download_image(ref_url)
            
            await _process_carousel_background(
                carousel_id=payload["carousel_id"],
                user_id=task["user_id"],
                slides=payload["slides"],
                channel=task["channel"],
                aspect_ratio=payload.get("aspect_ratio", "4:3"),
                reference_image=reference_bytes
            )
            complete_task(task["id"], {"status": "success", "type": "carousel"})
            
        elif task["task_type"] == "image_edit":
            from src.tools.image_editor import _process_edit_background
            payload = task["payload"]
            
            ref_url = payload["reference_url"]
            reference_bytes = await _download_image(ref_url)
            
            await _process_edit_background(
                user_id=task["user_id"],
                edit_prompt=payload["edit_instructions"],
                reference_bytes=reference_bytes,
                aspect_ratio=payload.get("aspect_ratio", "1:1"),
                channel=task["channel"]
            )
            complete_task(task["id"], {"status": "success", "type": "image_edit"})
            
    except Exception as e:
        print(f"[WORKER] Erro ao processar task {task['id']}: {e}\n{traceback.format_exc()}")
        fail_task(task["id"], str(e))

def _run_worker_sync():
    from src.events import _main_loop
    if _main_loop and _main_loop.is_running():
        asyncio.run_coroutine_threadsafe(process_task_queue(), _main_loop)

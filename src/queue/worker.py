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
            generation_mode = payload.get("generation_mode", "ai")

            # Only download reference for AI mode (HTML carousel handles its own download)
            if ref_url and generation_mode != "html":
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
                generation_mode=generation_mode,
                format=payload.get("format", "1080x1080"),
                reference_image_url=ref_url,
                brand_profile=payload.get("brand_profile"),
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

        elif task["task_type"] == "video":
            from src.video.pipeline import run_pipeline
            payload = task["payload"]

            # Load script: try DB → inline payload → generate on-the-fly
            import json as _json
            script = None
            script_id = payload.get("script_id")
            topic_fallback = payload.get("topic", "")

            # 1. Try loading from DB
            if script_id:
                try:
                    from src.db.session import get_db
                    from src.db.models import VideoScript
                    with get_db() as session:
                        db_script = session.get(VideoScript, script_id)
                        # Fallback: partial ID match (agent sends first 8 chars)
                        if not db_script:
                            db_script = session.query(VideoScript).filter(
                                VideoScript.id.startswith(script_id)
                            ).first()
                        if db_script and db_script.script_json:
                            script = _json.loads(db_script.script_json)
                            topic_fallback = topic_fallback or db_script.topic or ""
                            logger.info("Loaded script %s from DB", script_id[:8])
                        else:
                            logger.warning("Video script %s not found in DB", script_id[:8])
                except Exception as e:
                    logger.warning("Failed to load script %s from DB: %s", script_id[:8], e)

            # 2. Try inline script from payload (fallback when DB save failed)
            if not script and payload.get("script_json"):
                try:
                    script = _json.loads(payload["script_json"]) if isinstance(payload["script_json"], str) else payload["script_json"]
                    logger.info("Using inline script from payload")
                except Exception as e:
                    logger.warning("Failed to parse inline script_json: %s", e)

            # 3. Generate on-the-fly from topic
            if not script and topic_fallback:
                logger.info("Generating script on-the-fly for topic: %s", topic_fallback[:50])
                from src.video.script_generator import generate_script
                script = generate_script(
                    topic=topic_fallback,
                    style=payload.get("style", "tutorial"),
                    duration=payload.get("duration", 60),
                )

            if not script or (isinstance(script, dict) and "error" in script):
                error_detail = script.get("error", "Unknown") if isinstance(script, dict) else "No script_id, no inline script, and no topic provided"
                raise RuntimeError(f"Could not load/generate script: {error_detail}")

            # Create VideoProject record (ensure table exists)
            import uuid as _uuid
            from datetime import datetime as _dt, timezone as _tz
            from src.db.session import get_db, get_engine
            from src.db.models import VideoProject, VideoScript
            try:
                VideoProject.__table__.create(get_engine(), checkfirst=True)
                VideoScript.__table__.create(get_engine(), checkfirst=True)
            except Exception:
                pass

            now = _dt.now(_tz.utc).isoformat()
            initial_step = "initializing"
            with get_db() as session:
                # Idempotency: reuse existing VideoProject for this task
                existing = session.query(VideoProject).filter(
                    VideoProject.task_id == task["id"]
                ).first()
                if existing:
                    if existing.status == "failed":
                        raise RuntimeError("Video task already failed, not retrying")
                    project_id = existing.id
                    logger.info("Reusing VideoProject %s for task %s", project_id[:8], task["id"][:8])
                else:
                    project_id = str(_uuid.uuid4())
                    project = VideoProject(
                        id=project_id,
                        task_id=task["id"],
                        user_id=task["user_id"],
                        script_id=script_id or "",
                        source_type=payload.get("source_type", "avatar"),
                        source_url=payload.get("photo_url") or payload.get("video_url") or "",
                        status=initial_step,
                        current_step=initial_step,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(project)

            # Inject avatar_id into script for pipeline to use
            if payload.get("avatar_id"):
                script["_avatar_id"] = payload["avatar_id"]

            await run_pipeline(
                user_id=task["user_id"],
                project_id=project_id,
                script=script,
                source_type=payload.get("source_type", "avatar"),
                source_url=payload.get("photo_url") or payload.get("video_url") or "",
                channel=task["channel"],
                voice=payload.get("voice", ""),
                task_id=task["id"],
            )

            # Pipeline handles its own errors internally (no re-raise).
            # Check VideoProject status to determine outcome.
            with get_db() as session:
                vp = session.get(VideoProject, project_id)
                vp_status = vp.status if vp else "failed"
                vp_error = vp.error_message if vp else "VideoProject not found"

            if vp_status == "failed":
                fail_task(task["id"], vp_error or "pipeline failed")
                await _notify_task_failure(task)
            elif not is_task_cancelled(task["id"]):
                complete_task(task["id"], {"status": "success", "type": "video", "project_id": project_id})

    except Exception as e:
        logger.error("Erro ao processar task %s: %s", task['id'], e, exc_info=True)
        final = fail_task(task["id"], str(e))

        # Mark only THIS VideoProject as failed (not all user projects)
        if task.get("task_type") == "video":
            try:
                from src.db.session import get_db
                from src.db.models import VideoProject
                from datetime import datetime as _dt, timezone as _tz
                with get_db() as session:
                    vp = session.query(VideoProject).filter(
                        VideoProject.task_id == task["id"]
                    ).first()
                    if vp and vp.status not in ("done", "failed"):
                        vp.status = "failed"
                        vp.current_step = "failed"
                        vp.error_message = str(e)[:500]
                        vp.updated_at = _dt.now(_tz.utc).isoformat()
                        logger.info("Marked VideoProject %s as failed for task %s", vp.id[:8], task["id"][:8])
            except Exception as vp_err:
                logger.error("Failed to cleanup VideoProject: %s", vp_err)

            # Also clear the __VIDEO_GENERATING__ chat message
            try:
                from src.models.chat_messages import update_message_by_prefix
                import json
                error_payload = json.dumps({"error": str(e)[:200]})
                update_message_by_prefix(task["user_id"], "__VIDEO_GENERATING__", f"__VIDEO_FAILED__{error_payload}")
            except Exception:
                pass

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

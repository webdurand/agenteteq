"""
Video generation pipeline — HeyGen Standard.
Flow: script → HeyGen multi-scene video (Digital Twin voice) → Cloudinary upload.
"""

import json
import logging
from datetime import datetime, timezone

import cloudinary.uploader

logger = logging.getLogger(__name__)


async def run_pipeline(
    user_id: str,
    project_id: str,
    script: dict,
    source_type: str,
    source_url: str,
    channel: str = "web",
    voice: str = "",
    task_id: str = "",
):
    from src.db.session import get_db
    from src.db.models import VideoProject

    # ── Helpers (closures over project_id, user_id, task_id) ──

    def _update_status(step: str, error: str = ""):
        now = datetime.now(timezone.utc).isoformat()
        with get_db() as session:
            project = session.get(VideoProject, project_id)
            if project:
                project.status = "failed" if error else step
                project.current_step = step
                project.updated_at = now
                if error:
                    project.error_message = error[:500]

    _script_title = script.get("title", "")

    def _update_chat_step(step: str, detail: str = ""):
        try:
            from src.models.chat_messages import update_message_by_prefix
            payload = {"current_step": step, "title": _script_title, "task_id": task_id}
            if detail:
                payload["step_detail"] = detail
            update_message_by_prefix(
                user_id, "__VIDEO_GENERATING__",
                "__VIDEO_GENERATING__" + json.dumps(payload),
            )
        except Exception:
            pass

    def _check_cancelled():
        if task_id:
            from src.queue.task_queue import is_task_cancelled
            if is_task_cancelled(task_id):
                raise RuntimeError("cancelled by user")

    _notify_progress(user_id, channel, "Iniciando geracao do video...")

    # ── Guard: only 1 video at a time ──
    from src.db.models import VideoProject as _VP
    with get_db() as session:
        active_count = session.query(_VP).filter(
            _VP.user_id == user_id,
            _VP.id != project_id,
            _VP.status.notin_(["done", "failed"]),
        ).count()
        if active_count > 0:
            raise RuntimeError(
                f"Ja tem {active_count} video(s) sendo processado(s). "
                "Aguarde terminar ou cancele pela aba Fila."
            )

    # ── Force to heygen (only supported mode) ──
    if source_type != "heygen":
        logger.info("source_type '%s' requested, forcing to heygen", source_type)
        source_type = "heygen"

    avatar = _load_user_avatar(user_id, avatar_id=script.get("_avatar_id", ""))
    if not avatar.heygen_avatar_id:
        raise RuntimeError(
            "Avatar HeyGen nao configurado. "
            "Use setup_avatar para criar seu avatar no HeyGen primeiro."
        )

    cost_total = 0

    try:
        # ── Step 1: Build scenes from script ──
        _update_status("generating_scenes")
        _update_chat_step("generating_scenes")

        heygen_scenes = _build_heygen_scenes(script)
        if not heygen_scenes:
            raise RuntimeError("Roteiro sem narracao — impossivel gerar video.")

        voice_id = avatar.heygen_voice_id or ""
        if not voice_id:
            logger.warning("No heygen_voice_id — HeyGen will use default voice")

        _notify_progress(user_id, channel,
            f"Gerando video com {len(heygen_scenes)} cenas no HeyGen...")

        # ── Step 2: Generate video via HeyGen Standard API ──
        from src.video.providers.heygen import (
            generate_video as heygen_generate_video,
            wait_for_video as heygen_wait_for_video,
            estimate_video_cost_cents,
        )

        video_id = await heygen_generate_video(
            scenes=heygen_scenes,
            talking_photo_id=avatar.heygen_avatar_id,
            voice_id=voice_id,
            title=script.get("title", ""),
        )

        _check_cancelled()

        # ── Step 3: Poll until HeyGen finishes ──
        _notify_progress(user_id, channel, "HeyGen processando video...")

        video_data = await heygen_wait_for_video(
            video_id=video_id,
            on_progress=lambda s: _notify_progress(user_id, channel, f"HeyGen: {s}..."),
            cancel_check=_check_cancelled,
        )

        heygen_video_url = video_data.get("video_url", "")
        heygen_duration = video_data.get("duration", 0)
        heygen_thumbnail = video_data.get("thumbnail_url", "")

        if not heygen_video_url:
            raise RuntimeError("HeyGen nao retornou URL do video.")

        cost_total += estimate_video_cost_cents(heygen_duration or 60)
        _check_cancelled()

        # ── Step 4: Upload to Cloudinary ──
        _update_status("uploading")
        _update_chat_step("uploading")
        _notify_progress(user_id, channel, "Fazendo upload do video...")

        video_result = cloudinary.uploader.upload(
            heygen_video_url,
            folder="teq/videos",
            public_id=f"video_{project_id}",
            resource_type="video",
            overwrite=True,
            quality="auto:best",
        )
        video_url = video_result["secure_url"]
        whatsapp_url = video_url

        thumbnail_url = ""
        if heygen_thumbnail:
            try:
                thumb_result = cloudinary.uploader.upload(
                    heygen_thumbnail,
                    folder="teq/videos",
                    public_id=f"thumb_{project_id}",
                    overwrite=True,
                )
                thumbnail_url = thumb_result["secure_url"]
            except Exception as e:
                logger.warning("Failed to upload thumbnail: %s", e)

        duration_s = int(heygen_duration) if heygen_duration else 60

        # ── Step 5: Finalize ──
        now = datetime.now(timezone.utc).isoformat()
        with get_db() as session:
            project = session.get(VideoProject, project_id)
            if project:
                project.status = "done"
                project.current_step = "done"
                project.video_url = video_url
                project.video_url_whatsapp = whatsapp_url
                project.thumbnail_url = thumbnail_url
                project.duration = duration_s
                project.cost_cents = cost_total
                project.updated_at = now

        # Update chat: GENERATING → READY
        try:
            from src.models.chat_messages import update_message_by_prefix, save_message
            ready_payload = json.dumps({
                "video_url": video_url,
                "thumbnail_url": thumbnail_url,
                "title": script.get("title", ""),
                "duration": duration_s,
                "whatsapp_url": whatsapp_url,
            })
            updated = update_message_by_prefix(
                user_id, "__VIDEO_GENERATING__", f"__VIDEO_READY__{ready_payload}",
            )
            if not updated:
                save_message(user_id, user_id, "agent", f"__VIDEO_READY__{ready_payload}")
        except Exception as e:
            logger.error("Failed to update chat to VIDEO_READY: %s", e)

        # WebSocket notification
        try:
            from src.endpoints.web import ws_manager
            await ws_manager.send_personal_message(user_id, {
                "type": "video_ready",
                "video_url": video_url,
                "thumbnail_url": thumbnail_url,
                "title": script.get("title", ""),
                "duration": duration_s,
            })
        except Exception as e:
            logger.warning("Failed to send video_ready WS: %s", e)

        _notify_progress(user_id, channel, f"Video pronto! {video_url}")
        await _deliver_video(user_id, channel, video_url, whatsapp_url)

    except Exception as e:
        logger.error("Pipeline failed for project %s: %s", project_id, e, exc_info=True)
        _update_status("failed", str(e))

        try:
            from src.models.chat_messages import update_message_by_prefix
            failed_payload = json.dumps({"error": str(e)[:200]})
            update_message_by_prefix(
                user_id, "__VIDEO_GENERATING__", f"__VIDEO_FAILED__{failed_payload}",
            )
        except Exception:
            pass

        _notify_progress(user_id, channel, f"Erro na geracao do video: {str(e)[:100]}")


# ── Helpers ──

def _clean_narration(text: str) -> str:
    """Remove stage directions and annotations that shouldn't be spoken by TTS."""
    import re
    # Remove parenthetical annotations: (pausa), (pause), (tom sério), (energia alta), etc.
    text = re.sub(r'\([^)]{0,40}\)', '', text)
    # Collapse multiple spaces left behind
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()


def _build_heygen_scenes(script: dict) -> list[dict]:
    """Extract scenes from script into HeyGen format."""
    scenes = []
    default_bg = {"type": "color", "value": "#0D1117"}

    for section_key in ("hook", "callback"):
        section = script.get(section_key, {})
        if section.get("narration"):
            scenes.append({
                "narration": _clean_narration(section["narration"]),
                "background": section.get("heygen_background", default_bg),
                "emotion": section.get("heygen_emotion", "Friendly"),
                "speed": section.get("heygen_speed", 1.0),
            })

    # Insert middle scenes between hook and callback
    middle = []
    for scene in script.get("scenes", []):
        if scene.get("narration"):
            middle.append({
                "narration": _clean_narration(scene["narration"]),
                "background": scene.get("heygen_background", {"type": "color", "value": "#1a1a2e"}),
                "emotion": scene.get("heygen_emotion", "Friendly"),
                "speed": scene.get("heygen_speed", 1.0),
            })

    if scenes:
        # hook is first, callback is last, middle goes between
        hook = [scenes[0]] if len(scenes) > 0 else []
        callback = [scenes[-1]] if len(scenes) > 1 else []
        return hook + middle + callback

    return middle


def _notify_progress(user_id: str, channel: str, message: str):
    try:
        from src.events import emit_event_sync
        emit_event_sync(user_id, "video_progress", {"message": message})
    except Exception:
        pass

    if channel in ("whatsapp", "whatsapp_text", "web_whatsapp"):
        try:
            import asyncio as _aio
            from src.integrations.whatsapp import whatsapp_client
            coro = whatsapp_client.send_text_message(user_id, message)
            try:
                loop = _aio.get_running_loop()
                loop.create_task(coro)
            except RuntimeError:
                _aio.run(coro)
        except Exception:
            pass


def _load_user_avatar(user_id: str, avatar_id: str = ""):
    from src.db.session import get_db
    from src.db.models import UserAvatar
    with get_db() as session:
        if avatar_id:
            avatar = session.get(UserAvatar, avatar_id)
            if avatar and avatar.user_id == user_id:
                session.expunge(avatar)
                return avatar
        avatar = (
            session.query(UserAvatar)
            .filter(UserAvatar.user_id == user_id, UserAvatar.is_active == True)
            .order_by(UserAvatar.created_at.desc())
            .first()
        )
        if avatar:
            session.expunge(avatar)
            return avatar
    raise RuntimeError("Nenhum avatar configurado. Use setup_avatar primeiro.")


async def _deliver_video(user_id: str, channel: str, video_url: str, whatsapp_url: str):
    if channel in ("whatsapp", "whatsapp_text", "web_whatsapp"):
        try:
            from src.integrations.whatsapp import whatsapp_client
            await whatsapp_client.send_video_message(user_id, whatsapp_url or video_url)
        except Exception as e:
            logger.warning("Failed to deliver video via WhatsApp: %s", e)

"""
Video generation pipeline orchestrator.
Coordinates all steps: script → voice → captions → assets → assemble → encode → upload.
Supports error recovery per step and progress tracking.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import cloudinary.uploader

logger = logging.getLogger(__name__)

# Pipeline steps in order
STEPS = [
    "generating_voice",
    "syncing_captions",
    "generating_avatar",
    "generating_broll",
    "assembling",
    "encoding",
    "uploading",
]


async def run_pipeline(
    user_id: str,
    project_id: str,
    script: dict,
    source_type: str,
    source_url: str,
    channel: str = "web",
    voice: str = "",
):
    """
    Run the full video generation pipeline.

    Args:
        user_id: User ID.
        project_id: VideoProject ID.
        script: Video script dict.
        source_type: "avatar" or "real".
        source_url: Photo URL (avatar) or video URL (real).
        channel: Delivery channel.
        voice: Voice name for narration.
    """
    from src.db.session import get_db
    from src.db.models import VideoProject

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

    def _save_assets(assets: dict):
        now = datetime.now(timezone.utc).isoformat()
        with get_db() as session:
            project = session.get(VideoProject, project_id)
            if project:
                project.assets_json = json.dumps(assets, ensure_ascii=False)
                project.updated_at = now

    def _finalize(video_url: str, whatsapp_url: str, thumbnail_url: str, duration: int, cost_cents: int):
        now = datetime.now(timezone.utc).isoformat()
        with get_db() as session:
            project = session.get(VideoProject, project_id)
            if project:
                project.status = "done"
                project.current_step = "done"
                project.video_url = video_url
                project.video_url_whatsapp = whatsapp_url
                project.thumbnail_url = thumbnail_url
                project.duration = duration
                project.cost_cents = cost_cents
                project.updated_at = now

    # Get script title for progress bubble
    _script_title = script.get("title", "")

    def _update_chat_step(step: str):
        """Update the __VIDEO_GENERATING__ message in chat with current step."""
        try:
            from src.models.chat_messages import update_message_by_prefix
            new_text = "__VIDEO_GENERATING__" + json.dumps({
                "current_step": step,
                "title": _script_title,
            })
            update_message_by_prefix(user_id, "__VIDEO_GENERATING__", new_text)
        except Exception:
            pass

    _notify_progress(user_id, channel, "Iniciando geracao do video...")

    assets = {}
    cost_total = 0

    try:
        # Step 1: Generate voice
        _update_status("generating_voice")
        _update_chat_step("generating_voice")
        _notify_progress(user_id, channel, "Gerando narracao...")

        narration_text = _extract_full_narration(script)

        from src.video.voice_generator import generate_voice
        audio_bytes, audio_mime, audio_duration = await generate_voice(
            text=narration_text,
            voice=voice,
            user_id=user_id,
            channel=channel,
        )

        # Upload audio to Cloudinary
        audio_result = cloudinary.uploader.upload(
            audio_bytes,
            folder="teq/video_assets",
            public_id=f"voice_{project_id}",
            resource_type="video",
            overwrite=True,
        )
        audio_url = audio_result["secure_url"]
        assets["voice_url"] = audio_url
        _save_assets(assets)

        # Step 2: Caption sync
        _update_status("syncing_captions")
        _update_chat_step("syncing_captions")
        _notify_progress(user_id, channel, "Sincronizando legendas...")

        from src.video.voice_generator import convert_to_wav
        from src.video.caption_sync import generate_captions

        wav_bytes = await convert_to_wav(audio_bytes, audio_mime)
        captions = await generate_captions(
            audio_bytes=wav_bytes,
            language="pt",
            user_id=user_id,
            channel=channel,
        )
        assets["captions"] = captions
        _save_assets(assets)

        # Step 3: Generate talking head (avatar mode) or use uploaded video
        talking_head_url = ""
        if source_type == "avatar" and source_url:
            _update_status("generating_avatar")
            _update_chat_step("generating_avatar")
            _notify_progress(user_id, channel, "Gerando avatar com lip-sync...")

            from src.video.talking_head import generate_talking_head
            talking_head_url = await generate_talking_head(
                photo_url=source_url,
                audio_url=audio_url,
                user_id=user_id,
                channel=channel,
            )
            assets["talking_head_url"] = talking_head_url
            cost_total += 80  # $0.80 in cents
        elif source_type == "real" and source_url:
            talking_head_url = source_url
            assets["talking_head_url"] = source_url

        _save_assets(assets)

        # Step 4: Generate B-roll clips
        _update_status("generating_broll")
        _update_chat_step("generating_broll")
        _notify_progress(user_id, channel, "Gerando cenas de B-roll...")

        broll_urls = {}
        broll_prompts = {}
        for scene in script.get("scenes", []):
            prompt = scene.get("broll_prompt")
            if prompt:
                broll_prompts[scene["name"]] = prompt

        if broll_prompts:
            from src.video.scene_generator import generate_broll
            for scene_name, prompt in broll_prompts.items():
                try:
                    url = await generate_broll(
                        prompt=prompt,
                        duration=5,
                        aspect_ratio="9:16",
                        user_id=user_id,
                        channel=channel,
                    )
                    broll_urls[scene_name] = url
                    cost_total += 14  # $0.14 in cents per 5s
                except Exception as e:
                    logger.warning("B-roll failed for %s: %s (continuing without)", scene_name, e)

        assets["broll_urls"] = broll_urls
        _save_assets(assets)

        # Step 5: Assemble video with Remotion
        _update_status("assembling")
        _update_chat_step("assembling")
        _notify_progress(user_id, channel, "Montando o video...")

        from src.video.assembler import assemble_video
        raw_video_path = await assemble_video(
            script=script,
            audio_url=audio_url,
            captions=captions,
            talking_head_url=talking_head_url,
            broll_urls=broll_urls,
            user_id=user_id,
            channel=channel,
        )

        # Step 6: Post-processing (encode for Instagram + WhatsApp)
        _update_status("encoding")
        _update_chat_step("encoding")
        _notify_progress(user_id, channel, "Encodando para Instagram e WhatsApp...")

        from src.video.postprocessing import encode_for_instagram, encode_for_whatsapp, extract_thumbnail

        instagram_path = await encode_for_instagram(raw_video_path)
        whatsapp_path = await encode_for_whatsapp(raw_video_path)
        thumbnail_path = await extract_thumbnail(raw_video_path)

        # Step 7: Upload to Cloudinary
        _update_status("uploading")
        _update_chat_step("uploading")
        _notify_progress(user_id, channel, "Fazendo upload...")

        video_result = cloudinary.uploader.upload(
            instagram_path,
            folder="teq/videos",
            public_id=f"video_{project_id}",
            resource_type="video",
            overwrite=True,
        )
        video_url = video_result["secure_url"]

        whatsapp_result = cloudinary.uploader.upload(
            whatsapp_path,
            folder="teq/videos",
            public_id=f"video_{project_id}_wa",
            resource_type="video",
            overwrite=True,
        )
        whatsapp_url = whatsapp_result["secure_url"]

        thumb_result = cloudinary.uploader.upload(
            thumbnail_path,
            folder="teq/videos",
            public_id=f"thumb_{project_id}",
            overwrite=True,
        )
        thumbnail_url = thumb_result["secure_url"]

        # Calculate duration from audio
        duration_s = int(audio_duration)

        # Finalize
        _finalize(video_url, whatsapp_url, thumbnail_url, duration_s, cost_total)

        # Update chat message: GENERATING → READY
        try:
            from src.models.chat_messages import update_message_by_prefix
            ready_payload = json.dumps({
                "video_url": video_url,
                "thumbnail_url": thumbnail_url,
                "title": _script_title,
                "duration": duration_s,
                "whatsapp_url": whatsapp_url,
            })
            updated = update_message_by_prefix(user_id, "__VIDEO_GENERATING__", f"__VIDEO_READY__{ready_payload}")
            if not updated:
                from src.models.chat_messages import save_message
                save_message(user_id, user_id, "agent", f"__VIDEO_READY__{ready_payload}")
        except Exception as e:
            logger.warning("Failed to update chat message to VIDEO_READY: %s", e)

        _notify_progress(user_id, channel, f"Video pronto! {video_url}")

        # Send to user
        await _deliver_video(user_id, channel, video_url, whatsapp_url)

        # Cleanup temp files
        for path in [raw_video_path, instagram_path, whatsapp_path, thumbnail_path]:
            try:
                if path and os.path.exists(path):
                    os.unlink(path)
            except OSError:
                pass

    except Exception as e:
        logger.error("Pipeline failed at step for project %s: %s", project_id, e, exc_info=True)
        _update_status("failed", str(e))

        # Update chat message: GENERATING → FAILED
        try:
            from src.models.chat_messages import update_message_by_prefix
            failed_payload = json.dumps({"error": str(e)[:200]})
            update_message_by_prefix(user_id, "__VIDEO_GENERATING__", f"__VIDEO_FAILED__{failed_payload}")
        except Exception:
            pass

        _notify_progress(user_id, channel, f"Erro na geracao do video: {str(e)[:100]}")
        raise


def _extract_full_narration(script: dict) -> str:
    """Extract all narration text from script into a single string."""
    parts = []
    hook = script.get("hook", {})
    if hook.get("narration"):
        parts.append(hook["narration"])

    for scene in script.get("scenes", []):
        if scene.get("narration"):
            parts.append(scene["narration"])

    callback = script.get("callback", {})
    if callback.get("narration"):
        parts.append(callback["narration"])

    return " ".join(parts)


def _notify_progress(user_id: str, channel: str, message: str):
    """Send progress notification to user."""
    try:
        from src.events import emit_event_sync
        emit_event_sync(user_id, "video_progress", {"message": message})
    except Exception:
        pass

    # Also send via WhatsApp if applicable
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


async def _deliver_video(user_id: str, channel: str, video_url: str, whatsapp_url: str):
    """Deliver the finished video to the user."""
    # Emit WebSocket event
    try:
        from src.events import emit_event_sync
        emit_event_sync(user_id, "video_ready", {"video_url": video_url})
    except Exception:
        pass

    # Send via WhatsApp
    if channel in ("whatsapp", "whatsapp_text", "web_whatsapp"):
        try:
            from src.integrations.whatsapp import whatsapp_client
            await whatsapp_client.send_document(
                user_id,
                whatsapp_url,
                filename="video.mp4",
                caption="Seu video esta pronto!",
                mimetype="video/mp4",
            )
        except Exception as e:
            logger.error("Failed to send video via WhatsApp: %s", e)

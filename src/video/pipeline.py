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

# Pipeline steps in order (not all run for every source_type)
STEPS = [
    "generating_voice",
    "syncing_captions",
    "generating_avatar",     # avatar mode only
    "generating_scenes",     # ai_motion mode only
    "generating_broll",      # avatar/real mode only
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
    task_id: str = "",
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
                "task_id": task_id,
            })
            update_message_by_prefix(user_id, "__VIDEO_GENERATING__", new_text)
        except Exception:
            pass

    def _check_cancelled():
        """Raise if task was cancelled by user."""
        if task_id:
            from src.queue.task_queue import is_task_cancelled
            if is_task_cancelled(task_id):
                raise RuntimeError("cancelled by user")

    _notify_progress(user_id, channel, "Iniciando geracao do video...")

    # ── Validações e auto-correção de source_type ──
    if source_type == "heygen_seedance":
        try:
            avatar = _load_user_avatar(user_id, avatar_id=script.get("_avatar_id", ""))
            if not avatar.heygen_avatar_id:
                raise RuntimeError(
                    "Digital Twin nao configurado. "
                    "Use setup_digital_twin para criar seu avatar cinematografico."
                )
        except RuntimeError:
            raise
    elif source_type == "heygen":
        try:
            avatar = _load_user_avatar(user_id, avatar_id=script.get("_avatar_id", ""))
            if not avatar.heygen_avatar_id:
                raise RuntimeError(
                    "Avatar HeyGen nao configurado. "
                    "Use setup_avatar para criar seu avatar no HeyGen primeiro."
                )
        except RuntimeError:
            raise
    elif source_type == "avatar" and not source_url:
        # Auto-upgrade: se não tem photo_url mas tem avatar configurado, usar heygen ou ai_motion
        try:
            avatar = _load_user_avatar(user_id, avatar_id=script.get("_avatar_id", ""))
            if avatar.heygen_avatar_id and avatar.heygen_voice_id:
                logger.info("Auto-upgrading source_type from 'avatar' to 'heygen' (HeyGen avatar configured)")
                source_type = "heygen"
            else:
                logger.info("Auto-upgrading source_type from 'avatar' to 'ai_motion' (no photo_url but avatar exists)")
                source_type = "ai_motion"
        except RuntimeError:
            raise RuntimeError(
                "Modo avatar requer uma foto (photo_url). "
                "Envie uma foto ou configure um avatar com setup_avatar."
            )
    if source_type == "ai_motion":
        try:
            _load_user_avatar(user_id, avatar_id=script.get("_avatar_id", ""))
        except RuntimeError:
            raise RuntimeError(
                "Modo ai_motion requer avatar configurado. "
                "Use setup_avatar para enviar suas fotos primeiro."
            )

    assets = {}
    cost_total = 0
    audio_url = ""
    audio_duration = 0
    captions = []

    try:
        # Steps 1-2: Voice + Captions (skip for HeyGen — it handles voice internally)
        if source_type != "heygen":
            # Step 1: Generate voice
            _update_status("generating_voice")
            _update_chat_step("generating_voice")
            _notify_progress(user_id, channel, "Gerando narracao...")

            narration_text = _extract_full_narration(script)

            # Use avatar's cloned voice if available and no explicit voice was requested
            effective_voice = voice
            if not effective_voice and source_type in ("ai_motion", "avatar"):
                try:
                    _avatar = _load_user_avatar(user_id, avatar_id=script.get("_avatar_id", ""))
                    if _avatar and _avatar.voice_id:
                        effective_voice = _avatar.voice_id
                        logger.info("Using avatar cloned voice: %s", _avatar.voice_id)
                except RuntimeError:
                    pass  # No avatar = use default voice

            from src.video.voice_generator import generate_voice
            audio_bytes, audio_mime, audio_duration = await generate_voice(
                text=narration_text,
                voice=effective_voice,
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

            _check_cancelled()

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

            _check_cancelled()

        # Step 3+4: Generate visual assets (depends on source_type)
        talking_head_url = ""
        broll_urls = {}
        scene_clip_urls = {}

        if source_type == "ai_motion":
            # --- AI MOTION: Kling O1 Reference-to-Video + LipSync (camera_direct only) ---
            # Pipeline: Reference clips → optional LipSync → Remotion assembly
            avatar_id = script.get("_avatar_id", "")
            avatar = _load_user_avatar(user_id, avatar_id=avatar_id)
            ref_frames = json.loads(avatar.reference_frames or "[]")
            if not ref_frames:
                raise RuntimeError("Avatar sem frames de referencia. Configure um avatar primeiro.")

            # Build elements array for Subject Binding
            elements = [{
                "frontal_image_url": ref_frames[0],
                "reference_image_urls": ref_frames[1:4] if len(ref_frames) > 1 else [],
            }]

            # Step 3a: Split audio by scene (needed for lip-sync)
            _update_status("generating_scenes")
            _update_chat_step("generating_scenes")
            _notify_progress(user_id, channel, "Preparando audio por cena...")

            from src.video.audio_splitter import split_audio_by_scenes
            scene_audio_urls = await split_audio_by_scenes(
                audio_url=audio_url,
                script=script,
                captions=captions,
                project_id=project_id,
            )
            assets["scene_audio_urls"] = scene_audio_urls
            _save_assets(assets)

            # Step 3b: Collect scenes and generate cinematographic prompts
            all_scenes = _collect_all_scenes(script)
            if not all_scenes:
                logger.info("Script has no i2v_prompt — auto-generating from narration")
                _notify_progress(user_id, channel, "Gerando prompts cinematograficos...")
                all_scenes = _auto_generate_i2v_scenes(script)

            # Ensure prompts use @Element1 reference
            for scene in all_scenes:
                if "@Element1" not in scene.get("prompt", ""):
                    scene["prompt"] = f"@Element1 {scene['prompt']}"

            total_scenes = len(all_scenes)
            _notify_progress(user_id, channel,
                f"Gerando {total_scenes} cenas cinematograficas (Kling O1 Reference)...")

            # Step 3c: Generate reference clips via Kling O1
            from src.video.providers.kling_reference import (
                generate_multiple_reference_clips, estimate_reference_cost_cents,
            )
            scene_clip_urls = await generate_multiple_reference_clips(
                scenes=all_scenes,
                elements=elements,
                aspect_ratio="9:16",
                user_id=user_id,
                channel=channel,
            )

            for scene_name, url in scene_clip_urls.items():
                if url:
                    cost_total += estimate_reference_cost_cents(5)

            assets["scene_clip_urls"] = scene_clip_urls
            _save_assets(assets)
            _check_cancelled()

            # Step 3d: Apply lip-sync to camera_direct scenes only
            camera_direct_scenes = [
                s for s in all_scenes
                if s.get("camera_direct") and scene_clip_urls.get(s["name"])
                and scene_audio_urls.get(s["name"])
            ]

            if camera_direct_scenes:
                _notify_progress(user_id, channel,
                    f"Aplicando lip-sync em {len(camera_direct_scenes)} cena(s)...")

                from src.video.lipsync import apply_lipsync_to_video, estimate_video_lipsync_cost_cents
                for scene in camera_direct_scenes:
                    name = scene["name"]
                    try:
                        lipsync_url = await apply_lipsync_to_video(
                            video_url=scene_clip_urls[name],
                            audio_url=scene_audio_urls[name],
                        )
                        scene_clip_urls[name] = lipsync_url  # Replace with lip-synced version
                        cost_total += estimate_video_lipsync_cost_cents(scene.get("duration", 5))
                        logger.info("LipSync applied to scene '%s'", name)
                    except Exception as e:
                        logger.warning("LipSync failed for scene '%s': %s (keeping original)", name, e)

                assets["scene_clip_urls"] = scene_clip_urls
            _save_assets(assets)

        elif source_type == "avatar" and source_url:
            # --- AVATAR: D-ID talking head + generic B-roll ---
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

            _save_assets(assets)
            _check_cancelled()

            # Generate B-roll
            _update_status("generating_broll")
            _update_chat_step("generating_broll")
            _notify_progress(user_id, channel, "Gerando cenas de B-roll...")

            broll_urls = await _generate_broll_for_script(script, user_id, channel)
            cost_total += sum(14 for _ in broll_urls.values() if _)
            assets["broll_urls"] = broll_urls

        elif source_type == "heygen_seedance":
            # --- HEYGEN SEEDANCE 2.0: Cinematic scenes via Video Agent API ---
            # Each scene generates a 5-15s cinematic clip with the user as actor.
            # Clips are concatenated via FFmpeg into the final video.
            _update_status("generating_scenes")
            _update_chat_step("generating_scenes")
            _notify_progress(user_id, channel, "Preparando video cinematografico (Seedance 2.0)...")

            avatar_id_local = script.get("_avatar_id", "")
            avatar = _load_user_avatar(user_id, avatar_id=avatar_id_local)

            if not avatar.heygen_avatar_id:
                raise RuntimeError("Digital Twin nao configurado. Use setup_digital_twin primeiro.")
            if not avatar.heygen_voice_id:
                raise RuntimeError("Voz HeyGen nao configurada.")

            # Build seedance scenes from script
            seedance_scenes = []

            hook = script.get("hook", {})
            if hook.get("narration"):
                seedance_scenes.append({
                    "prompt": hook.get("heygen_scene_description", "Presenter looking at camera with energy"),
                    "narration": hook["narration"],
                    "duration_sec": min(15, max(5, hook.get("duration_s", 5))),
                })

            for scene in script.get("scenes", []):
                if scene.get("narration"):
                    seedance_scenes.append({
                        "prompt": scene.get("heygen_scene_description", "Presenter explaining with natural gestures"),
                        "narration": scene["narration"],
                        "duration_sec": min(15, max(5, scene.get("duration_s", 8))),
                    })

            callback = script.get("callback", {})
            if callback.get("narration"):
                seedance_scenes.append({
                    "prompt": callback.get("heygen_scene_description", "Presenter smiling confidently at camera"),
                    "narration": callback["narration"],
                    "duration_sec": min(15, max(5, callback.get("duration_s", 5))),
                })

            if not seedance_scenes:
                raise RuntimeError("Roteiro sem narracao — impossivel gerar video.")

            total = len(seedance_scenes)
            _notify_progress(user_id, channel,
                f"Gerando {total} cenas cinematograficas com Seedance 2.0...")

            from src.video.providers.heygen import (
                generate_seedance_multi_scene,
                wait_for_video as heygen_wait_for_video,
                estimate_seedance_cost_cents,
            )

            # Generate all clips
            scene_results = await generate_seedance_multi_scene(
                scenes=seedance_scenes,
                avatar_id=avatar.heygen_avatar_id,
                orientation="portrait",
            )

            assets["seedance_results"] = scene_results
            _save_assets(assets)
            _check_cancelled()

            # Poll each clip for completion and collect URLs
            clip_urls = []
            for i, sr in enumerate(scene_results):
                if sr.get("status") == "failed" or not sr.get("video_id"):
                    logger.warning("Seedance scene %d failed, skipping", i)
                    continue

                _notify_progress(user_id, channel,
                    f"Aguardando cena {i + 1}/{total}...")

                try:
                    clip_data = await heygen_wait_for_video(
                        video_id=sr["video_id"],
                        max_attempts=120,
                        interval_s=10,
                    )
                    clip_url = clip_data.get("video_url", "")
                    if clip_url:
                        clip_urls.append(clip_url)
                        cost_total += estimate_seedance_cost_cents(
                            seedance_scenes[i].get("duration_sec", 10)
                        )
                        logger.info("Seedance scene %d ready: %s", i, clip_url[:80])
                except Exception as e:
                    logger.error("Seedance scene %d poll failed: %s", i, e)

            if not clip_urls:
                raise RuntimeError("Nenhuma cena Seedance foi gerada com sucesso.")

            assets["seedance_clip_urls"] = clip_urls
            _save_assets(assets)
            _check_cancelled()

            # Concatenate clips via FFmpeg
            _update_status("assembling")
            _update_chat_step("assembling")
            _notify_progress(user_id, channel,
                f"Montando {len(clip_urls)} clips em um video...")

            import tempfile
            import subprocess
            import httpx as _httpx

            temp_dir = tempfile.mkdtemp(prefix="seedance_")
            clip_paths = []

            # Download all clips
            async with _httpx.AsyncClient(timeout=120) as dl_client:
                for idx, url in enumerate(clip_urls):
                    resp = await dl_client.get(url)
                    resp.raise_for_status()
                    clip_path = os.path.join(temp_dir, f"clip_{idx:03d}.mp4")
                    with open(clip_path, "wb") as f:
                        f.write(resp.content)
                    clip_paths.append(clip_path)

            # Create FFmpeg concat list
            concat_list_path = os.path.join(temp_dir, "concat.txt")
            with open(concat_list_path, "w") as f:
                for cp in clip_paths:
                    f.write(f"file '{cp}'\n")

            output_path = os.path.join(temp_dir, "final.mp4")
            ffmpeg_cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", concat_list_path,
                "-c", "copy",
                "-movflags", "+faststart",
                output_path,
            ]
            proc = subprocess.run(ffmpeg_cmd, capture_output=True, timeout=120)
            if proc.returncode != 0:
                logger.error("FFmpeg concat failed: %s", proc.stderr.decode()[:500])
                raise RuntimeError("Falha ao concatenar clips do Seedance.")

            # Upload final video to Cloudinary
            _update_status("uploading")
            _update_chat_step("uploading")
            _notify_progress(user_id, channel, "Fazendo upload do video...")

            video_result = cloudinary.uploader.upload(
                output_path,
                folder="teq/videos",
                public_id=f"video_{project_id}",
                resource_type="video",
                overwrite=True,
            )
            video_url = video_result["secure_url"]
            whatsapp_url = video_url

            # Get duration from FFmpeg
            probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "default=noprint_wrappers=1:nokey=1", output_path]
            try:
                probe_result = subprocess.run(probe_cmd, capture_output=True, timeout=10)
                duration_s = int(float(probe_result.stdout.decode().strip()))
            except Exception:
                duration_s = sum(s.get("duration_sec", 10) for s in seedance_scenes)

            thumbnail_url = ""

            # Cleanup temp files
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

            # Finalize
            _finalize(video_url, whatsapp_url, thumbnail_url, duration_s, cost_total)

            try:
                from src.models.chat_messages import update_message_by_prefix, save_message
                ready_payload = json.dumps({
                    "video_url": video_url,
                    "thumbnail_url": thumbnail_url,
                    "title": script.get("title", ""),
                    "duration": duration_s,
                    "whatsapp_url": whatsapp_url,
                })
                updated = update_message_by_prefix(user_id, "__VIDEO_GENERATING__", f"__VIDEO_READY__{ready_payload}")
                if not updated:
                    save_message(user_id, user_id, "agent", f"__VIDEO_READY__{ready_payload}")
            except Exception as e:
                logger.error("Failed to update chat to VIDEO_READY: %s", e)

            _notify_progress(user_id, channel, f"Video cinematografico pronto! {video_url}")
            await _deliver_video(user_id, channel, video_url, whatsapp_url)
            return  # Seedance flow complete

        elif source_type == "heygen":
            # --- HEYGEN: Full video generation via HeyGen API ---
            # HeyGen handles: avatar + voice + backgrounds + transitions in one call.
            # No Remotion, no Whisper, no audio splitting needed.
            _update_status("generating_scenes")
            _update_chat_step("generating_scenes")
            _notify_progress(user_id, channel, "Preparando video com HeyGen...")

            avatar_id = script.get("_avatar_id", "")
            avatar = _load_user_avatar(user_id, avatar_id=avatar_id)

            if not avatar.heygen_avatar_id:
                raise RuntimeError(
                    "Avatar HeyGen nao configurado. "
                    "Use setup_avatar para criar seu avatar no HeyGen primeiro."
                )
            if not avatar.heygen_voice_id:
                raise RuntimeError(
                    "Voz HeyGen nao configurada. "
                    "Configure sua voz no setup do avatar."
                )

            # Build scenes from script
            heygen_scenes = []

            # Hook
            hook = script.get("hook", {})
            if hook.get("narration"):
                heygen_scenes.append({
                    "narration": hook["narration"],
                    "background": hook.get("heygen_background", {"type": "color", "value": "#0D1117"}),
                    "emotion": hook.get("heygen_emotion", "Excited"),
                    "speed": hook.get("heygen_speed", 1.1),
                })

            # Scenes
            for scene in script.get("scenes", []):
                if scene.get("narration"):
                    heygen_scenes.append({
                        "narration": scene["narration"],
                        "background": scene.get("heygen_background", {"type": "color", "value": "#1a1a2e"}),
                        "emotion": scene.get("heygen_emotion", "Friendly"),
                        "speed": scene.get("heygen_speed", 1.0),
                    })

            # Callback
            callback = script.get("callback", {})
            if callback.get("narration"):
                heygen_scenes.append({
                    "narration": callback["narration"],
                    "background": callback.get("heygen_background", {"type": "color", "value": "#0D1117"}),
                    "emotion": callback.get("heygen_emotion", "Soothing"),
                    "speed": callback.get("heygen_speed", 0.95),
                })

            if not heygen_scenes:
                raise RuntimeError("Roteiro sem narracao — impossivel gerar video.")

            _notify_progress(user_id, channel,
                f"Gerando video com {len(heygen_scenes)} cenas no HeyGen...")

            from src.video.providers.heygen import (
                generate_video as heygen_generate_video,
                wait_for_video as heygen_wait_for_video,
                estimate_video_cost_cents,
            )

            video_id = await heygen_generate_video(
                scenes=heygen_scenes,
                talking_photo_id=avatar.heygen_avatar_id,
                voice_id=avatar.heygen_voice_id,
                title=script.get("title", ""),
                width=1080,
                height=1920,
            )

            assets["heygen_video_id"] = video_id
            _save_assets(assets)
            _check_cancelled()

            # Poll for completion
            _notify_progress(user_id, channel, "HeyGen processando video... aguarde.")

            def _on_heygen_progress(status):
                _notify_progress(user_id, channel, f"HeyGen: {status}...")

            video_data = await heygen_wait_for_video(
                video_id=video_id,
                on_progress=_on_heygen_progress,
            )

            heygen_video_url = video_data.get("video_url", "")
            heygen_duration = video_data.get("duration", 0)
            heygen_thumbnail = video_data.get("thumbnail_url", "")

            if not heygen_video_url:
                raise RuntimeError("HeyGen nao retornou URL do video.")

            cost_total += estimate_video_cost_cents(heygen_duration or 60)

            # Upload HeyGen video to Cloudinary for permanent storage
            _update_status("uploading")
            _update_chat_step("uploading")
            _notify_progress(user_id, channel, "Fazendo upload do video...")

            video_result = cloudinary.uploader.upload(
                heygen_video_url,
                folder="teq/videos",
                public_id=f"video_{project_id}",
                resource_type="video",
                overwrite=True,
            )
            video_url = video_result["secure_url"]

            # WhatsApp version (same video, HeyGen already optimized)
            whatsapp_url = video_url

            # Thumbnail
            thumbnail_url = heygen_thumbnail or ""
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
                    logger.warning("Failed to upload HeyGen thumbnail: %s", e)

            duration_s = int(heygen_duration) if heygen_duration else 60

            # Finalize
            _finalize(video_url, whatsapp_url, thumbnail_url, duration_s, cost_total)

            # Update chat message: GENERATING → READY
            try:
                from src.models.chat_messages import update_message_by_prefix, save_message
                ready_payload = json.dumps({
                    "video_url": video_url,
                    "thumbnail_url": thumbnail_url,
                    "title": script.get("title", ""),
                    "duration": duration_s,
                    "whatsapp_url": whatsapp_url,
                })
                updated = update_message_by_prefix(user_id, "__VIDEO_GENERATING__", f"__VIDEO_READY__{ready_payload}")
                if not updated:
                    save_message(user_id, user_id, "agent", f"__VIDEO_READY__{ready_payload}")
            except Exception as e:
                logger.error("Failed to update chat to VIDEO_READY: %s", e)

            _notify_progress(user_id, channel, f"Video pronto! {video_url}")
            await _deliver_video(user_id, channel, video_url, whatsapp_url)
            return  # HeyGen flow complete — skip Remotion/FFmpeg steps below

        elif source_type == "real" and source_url:
            # --- REAL: User-uploaded video + generic B-roll ---
            talking_head_url = source_url
            assets["talking_head_url"] = source_url

            _update_status("generating_broll")
            _update_chat_step("generating_broll")
            _notify_progress(user_id, channel, "Gerando cenas de B-roll...")

            broll_urls = await _generate_broll_for_script(script, user_id, channel)
            cost_total += sum(14 for _ in broll_urls.values() if _)
            assets["broll_urls"] = broll_urls

        _save_assets(assets)

        # ── Validação C1: pelo menos 1 asset visual deve existir ──
        has_visual = (
            bool(talking_head_url)
            or any(v for v in scene_clip_urls.values() if v)
            or any(v for v in broll_urls.values() if v)
        )
        if not has_visual and source_type != "real":
            raise RuntimeError(
                "Nenhum asset visual foi gerado — o video ficaria sem imagem. "
                "Verifique: foto do avatar (modo avatar) ou avatar configurado (modo ai_motion)."
            )

        _check_cancelled()

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
            scene_clip_urls=scene_clip_urls,
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
            from src.models.chat_messages import update_message_by_prefix, save_message
            ready_payload = json.dumps({
                "video_url": video_url,
                "thumbnail_url": thumbnail_url,
                "title": _script_title,
                "duration": duration_s,
                "whatsapp_url": whatsapp_url,
            })
            logger.info("Updating chat message to VIDEO_READY for user %s (video: %s)", user_id, video_url)
            updated = update_message_by_prefix(user_id, "__VIDEO_GENERATING__", f"__VIDEO_READY__{ready_payload}")
            if not updated:
                logger.warning("No __VIDEO_GENERATING__ message found to update — creating new VIDEO_READY message")
                save_message(user_id, user_id, "agent", f"__VIDEO_READY__{ready_payload}")
        except Exception as e:
            logger.error("Failed to update chat message to VIDEO_READY: %s", e)
            # Last resort: try to save a new message
            try:
                from src.models.chat_messages import save_message as _save
                _save(user_id, user_id, "agent", f"__VIDEO_READY__{ready_payload}")
            except Exception:
                logger.error("CRITICAL: Could not deliver video to chat for user %s", user_id)

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


def _auto_generate_i2v_scenes(script: dict) -> list[dict]:
    """
    Auto-generate cinematographic i2v_prompt for each scene using AI.
    Uses the script's narration, title, and scene context to create
    detailed Kling I2V prompts (person + action + scenario + lighting + camera).
    """
    person_desc = script.get("person_description", "")
    title = script.get("title", "")

    # Collect all scenes with their narration
    raw_scenes = []
    hook = script.get("hook", {})
    if hook.get("narration"):
        raw_scenes.append({
            "name": "hook",
            "narration": hook["narration"],
            "on_screen_text": hook.get("on_screen_text", ""),
            "broll_prompt": hook.get("broll_prompt", ""),
            "duration_s": hook.get("duration_s", 5),
        })

    for i, scene in enumerate(script.get("scenes", [])):
        if scene.get("narration"):
            raw_scenes.append({
                "name": scene.get("name", f"scene_{i}"),
                "narration": scene["narration"],
                "on_screen_text": scene.get("on_screen_text", ""),
                "broll_prompt": scene.get("broll_prompt", ""),
                "duration_s": scene.get("duration_s", 5),
            })

    callback = script.get("callback", {})
    if callback.get("narration"):
        raw_scenes.append({
            "name": "callback",
            "narration": callback["narration"],
            "on_screen_text": callback.get("on_screen_text", ""),
            "broll_prompt": callback.get("broll_prompt", ""),
            "duration_s": callback.get("duration_s", 5),
        })

    if not raw_scenes:
        return []

    # Use AI to generate cinematographic prompts from narration context
    try:
        prompts = _generate_cinematographic_prompts(raw_scenes, person_desc, title)
    except Exception as e:
        logger.warning("AI prompt generation failed, using enhanced fallback: %s", e)
        prompts = _fallback_prompts(raw_scenes, person_desc)

    scenes = []
    for i, raw in enumerate(raw_scenes):
        scenes.append({
            "name": raw["name"],
            "prompt": prompts.get(raw["name"], prompts.get(f"scene_{i}", "")),
            "broll_prompt": raw.get("broll_prompt", ""),
            "duration": max(5, raw["duration_s"]),
            "camera_control": None,
        })

    logger.info("Auto-generated %d cinematographic i2v scenes", len(scenes))
    return scenes


def _generate_cinematographic_prompts(
    scenes: list[dict], person_desc: str, title: str,
) -> dict[str, str]:
    """Use Gemini to generate cinematographic Kling I2V prompts from script scenes."""
    import json as _json

    scenes_text = ""
    for s in scenes:
        scenes_text += f"- Scene '{s['name']}': narration=\"{s['narration']}\", text=\"{s['on_screen_text']}\"\n"

    prompt = f"""You are a cinematography expert creating prompts for Kling AI Image-to-Video.
Each prompt will animate a PHOTO of a person into a short video clip (5-10s).

VIDEO TOPIC: {title}
PERSON APPEARANCE: {person_desc or 'young professional man'}

SCENES TO CREATE PROMPTS FOR:
{scenes_text}

RULES FOR EACH PROMPT:
1. Format: "[person description] [specific action/gesture] [detailed setting/environment] [lighting style] [camera angle/movement]"
2. EVERY scene must have a DIFFERENT setting — office, outdoor, cafe, rooftop, studio, street, park, library, etc.
3. Actions must be DYNAMIC — walking, gesturing, leaning, writing, pointing, turning, etc. NO static standing.
4. Match the EMOTION of the narration — confident for bold claims, thoughtful for explanations, excited for reveals.
5. Lighting must VARY — golden hour, soft diffused, dramatic backlight, natural window, neon ambient, etc.
6. Camera angles must VARY — medium close-up, wide shot, tracking shot, low angle, over-shoulder, etc.
7. Each prompt must be 1-2 sentences in ENGLISH.
8. Do NOT include text, logos, UI elements, or screens with readable content.
9. Make it CINEMATIC — think movie quality, not corporate video.

EXAMPLES OF GREAT PROMPTS:
- "Young man in dark blazer walking confidently through a sunlit modern city street, gesturing while explaining, golden hour warm light casting long shadows, smooth tracking shot from side angle"
- "Professional creator leaning forward at a sleek minimalist desk, pointing at camera with conviction, soft diffused studio lighting with subtle blue accent, medium close-up"
- "Entrepreneur standing on a modern rooftop terrace overlooking cityscape at dusk, arms crossed confidently, dramatic backlit silhouette with warm ambient glow, wide cinematic shot"

Return ONLY a JSON object mapping scene name to prompt. No markdown, no explanation:
{{"hook": "prompt...", "scene_name": "prompt...", "callback": "prompt..."}}"""

    from agno.agent import Agent
    from agno.models.google import Gemini
    agent = Agent(
        model=Gemini(id="gemini-2.5-flash"),
        description="Cinematography expert. Return ONLY valid JSON.",
    )
    result = agent.run(prompt)
    raw = result.content if hasattr(result, "content") else str(result)

    # Clean markdown fences
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()
    if raw.startswith("json"):
        raw = raw[4:].strip()

    prompts = _json.loads(raw)
    logger.info("AI generated %d cinematographic prompts", len(prompts))
    return prompts


def _fallback_prompts(scenes: list[dict], person_desc: str) -> dict[str, str]:
    """Fallback: generate context-aware prompts without AI call."""
    person = person_desc or "young professional man"
    settings = [
        ("walking through a modern city street at golden hour, gesturing confidently", "warm sunlight, tracking shot"),
        ("leaning forward at a sleek desk, pointing at camera with conviction", "soft studio lighting, medium close-up"),
        ("standing by a large window in a modern office, arms crossed", "natural window light, wide shot"),
        ("sitting in a trendy cafe, explaining passionately with hand gestures", "warm ambient light, medium shot"),
        ("walking down a bright modern corridor, talking to camera", "soft diffused light, smooth tracking shot"),
        ("standing on a rooftop terrace at dusk, looking into camera", "dramatic backlight, wide cinematic shot"),
        ("seated in a creative workspace with plants, leaning back thoughtfully", "natural light with shadows, medium shot"),
        ("striding through a co-working space, making eye contact with camera", "bright modern lighting, dynamic tracking shot"),
    ]

    prompts = {}
    for i, scene in enumerate(scenes):
        setting, camera = settings[i % len(settings)]
        prompts[scene["name"]] = f"{person}, {setting}, {camera}"
    return prompts


def _load_user_avatar(user_id: str, avatar_id: str = ""):
    """Load avatar by ID, or fall back to the user's active avatar."""
    from src.db.session import get_db
    from src.db.models import UserAvatar
    with get_db() as session:
        if avatar_id:
            avatar = session.get(UserAvatar, avatar_id)
            if avatar and avatar.user_id == user_id:
                session.expunge(avatar)
                return avatar
        # Fallback: most recent active avatar
        avatar = (
            session.query(UserAvatar)
            .filter(UserAvatar.user_id == user_id, UserAvatar.is_active == True)
            .order_by(UserAvatar.created_at.desc())
            .first()
        )
        if not avatar:
            raise RuntimeError("Nenhum avatar configurado. Use setup_avatar primeiro.")
        session.expunge(avatar)
        return avatar


async def _download_image_as_base64(url: str) -> str:
    """Download image from URL and return as base64 string."""
    import base64
    import httpx
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return base64.b64encode(resp.content).decode("utf-8")


def _collect_all_scenes(script: dict) -> list[dict]:
    """Extract hook + scenes + callback as a unified list with i2v_prompt and camera_direct."""
    scenes = []

    # Hook
    hook = script.get("hook", {})
    if hook.get("i2v_prompt"):
        scenes.append({
            "name": "hook",
            "prompt": hook["i2v_prompt"],
            "broll_prompt": hook.get("broll_prompt", ""),
            "duration": hook.get("duration_s", 3),
            "camera_direct": hook.get("camera_direct", True),  # hook usually faces camera
        })

    # Scenes
    for scene in script.get("scenes", []):
        if scene.get("i2v_prompt"):
            scenes.append({
                "name": scene.get("name", ""),
                "prompt": scene["i2v_prompt"],
                "broll_prompt": scene.get("broll_prompt", ""),
                "duration": scene.get("duration_s", 5),
                "camera_direct": scene.get("camera_direct", False),
            })

    # Callback
    callback = script.get("callback", {})
    if callback.get("i2v_prompt"):
        scenes.append({
            "name": "callback",
            "prompt": callback["i2v_prompt"],
            "broll_prompt": callback.get("broll_prompt", ""),
            "duration": callback.get("duration_s", 5),
            "camera_direct": callback.get("camera_direct", True),  # callback usually faces camera
        })

    return scenes


def _camera_hint_to_control(hint: str) -> dict | None:
    """Convert script camera_hint to Kling camera_control payload."""
    if not hint or hint == "static":
        return None
    CAMERA_MAP = {
        "zoom_in": {"type": "simple", "config": {"horizontal": 0, "vertical": 0, "pan": 0, "tilt": 0, "roll": 0, "zoom": 5}},
        "pan_right": {"type": "simple", "config": {"horizontal": 5, "vertical": 0, "pan": 0, "tilt": 0, "roll": 0, "zoom": 0}},
        "pan_left": {"type": "simple", "config": {"horizontal": -5, "vertical": 0, "pan": 0, "tilt": 0, "roll": 0, "zoom": 0}},
        "tilt_up": {"type": "simple", "config": {"horizontal": 0, "vertical": 0, "pan": 0, "tilt": 5, "roll": 0, "zoom": 0}},
        "dolly_forward": {"type": "forward_up"},
    }
    return CAMERA_MAP.get(hint)


async def _generate_broll_for_script(
    script: dict, user_id: str, channel: str,
) -> dict[str, str]:
    """Generate B-roll clips for all scenes that have broll_prompt."""
    broll_urls = {}
    from src.video.scene_generator import generate_broll
    for scene in script.get("scenes", []):
        prompt = scene.get("broll_prompt")
        if prompt:
            try:
                url = await generate_broll(
                    prompt=prompt,
                    duration=5,
                    aspect_ratio="9:16",
                    user_id=user_id,
                    channel=channel,
                )
                broll_urls[scene["name"]] = url
            except Exception as e:
                logger.warning("B-roll failed for %s: %s (continuing without)", scene.get("name"), e)
    return broll_urls


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

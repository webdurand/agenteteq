"""
REST API endpoints for video creation.
"""

import logging
import os
import secrets
import uuid
from datetime import datetime, timezone

import cloudinary.uploader
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from typing import Optional

from src.auth.deps import get_current_user, require_admin
from src.db.session import get_db
from src.db.models import UserAvatar, VideoProject, VideoScript

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/video", tags=["video"])


# --- List user videos ---
@router.get("/")
async def api_list_videos(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="Filter by status: done, failed, etc."),
    user=Depends(get_current_user),
):
    user_id = user["phone_number"]
    with get_db() as session:
        query = session.query(VideoProject).filter(
            VideoProject.user_id == user_id,
        )
        if status:
            query = query.filter(VideoProject.status == status)
        total = query.count()
        videos = (
            query.order_by(VideoProject.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        results = [v.to_dict() for v in videos]

    return {"videos": results, "total": total}


# --- Cancel video generation ---
@router.post("/{task_id}/cancel")
async def cancel_video_generation(task_id: str, user: dict = Depends(get_current_user)):
    user_id = user.get("phone_number")
    if not user_id:
        raise HTTPException(status_code=400, detail="Usuário sem identificador")

    from src.queue.task_queue import cancel_task_by_video
    cancelled_id = cancel_task_by_video(user_id, task_id)

    if not cancelled_id:
        raise HTTPException(status_code=400, detail="Task não encontrada ou não está em geração")

    # Also mark any active VideoProject as failed
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with get_db() as session:
            stuck = (
                session.query(VideoProject)
                .filter(
                    VideoProject.user_id == user_id,
                    VideoProject.status.notin_(["done", "failed"]),
                )
                .all()
            )
            for vp in stuck:
                vp.status = "failed"
                vp.current_step = "failed"
                vp.error_message = "Cancelado pelo usuario"
                vp.updated_at = now
    except Exception:
        pass

    # Update chat message placeholder to FAILED
    try:
        import asyncio
        from src.models.chat_messages import update_message_by_prefix
        await asyncio.to_thread(
            update_message_by_prefix, user_id,
            "__VIDEO_GENERATING__",
            "__VIDEO_FAILED__" + '{"error": "Cancelado pelo usuário"}',
        )
    except Exception:
        pass

    # Send WS event for instant frontend feedback
    try:
        from src.endpoints.web import ws_manager
        await ws_manager.send_personal_message(user_id, {
            "type": "video_failed",
            "task_id": task_id,
            "cancelled": True,
        })
    except Exception:
        pass

    return {"status": "cancelled"}


# --- Delete video ---
@router.delete("/{video_id}")
async def api_delete_video(
    video_id: str,
    user=Depends(get_current_user),
):
    user_id = user["phone_number"]
    with get_db() as session:
        video = session.get(VideoProject, video_id)
        if not video or video.user_id != user_id:
            raise HTTPException(status_code=404, detail="Video not found")
        session.delete(video)
    return {"status": "deleted"}


# --- Share link ---
@router.get("/{video_id}/share")
async def api_share_video(
    video_id: str,
    user=Depends(get_current_user),
):
    user_id = user["phone_number"]
    with get_db() as session:
        video = session.get(VideoProject, video_id)
        if not video or video.user_id != user_id:
            raise HTTPException(status_code=404, detail="Video not found")
        if not video.video_url:
            raise HTTPException(status_code=400, detail="Video not ready yet")

        # Generate share token if not exists
        if not video.share_token:
            video.share_token = secrets.token_urlsafe(32)
            video.updated_at = datetime.now(timezone.utc).isoformat()

        return {
            "video_url": video.video_url,
            "share_token": video.share_token,
            "thumbnail_url": video.thumbnail_url,
        }


# --- Upload video (for "real" mode) ---
@router.post("/upload")
async def api_upload_video(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    user_id = user["phone_number"]

    # Validate file type
    allowed_types = {"video/mp4", "video/quicktime", "video/webm", "video/x-matroska"}
    if file.content_type and file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Tipo de arquivo nao suportado: {file.content_type}. Aceitos: MP4, MOV, WebM.",
        )

    # Read file in chunks (max 500MB) to avoid loading everything into RAM at once
    max_size = 500 * 1024 * 1024
    import tempfile, shutil
    tmp = tempfile.SpooledTemporaryFile(max_size=10 * 1024 * 1024)  # spool to disk after 10MB
    total_size = 0
    while chunk := await file.read(1024 * 1024):
        total_size += len(chunk)
        if total_size > max_size:
            tmp.close()
            raise HTTPException(status_code=400, detail="Arquivo muito grande. Maximo: 500MB.")
        tmp.write(chunk)
    tmp.seek(0)
    contents = tmp.read()
    tmp.close()

    # Upload to Cloudinary
    try:
        result = cloudinary.uploader.upload(
            contents,
            folder="teq/video_uploads",
            public_id=f"upload_{user_id}_{uuid.uuid4().hex[:8]}",
            resource_type="video",
            overwrite=True,
        )
        video_url = result["secure_url"]
        duration = result.get("duration", 0)
    except Exception as e:
        logger.error("Cloudinary upload failed: %s", e)
        raise HTTPException(status_code=500, detail="Erro ao fazer upload do video.")

    return {
        "url": video_url,
        "duration": duration,
        "size_mb": round(len(contents) / 1024 / 1024, 1),
        "filename": file.filename,
    }


# --- Avatar management (AI Motion reference media) ---


@router.post("/avatar")
async def api_create_avatar(
    files: list[UploadFile] = File(...),
    label: Optional[str] = Query(None),
    user=Depends(get_current_user),
):
    """Upload 1-4 photos or 1 video as the user's avatar for AI Motion videos."""
    import json

    user_id = user["phone_number"]

    if not files:
        raise HTTPException(status_code=400, detail="Envie pelo menos 1 arquivo.")
    if len(files) > 4:
        raise HTTPException(status_code=400, detail="Máximo 4 arquivos.")

    # Detect media type from first file
    first_type = files[0].content_type or ""
    is_video = first_type.startswith("video/")

    if is_video and len(files) > 1:
        raise HTTPException(status_code=400, detail="Para vídeo, envie apenas 1 arquivo.")

    media_urls = []
    reference_frames = []

    if is_video:
        # Upload video to Cloudinary
        contents = await files[0].read()
        max_size = 500 * 1024 * 1024
        if len(contents) > max_size:
            raise HTTPException(status_code=400, detail="Vídeo muito grande. Máximo: 500MB.")

        try:
            result = cloudinary.uploader.upload(
                contents,
                folder=f"teq/avatars/{user_id}",
                public_id=f"avatar_video_{uuid.uuid4().hex[:8]}",
                resource_type="video",
                overwrite=True,
            )
            media_urls = [result["secure_url"]]
        except Exception as e:
            logger.error("Avatar video upload failed: %s", e)
            raise HTTPException(status_code=500, detail="Erro ao fazer upload do vídeo.")

        # Extract key frames
        from src.video.frame_extractor import extract_key_frames
        try:
            reference_frames = await extract_key_frames(
                video_url=media_urls[0],
                user_id=user_id,
                num_frames=4,
            )
        except Exception as e:
            logger.error("Frame extraction failed: %s", e)
            raise HTTPException(status_code=500, detail="Erro ao extrair frames do vídeo.")

        media_type = "video"
    else:
        # Upload photos
        allowed_image_types = {"image/jpeg", "image/png", "image/webp"}
        for f in files:
            if f.content_type and f.content_type not in allowed_image_types:
                raise HTTPException(
                    status_code=400,
                    detail=f"Tipo não suportado: {f.content_type}. Aceitos: JPEG, PNG, WebP.",
                )
            contents = await f.read()
            if len(contents) > 20 * 1024 * 1024:
                raise HTTPException(status_code=400, detail="Foto muito grande. Máximo: 20MB.")

            try:
                result = cloudinary.uploader.upload(
                    contents,
                    folder=f"teq/avatars/{user_id}",
                    public_id=f"avatar_photo_{uuid.uuid4().hex[:8]}",
                    overwrite=True,
                )
                media_urls.append(result["secure_url"])
            except Exception as e:
                logger.error("Avatar photo upload failed: %s", e)
                raise HTTPException(status_code=500, detail="Erro ao fazer upload da foto.")

        reference_frames = media_urls[:]
        media_type = "photo"

    # Deactivate previous avatar
    with get_db() as session:
        session.query(UserAvatar).filter(
            UserAvatar.user_id == user_id,
            UserAvatar.is_active == True,
        ).update({"is_active": False})

        # Create new avatar
        avatar = UserAvatar(
            user_id=user_id,
            media_type=media_type,
            media_urls=json.dumps(media_urls),
            reference_frames=json.dumps(reference_frames),
            is_active=True,
            label=label,
        )
        session.add(avatar)
        session.flush()
        result = avatar.to_dict()

    return result


@router.get("/avatar")
async def api_get_avatar(user=Depends(get_current_user)):
    """Get the user's active avatar."""
    user_id = user["phone_number"]
    with get_db() as session:
        avatar = (
            session.query(UserAvatar)
            .filter(UserAvatar.user_id == user_id, UserAvatar.is_active == True)
            .order_by(UserAvatar.created_at.desc())
            .first()
        )
        if not avatar:
            raise HTTPException(status_code=404, detail="Nenhum avatar configurado.")
        return avatar.to_dict()


@router.delete("/avatar")
async def api_delete_avatar(user=Depends(get_current_user)):
    """Deactivate the user's active avatar."""
    user_id = user["phone_number"]
    with get_db() as session:
        updated = session.query(UserAvatar).filter(
            UserAvatar.user_id == user_id,
            UserAvatar.is_active == True,
        ).update({"is_active": False})
        if not updated:
            raise HTTPException(status_code=404, detail="Nenhum avatar ativo.")
    return {"status": "deleted"}


@router.delete("/avatar/{avatar_id}")
async def api_delete_avatar_by_id(avatar_id: str, user=Depends(get_current_user)):
    """Permanently delete a specific avatar."""
    user_id = user["phone_number"]
    with get_db() as session:
        avatar = session.get(UserAvatar, avatar_id)
        if not avatar or avatar.user_id != user_id:
            raise HTTPException(status_code=404, detail="Avatar não encontrado.")
        # Delete cloned voice from ElevenLabs if exists
        if avatar.voice_id:
            try:
                from src.video.voice_cloner import delete_cloned_voice
                await delete_cloned_voice(avatar.voice_id)
            except Exception as e:
                logger.warning("Failed to delete voice from ElevenLabs: %s", e)
        session.delete(avatar)
    return {"status": "deleted"}


# --- Avatar management (extended) ---

@router.get("/avatars")
async def api_list_avatars(user=Depends(get_current_user)):
    """List ALL avatars (active and inactive) for the avatar management screen."""
    user_id = user["phone_number"]
    with get_db() as session:
        avatars = (
            session.query(UserAvatar)
            .filter(UserAvatar.user_id == user_id)
            .order_by(UserAvatar.created_at.desc())
            .all()
        )
        return {"avatars": [a.to_dict() for a in avatars]}


@router.put("/avatar/{avatar_id}/activate")
async def api_activate_avatar(avatar_id: str, user=Depends(get_current_user)):
    """Activate a specific avatar (deactivates all others)."""
    user_id = user["phone_number"]
    with get_db() as session:
        avatar = session.get(UserAvatar, avatar_id)
        if not avatar or avatar.user_id != user_id:
            raise HTTPException(status_code=404, detail="Avatar não encontrado.")
        # Deactivate all others
        session.query(UserAvatar).filter(
            UserAvatar.user_id == user_id,
            UserAvatar.is_active == True,
        ).update({"is_active": False})
        # Activate this one
        avatar.is_active = True
        return avatar.to_dict()


@router.post("/avatar/{avatar_id}/voice")
async def api_add_voice_to_avatar(
    avatar_id: str,
    files: list[UploadFile] = File(...),
    voice_name: Optional[str] = Query("Minha voz"),
    user=Depends(get_current_user),
):
    """Upload 1-25 audio samples to clone voice and attach to an existing avatar.
    More samples = better quality. Each should be 30s-5min of clean speech."""
    import json
    user_id = user["phone_number"]

    with get_db() as session:
        avatar = session.get(UserAvatar, avatar_id)
        if not avatar or avatar.user_id != user_id:
            raise HTTPException(status_code=404, detail="Avatar não encontrado.")

    if not files:
        raise HTTPException(status_code=400, detail="Envie pelo menos 1 arquivo de áudio.")
    if len(files) > 25:
        raise HTTPException(status_code=400, detail="Máximo de 25 amostras de áudio.")

    # Read and validate all audio samples
    audio_samples = []
    max_size = 50 * 1024 * 1024  # 50MB per file
    for f in files:
        contents = await f.read()
        if len(contents) > max_size:
            raise HTTPException(status_code=400, detail=f"Áudio '{f.filename}' muito grande. Máximo: 50MB.")
        if len(contents) < 1000:
            raise HTTPException(status_code=400, detail=f"Áudio '{f.filename}' muito pequeno. Mínimo: 30s de fala.")
        audio_samples.append(contents)

    # Upload first sample to Cloudinary (for reference)
    voice_sample_url = ""
    try:
        audio_result = cloudinary.uploader.upload(
            audio_samples[0],
            folder=f"teq/avatars/{user_id}",
            public_id=f"voice_sample_{uuid.uuid4().hex[:8]}",
            resource_type="video",
            overwrite=True,
        )
        voice_sample_url = audio_result["secure_url"]
    except Exception as e:
        logger.error("Voice sample upload failed: %s", e)
        raise HTTPException(status_code=500, detail="Erro ao fazer upload do áudio.")

    # Clone voice via ElevenLabs (all samples)
    try:
        from src.video.voice_cloner import clone_voice
        result = await clone_voice(
            audio_samples=audio_samples,
            voice_name=voice_name or "Minha voz",
            user_id=user_id,
        )
    except Exception as e:
        logger.error("Voice cloning failed: %s", e)
        raise HTTPException(status_code=500, detail="Erro ao clonar voz. Tente novamente.")

    # Save to avatar
    with get_db() as session:
        av = session.get(UserAvatar, avatar_id)
        if av:
            av.voice_id = result["voice_id"]
            av.voice_name = result["voice_name"]
            av.voice_sample_url = voice_sample_url
            session.flush()
            return av.to_dict()

    raise HTTPException(status_code=500, detail="Erro ao salvar voz no avatar.")


@router.delete("/avatar/{avatar_id}/voice")
async def api_remove_voice_from_avatar(
    avatar_id: str,
    user=Depends(get_current_user),
):
    """Remove cloned voice from an avatar."""
    user_id = user["phone_number"]
    with get_db() as session:
        avatar = session.get(UserAvatar, avatar_id)
        if not avatar or avatar.user_id != user_id:
            raise HTTPException(status_code=404, detail="Avatar não encontrado.")

        # Delete from ElevenLabs
        if avatar.voice_id:
            try:
                from src.video.voice_cloner import delete_cloned_voice
                import asyncio
                await delete_cloned_voice(avatar.voice_id)
            except Exception as e:
                logger.warning("Failed to delete voice from ElevenLabs: %s", e)

        avatar.voice_id = None
        avatar.voice_name = None
        avatar.voice_sample_url = None
        return avatar.to_dict()


# --- Audio transcription (for voice-to-text in chat) ---

@router.post("/audio/transcribe")
async def api_transcribe_audio(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    """Transcribe audio to text using Whisper."""
    import httpx as _httpx

    user_id = user["phone_number"]
    contents = await file.read()

    max_size = 25 * 1024 * 1024  # 25MB (Whisper limit)
    if len(contents) > max_size:
        raise HTTPException(status_code=400, detail="Áudio muito grande. Máximo: 25MB.")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY não configurada.")

    try:
        async with _httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (file.filename or "audio.webm", contents, file.content_type or "audio/webm")},
                data={"model": "whisper-1", "language": "pt"},
            )
            resp.raise_for_status()
            result = resp.json()
            text = result.get("text", "").strip()
    except Exception as e:
        logger.error("Transcription failed: %s", e)
        raise HTTPException(status_code=500, detail="Erro na transcrição. Tente novamente.")

    if not text:
        raise HTTPException(status_code=400, detail="Não consegui entender o áudio. Tente novamente.")

    logger.info("Transcribed %d chars for user %s", len(text), user_id)
    return {"text": text}


# --- Queue status ---

@router.get("/queue/active")
async def api_queue_active(user=Depends(get_current_user)):
    """List active (pending/processing) tasks for the user, with video progress."""
    from src.db.models import BackgroundTask, VideoProject
    user_id = user["phone_number"]
    with get_db() as session:
        tasks = (
            session.query(BackgroundTask)
            .filter(
                BackgroundTask.user_id == user_id,
                BackgroundTask.status.in_(["pending", "processing"]),
            )
            .order_by(BackgroundTask.created_at.desc())
            .all()
        )
        result = []
        for t in tasks:
            task_data = {
                "task_id": str(t.id),
                "type": t.task_type,
                "status": t.status,
                "created_at": t.created_at,
            }
            # Enrich video tasks with current_step from VideoProject
            if t.task_type == "video":
                project = (
                    session.query(VideoProject)
                    .filter(
                        VideoProject.user_id == user_id,
                        VideoProject.status.notin_(["done", "failed"]),
                    )
                    .order_by(VideoProject.created_at.desc())
                    .first()
                )
                if project:
                    task_data["current_step"] = project.current_step
                    task_data["title"] = project.script_id[:8] if project.script_id else ""

            result.append(task_data)
        return {"tasks": result}


@router.get("/queue/history")
async def api_queue_history(
    limit: int = Query(20, ge=1, le=50),
    user=Depends(get_current_user),
):
    """Recent task history (done/failed/cancelled)."""
    from src.db.models import BackgroundTask
    user_id = user["phone_number"]
    with get_db() as session:
        tasks = (
            session.query(BackgroundTask)
            .filter(BackgroundTask.user_id == user_id)
            .order_by(BackgroundTask.created_at.desc())
            .limit(limit)
            .all()
        )
        return {"tasks": [
            {
                "task_id": str(t.id),
                "type": t.task_type,
                "status": t.status,
                "created_at": t.created_at,
                "completed_at": getattr(t, "completed_at", None),
            }
            for t in tasks
        ]}


@router.post("/admin/cleanup-stuck")
async def api_cleanup_stuck(user=Depends(require_admin)):
    """Mark all stuck VideoProjects as failed and clear stuck chat messages."""
    from datetime import datetime, timezone
    user_id = user["phone_number"]
    now = datetime.now(timezone.utc).isoformat()

    cleaned = 0
    with get_db() as session:
        stuck = (
            session.query(VideoProject)
            .filter(
                VideoProject.user_id == user_id,
                VideoProject.status.notin_(["done", "failed"]),
            )
            .all()
        )
        for vp in stuck:
            vp.status = "failed"
            vp.current_step = "failed"
            vp.error_message = "cleaned up by admin"
            vp.updated_at = now
            cleaned += 1

    # Clear stuck __VIDEO_GENERATING__ chat messages
    try:
        from src.models.chat_messages import update_message_by_prefix
        import json
        error_payload = json.dumps({"error": "Processo anterior foi limpo."})
        update_message_by_prefix(user_id, "__VIDEO_GENERATING__", f"__VIDEO_FAILED__{error_payload}")
    except Exception:
        pass

    return {"cleaned": cleaned}


# --- Get video details (MUST be after all static routes to avoid catching /avatars etc.) ---
@router.get("/{video_id}")
async def api_get_video(
    video_id: str,
    user=Depends(get_current_user),
):
    user_id = user["phone_number"]
    with get_db() as session:
        video = session.get(VideoProject, video_id)
        if not video:
            video = session.query(VideoProject).filter(
                VideoProject.id.startswith(video_id),
                VideoProject.user_id == user_id,
            ).first()
        if not video or video.user_id != user_id:
            raise HTTPException(status_code=404, detail="Video not found")
        return video.to_dict()


# --- List scripts ---
@router.get("/scripts/")
async def api_list_scripts(
    limit: int = Query(10, ge=1, le=50),
    user=Depends(get_current_user),
):
    user_id = user["phone_number"]
    with get_db() as session:
        scripts = (
            session.query(VideoScript)
            .filter(VideoScript.user_id == user_id)
            .order_by(VideoScript.created_at.desc())
            .limit(limit)
            .all()
        )
        return {"scripts": [s.to_dict() for s in scripts]}

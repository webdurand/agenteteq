"""
REST API endpoints for video creation.
"""

import logging
import uuid
from datetime import datetime, timezone

import cloudinary.uploader
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from typing import Optional

from src.auth.deps import get_current_user
from src.db.session import get_db
from src.db.models import VideoProject, VideoScript

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


# --- Get video details ---
@router.get("/{video_id}")
async def api_get_video(
    video_id: str,
    user=Depends(get_current_user),
):
    user_id = user["phone_number"]
    with get_db() as session:
        video = session.get(VideoProject, video_id)
        if not video:
            # Try partial ID match
            video = session.query(VideoProject).filter(
                VideoProject.id.startswith(video_id),
                VideoProject.user_id == user_id,
            ).first()
        if not video or video.user_id != user_id:
            raise HTTPException(status_code=404, detail="Video not found")
        return video.to_dict()


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
            video.share_token = str(uuid.uuid4())[:8]
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

    # Read file (max 500MB)
    contents = await file.read()
    max_size = 500 * 1024 * 1024
    if len(contents) > max_size:
        raise HTTPException(status_code=400, detail="Arquivo muito grande. Maximo: 500MB.")

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

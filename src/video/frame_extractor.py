"""
Extract key frames from a video for use as AI Motion reference images.
Downloads video, extracts frames at strategic timestamps via FFmpeg,
uploads frames to Cloudinary.
"""

import asyncio
import logging
import os
import tempfile
import uuid

import cloudinary.uploader
import httpx

logger = logging.getLogger(__name__)


async def extract_key_frames(
    video_url: str,
    user_id: str,
    num_frames: int = 4,
) -> list[str]:
    """
    Extract key frames from a video and upload to Cloudinary.

    Args:
        video_url: Public URL of the video (Cloudinary or other).
        user_id: For organizing uploads.
        num_frames: Number of frames to extract (1-4).

    Returns:
        List of Cloudinary URLs for the extracted frame images.
    """
    num_frames = max(1, min(4, num_frames))

    # Download video to temp file (streaming to avoid loading 500MB in RAM)
    video_fd = tempfile.NamedTemporaryFile(suffix=".mp4", prefix="teq_avatar_", delete=False)
    video_path = video_fd.name
    video_fd.close()
    frame_paths: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("GET", video_url) as resp:
                resp.raise_for_status()
                with open(video_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)

        # Get video duration
        duration = await _get_duration(video_path)
        if duration <= 0:
            raise RuntimeError("Could not determine video duration")

        # Calculate timestamps: 1s, 33%, 50%, 66% of duration
        timestamps = _calculate_timestamps(duration, num_frames)

        # Extract frames
        frame_paths = []
        for i, ts in enumerate(timestamps):
            frame_fd = tempfile.NamedTemporaryFile(suffix=".jpg", prefix=f"teq_frame_{i}_", delete=False)
            frame_path = frame_fd.name
            frame_fd.close()
            await _extract_frame(video_path, ts, frame_path)
            frame_paths.append(frame_path)

        # Upload to Cloudinary in parallel
        import asyncio as _aio

        async def _upload_frame(fpath: str) -> str | None:
            try:
                result = await _aio.to_thread(
                    cloudinary.uploader.upload,
                    fpath,
                    folder=f"teq/avatars/{user_id}",
                    public_id=f"frame_{uuid.uuid4().hex[:8]}",
                    overwrite=True,
                )
                return result["secure_url"]
            except Exception as e:
                logger.error("Frame upload failed: %s", e)
                return None

        results = await _aio.gather(*[_upload_frame(fp) for fp in frame_paths])
        frame_urls = [u for u in results if u]

        if not frame_urls:
            raise RuntimeError("No frames could be uploaded")

        logger.info("Extracted %d frames from video for user %s", len(frame_urls), user_id)
        return frame_urls

    finally:
        # Cleanup temp files
        cleanup_paths = [video_path] + frame_paths
        for path in cleanup_paths:
            try:
                if path and os.path.exists(path):
                    os.unlink(path)
            except OSError:
                pass


def _calculate_timestamps(duration: float, num_frames: int) -> list[float]:
    """Calculate evenly spaced timestamps, avoiding the very start and end."""
    if num_frames == 1:
        return [min(1.0, duration * 0.5)]

    # Start at 1s or 10% of duration, end at 90%
    start = min(1.0, duration * 0.1)
    end = duration * 0.9
    step = (end - start) / (num_frames - 1)
    return [start + step * i for i in range(num_frames)]


async def _get_duration(video_path: str) -> float:
    """Get video duration in seconds using FFprobe."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    try:
        return float(stdout.decode().strip())
    except (ValueError, AttributeError):
        return 0.0


async def _extract_frame(video_path: str, timestamp: float, output_path: str):
    """Extract a single frame at the given timestamp."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-ss", str(round(timestamp, 2)),
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        output_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg frame extraction failed: {stderr.decode()[-200:]}")
    if not os.path.exists(output_path):
        raise RuntimeError(f"Frame not created at {output_path}")

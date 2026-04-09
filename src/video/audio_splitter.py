"""
Audio splitter: splits narration audio into per-scene segments using FFmpeg.
Uses Whisper caption timestamps + script scene durations to determine cut points.
"""

import asyncio
import logging
import os
import tempfile

import httpx
import cloudinary.uploader

logger = logging.getLogger(__name__)


async def split_audio_by_scenes(
    audio_url: str,
    script: dict,
    captions: list[dict],
    project_id: str = "",
) -> dict[str, str]:
    """
    Split narration audio into per-scene segments.

    Uses cumulative scene durations from script to determine cut points,
    then FFmpeg to slice the audio file.

    Args:
        audio_url: URL of the full narration audio (Cloudinary).
        script: Video script dict with hook, scenes, callback.
        captions: Word-level captions [{text, startMs, endMs}].
        project_id: For naming uploaded segments.

    Returns:
        Dict mapping scene_name → audio_segment_url (Cloudinary).
    """
    # Calculate scene boundaries (cumulative timestamps)
    scene_boundaries = _calculate_scene_boundaries(script)
    if not scene_boundaries:
        logger.warning("No scene boundaries calculated, returning empty")
        return {}

    # Download full audio
    audio_bytes = await _download_audio(audio_url)

    # Write to temp file
    audio_path = tempfile.mktemp(suffix=".mp3", prefix="teq_audio_full_")
    with open(audio_path, "wb") as f:
        f.write(audio_bytes)

    segments = {}
    try:
        for scene_name, start_s, end_s in scene_boundaries:
            segment_path = tempfile.mktemp(suffix=".mp3", prefix=f"teq_audio_{scene_name}_")
            try:
                await _ffmpeg_cut(audio_path, segment_path, start_s, end_s)

                # Upload segment to Cloudinary
                result = cloudinary.uploader.upload(
                    segment_path,
                    folder="teq/video_assets",
                    public_id=f"audio_segment_{project_id}_{scene_name}" if project_id else f"audio_segment_{scene_name}",
                    resource_type="video",
                    overwrite=True,
                )
                segments[scene_name] = result["secure_url"]
                logger.debug("Audio segment '%s': %.1fs-%.1fs → %s",
                             scene_name, start_s, end_s, result["secure_url"][:60])
            except Exception as e:
                logger.warning("Failed to split audio for scene '%s': %s", scene_name, e)
                segments[scene_name] = ""
            finally:
                if os.path.exists(segment_path):
                    os.unlink(segment_path)
    finally:
        if os.path.exists(audio_path):
            os.unlink(audio_path)

    logger.info("Audio split into %d segments (%d successful)",
                len(segments), sum(1 for v in segments.values() if v))
    return segments


def _calculate_scene_boundaries(script: dict) -> list[tuple[str, float, float]]:
    """
    Calculate start/end timestamps for each scene based on script durations.
    Returns list of (scene_name, start_seconds, end_seconds).
    """
    boundaries = []
    cursor = 0.0

    # Hook
    hook = script.get("hook", {})
    if hook.get("narration"):
        duration = hook.get("duration_s", 3)
        boundaries.append(("hook", cursor, cursor + duration))
        cursor += duration

    # Scenes
    for scene in script.get("scenes", []):
        if scene.get("narration"):
            duration = scene.get("duration_s", 5)
            boundaries.append((scene.get("name", "scene"), cursor, cursor + duration))
            cursor += duration

    # Callback
    callback = script.get("callback", {})
    if callback.get("narration"):
        duration = callback.get("duration_s", 5)
        boundaries.append(("callback", cursor, cursor + duration))

    return boundaries


async def _ffmpeg_cut(input_path: str, output_path: str, start_s: float, end_s: float):
    """Cut audio segment using FFmpeg."""
    duration = end_s - start_s
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-ss", f"{start_s:.2f}",
        "-t", f"{duration:.2f}",
        "-acodec", "libmp3lame",
        "-q:a", "2",
        output_path,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        error = stderr.decode("utf-8", errors="replace")[-300:]
        raise RuntimeError(f"FFmpeg cut failed: {error}")


async def _download_audio(url: str) -> bytes:
    """Download audio from URL."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content

"""
Video post-processing with FFmpeg.
Encodes for Instagram Reels and WhatsApp, extracts thumbnail.
"""

import asyncio
import os
import logging

logger = logging.getLogger(__name__)


async def encode_for_instagram(input_path: str) -> str:
    """
    Encode video for Instagram Reels.
    1080x1920, H.264 Main profile, 3500kbps, 30fps, AAC 256kbps.
    """
    output_path = input_path.replace(".mp4", "_instagram.mp4")

    cmd = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264",
        "-profile:v", "main",
        "-level:v", "3.1",
        "-crf", "23",
        "-maxrate", "3500k",
        "-bufsize", "3500k",
        "-r", "30",
        "-c:a", "aac",
        "-b:a", "256k",
        "-ar", "44100",
        "-movflags", "+faststart",
        "-y",
        output_path,
    ]

    await _run_ffmpeg(cmd, "Instagram encode")
    return output_path


async def encode_for_whatsapp(input_path: str) -> str:
    """
    Encode video for WhatsApp (<16MB).
    720p, H.264 Baseline, 1000-1500kbps, 25fps, AAC 160kbps.
    """
    output_path = input_path.replace(".mp4", "_whatsapp.mp4")

    cmd = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-2:720",
        "-c:v", "libx264",
        "-profile:v", "baseline",
        "-level", "3.0",
        "-preset", "slow",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-r", "25",
        "-c:a", "aac",
        "-b:a", "160k",
        "-ar", "44100",
        "-movflags", "+faststart",
        "-y",
        output_path,
    ]

    await _run_ffmpeg(cmd, "WhatsApp encode")

    # Check file size — if >16MB, re-encode with lower bitrate
    file_size = os.path.getsize(output_path)
    if file_size > 16 * 1024 * 1024:
        logger.warning("WhatsApp video too large (%.1f MB), re-encoding", file_size / 1024 / 1024)
        os.unlink(output_path)
        cmd_low = [
            "ffmpeg", "-i", input_path,
            "-vf", "scale=-2:480",
            "-c:v", "libx264",
            "-profile:v", "baseline",
            "-preset", "slow",
            "-crf", "28",
            "-r", "25",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-movflags", "+faststart",
            "-y",
            output_path,
        ]
        await _run_ffmpeg(cmd_low, "WhatsApp re-encode (lower quality)")

    return output_path


async def extract_thumbnail(input_path: str, time_s: float = 1.0) -> str:
    """
    Extract a thumbnail frame from the video.
    Takes frame at time_s (default: 1 second = hook frame).
    """
    output_path = input_path.replace(".mp4", "_thumb.jpg")

    cmd = [
        "ffmpeg", "-i", input_path,
        "-ss", str(time_s),
        "-frames:v", "1",
        "-q:v", "2",
        "-y",
        output_path,
    ]

    await _run_ffmpeg(cmd, "thumbnail extraction")
    return output_path


async def _run_ffmpeg(cmd: list[str], description: str):
    """Run an FFmpeg command as subprocess."""
    logger.info("FFmpeg %s: %s", description, " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        error = stderr.decode("utf-8", errors="replace")[-300:]
        logger.error("FFmpeg %s failed: %s", description, error)
        raise RuntimeError(f"FFmpeg {description} failed: {error}")

    logger.info("FFmpeg %s complete", description)

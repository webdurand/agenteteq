"""
Video assembler: bridges Python pipeline with Remotion render.
Builds inputProps JSON from script + assets, calls npx remotion render.
"""

import asyncio
import json
import os
import tempfile
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Path to the Remotion project
REMOTION_DIR = Path(__file__).parent / "remotion"
ENTRY_POINT = "src/index.ts"
COMPOSITION_ID = "ReelsVideo"


async def assemble_video(
    script: dict,
    audio_url: str,
    captions: list[dict],
    talking_head_url: str = "",
    broll_urls: dict[str, str] | None = None,
    overlay_urls: dict[str, str] | None = None,
    music_url: str = "",
    output_path: str = "",
    user_id: str = "",
    channel: str = "web",
) -> str:
    """
    Assemble final video using Remotion.

    Args:
        script: Video script dict (from script_generator).
        audio_url: URL of narration audio.
        captions: Word-level captions list [{text, startMs, endMs}].
        talking_head_url: URL of talking head video (mode avatar).
        broll_urls: Dict mapping scene name → B-roll video URL.
        overlay_urls: Dict mapping scene name → overlay image URL.
        music_url: Background music URL.
        output_path: Where to save the MP4. Auto-generated if empty.
        user_id: For cost tracking.
        channel: For cost tracking.

    Returns:
        Path to the rendered MP4 file.
    """
    broll_urls = broll_urls or {}
    overlay_urls = overlay_urls or {}

    if not output_path:
        output_path = tempfile.mktemp(suffix=".mp4", prefix="teq_video_")

    # Build Remotion inputProps
    input_props = _build_input_props(
        script=script,
        audio_url=audio_url,
        captions=captions,
        talking_head_url=talking_head_url,
        broll_urls=broll_urls,
        overlay_urls=overlay_urls,
        music_url=music_url,
    )

    # Write props to temp file (Remotion reads from file)
    props_path = tempfile.mktemp(suffix=".json", prefix="teq_props_")
    with open(props_path, "w") as f:
        json.dump(input_props, f, ensure_ascii=False)

    logger.info("Assembling video with Remotion (props: %s)", props_path)

    try:
        # Call Remotion render
        await _render_with_remotion(props_path, output_path)

        # Track cost
        _log_cost(user_id, channel)

        logger.info("Video assembled: %s", output_path)
        return output_path

    finally:
        # Cleanup props file
        try:
            os.unlink(props_path)
        except OSError:
            pass


def _build_input_props(
    script: dict,
    audio_url: str,
    captions: list[dict],
    talking_head_url: str,
    broll_urls: dict[str, str],
    overlay_urls: dict[str, str],
    music_url: str,
) -> dict:
    """Build Remotion inputProps from script and assets."""

    hook = script.get("hook", {})
    callback = script.get("callback", {})
    config = script.get("config", {})

    # Build scenes with asset URLs
    scenes = []
    for scene in script.get("scenes", []):
        scene_name = scene.get("name", "")
        scenes.append({
            "name": scene_name,
            "narration": scene.get("narration", ""),
            "on_screen_text": scene.get("on_screen_text", ""),
            "movement": scene.get("movement", "ken_burns"),
            "duration_s": scene.get("duration_s", 5),
            "broll_url": broll_urls.get(scene_name, ""),
            "overlay_image_url": overlay_urls.get(scene_name, ""),
            "sfx": scene.get("sfx"),
        })

    return {
        "audioUrl": audio_url,
        "captions": captions,
        "scenes": scenes,
        "hook": {
            "narration": hook.get("narration", ""),
            "on_screen_text": hook.get("on_screen_text", ""),
            "movement": hook.get("movement", "zoom_in_face"),
            "duration_s": hook.get("duration_s", 3),
            "broll_url": broll_urls.get("hook", ""),
        },
        "callback": {
            "narration": callback.get("narration", ""),
            "on_screen_text": callback.get("on_screen_text", ""),
            "movement": callback.get("movement", "zoom_out"),
            "duration_s": callback.get("duration_s", 5),
        },
        "config": {
            "music_url": music_url,
            "music_volume": 0.1,
            "caption_style": config.get("caption_style", "tiktok_bounce_highlight"),
            "talking_head_url": talking_head_url,
        },
    }


async def _render_with_remotion(props_path: str, output_path: str):
    """Call npx remotion render as subprocess."""
    cmd = [
        "npx", "remotion", "render",
        ENTRY_POINT,
        COMPOSITION_ID,
        output_path,
        f"--props={props_path}",
        "--codec=h264",
        "--concurrency=50%",
        "--log=error",
    ]

    logger.info("Running Remotion: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(REMOTION_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode("utf-8", errors="replace")[-500:]
        logger.error("Remotion render failed (code %d): %s", proc.returncode, error_msg)
        raise RuntimeError(f"Remotion render failed: {error_msg}")

    if not os.path.exists(output_path):
        raise RuntimeError(f"Remotion render produced no output at {output_path}")

    file_size = os.path.getsize(output_path)
    logger.info("Remotion render complete: %s (%.1f MB)", output_path, file_size / 1024 / 1024)


def _log_cost(user_id: str, channel: str):
    """Track Remotion render cost (local render = free)."""
    if not user_id:
        return
    try:
        from src.memory.analytics import log_event
        log_event(
            user_id=user_id,
            channel=channel,
            event_type="video_render",
            tool_name="remotion-local",
            status="success",
            extra_data={"cost_usd": 0.0},
        )
    except Exception as e:
        logger.error("Failed to log render cost: %s", e)

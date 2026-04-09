"""
Talking head video generation.
Primary: D-ID API (photo + audio → talking head video with lip-sync).
"""

import asyncio
import os
import logging

import httpx

logger = logging.getLogger(__name__)

D_ID_API_URL = "https://api.d-id.com"


async def generate_talking_head(
    photo_url: str,
    audio_url: str,
    user_id: str = "",
    channel: str = "web",
) -> str:
    """
    Generate a talking head video from a photo and audio.

    Args:
        photo_url: URL of the person's photo (Cloudinary or public URL).
        audio_url: URL of the narration audio (Cloudinary or public URL).
        user_id: For cost tracking.
        channel: For cost tracking.

    Returns:
        URL of the generated talking head video (MP4).
    """
    api_key = os.getenv("DID_API_KEY")
    if not api_key:
        raise ValueError("DID_API_KEY not configured in .env")

    headers = {
        "Authorization": f"Basic {api_key}",
        "Content-Type": "application/json",
    }

    # Create talk
    payload = {
        "source_url": photo_url,
        "script": {
            "type": "audio",
            "audio_url": audio_url,
        },
        "config": {
            "stitch": True,
        },
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{D_ID_API_URL}/talks",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        talk_id = data["id"]

    logger.info("D-ID talk created: %s", talk_id)

    # Poll for completion
    video_url = await _poll_talk_status(talk_id, api_key)

    # Track cost
    _log_cost(user_id, channel, talk_id)

    return video_url


async def _poll_talk_status(
    talk_id: str,
    api_key: str,
    max_attempts: int = 60,
    interval_s: int = 5,
) -> str:
    """Poll D-ID API until talk is done or failed."""
    headers = {
        "Authorization": f"Basic {api_key}",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(max_attempts):
            await asyncio.sleep(interval_s)

            resp = await client.get(
                f"{D_ID_API_URL}/talks/{talk_id}",
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "")

            if status == "done":
                result_url = data.get("result_url", "")
                if result_url:
                    logger.info("D-ID talk done: %s", talk_id)
                    return result_url
                raise RuntimeError(f"D-ID talk done but no result_url: {data}")

            if status in ("error", "rejected"):
                error_msg = data.get("error", {}).get("description", str(data))
                raise RuntimeError(f"D-ID talk failed: {error_msg}")

            logger.debug("D-ID talk %s status: %s (attempt %d)", talk_id, status, attempt + 1)

    raise TimeoutError(f"D-ID talk {talk_id} timed out after {max_attempts * interval_s}s")


def _log_cost(user_id: str, channel: str, talk_id: str):
    """Track D-ID cost. Lite plan: $5.90/mo for 10 min → ~$0.01/sec."""
    if not user_id:
        return
    try:
        from src.memory.analytics import log_event
        # Approximate: 60s video ≈ $0.80
        log_event(
            user_id=user_id,
            channel=channel,
            event_type="video_talking_head",
            tool_name="d-id",
            status="success",
            extra_data={
                "talk_id": talk_id,
                "cost_usd": 0.80,
            },
        )
    except Exception as e:
        logger.error("Failed to log D-ID cost: %s", e)

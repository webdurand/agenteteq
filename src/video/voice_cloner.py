"""
Voice cloning via ElevenLabs Instant Voice Clone API.
Accepts an audio sample and creates a cloned voice that can be used for TTS.
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1"


async def clone_voice(
    audio_bytes: bytes,
    voice_name: str,
    user_id: str = "",
    description: str = "",
) -> dict:
    """
    Clone a voice using ElevenLabs Instant Voice Clone.

    Args:
        audio_bytes: Audio sample (30s-5min recommended, any format).
        voice_name: Name for the cloned voice.
        user_id: For logging.
        description: Optional description of the voice.

    Returns:
        {"voice_id": "abc123", "voice_name": "My Voice"}
    """
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY not configured")

    headers = {
        "xi-api-key": api_key,
    }

    # ElevenLabs expects multipart form data
    files = {
        "files": ("voice_sample.mp3", audio_bytes, "audio/mpeg"),
    }
    data = {
        "name": voice_name,
        "description": description or f"Cloned voice for user {user_id}",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{ELEVENLABS_API_URL}/voices/add",
            headers=headers,
            files=files,
            data=data,
        )
        resp.raise_for_status()
        result = resp.json()

    voice_id = result.get("voice_id", "")
    if not voice_id:
        raise RuntimeError(f"ElevenLabs did not return a voice_id: {result}")

    logger.info(
        "Voice cloned for user %s: voice_id=%s, name=%s",
        user_id, voice_id, voice_name,
    )

    # Log cost
    _log_cost(user_id, voice_name)

    return {
        "voice_id": voice_id,
        "voice_name": voice_name,
    }


async def delete_cloned_voice(voice_id: str) -> bool:
    """Delete a cloned voice from ElevenLabs."""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        return False

    headers = {"xi-api-key": api_key}

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.delete(
            f"{ELEVENLABS_API_URL}/voices/{voice_id}",
            headers=headers,
        )
        if resp.status_code == 200:
            logger.info("Deleted cloned voice: %s", voice_id)
            return True
        logger.warning("Failed to delete voice %s: %s", voice_id, resp.status_code)
        return False


def _log_cost(user_id: str, voice_name: str):
    """Track voice cloning cost (ElevenLabs Instant Clone is free on paid plans)."""
    if not user_id:
        return
    try:
        from src.memory.analytics import log_event
        log_event(
            user_id=user_id,
            channel="web",
            event_type="voice_clone",
            tool_name="elevenlabs",
            status="success",
            extra_data={
                "voice_name": voice_name,
                "cost_usd": 0.0,  # Instant clone is included in ElevenLabs plans
            },
        )
    except Exception as e:
        logger.error("Failed to log voice clone cost: %s", e)

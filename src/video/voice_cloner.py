"""
Voice cloning via ElevenLabs API.
Supports both Instant Voice Clone (1 sample) and multi-sample cloning (better quality).
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1"


async def clone_voice(
    audio_samples: list[bytes] | bytes,
    voice_name: str,
    user_id: str = "",
    description: str = "",
) -> dict:
    """
    Clone a voice using ElevenLabs Instant Voice Clone.

    Supports 1-25 audio samples. More samples = better quality.
    Each sample should be 30s-5min of clean speech (no music, no background noise).

    Args:
        audio_samples: Single audio bytes or list of audio bytes (1-25 samples).
        voice_name: Name for the cloned voice.
        user_id: For logging.
        description: Optional description of the voice.

    Returns:
        {"voice_id": "abc123", "voice_name": "My Voice", "num_samples": 3}
    """
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY not configured")

    # Normalize to list
    if isinstance(audio_samples, bytes):
        audio_samples = [audio_samples]

    if not audio_samples:
        raise ValueError("At least 1 audio sample is required")

    if len(audio_samples) > 25:
        logger.warning("Too many samples (%d), using first 25", len(audio_samples))
        audio_samples = audio_samples[:25]

    headers = {
        "xi-api-key": api_key,
    }

    # ElevenLabs expects multipart form data with multiple "files" entries
    files = [
        ("files", (f"sample_{i}.mp3", sample, "audio/mpeg"))
        for i, sample in enumerate(audio_samples)
    ]
    data = {
        "name": voice_name,
        "description": description or f"Cloned voice for user {user_id}",
    }

    logger.info("Cloning voice '%s' with %d sample(s) for user %s",
                voice_name, len(audio_samples), user_id[:8] if user_id else "?")

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{ELEVENLABS_API_URL}/voices/add",
            headers=headers,
            files=files,
            data=data,
        )
        if resp.status_code >= 400:
            logger.error("ElevenLabs clone error %d: %s", resp.status_code, resp.text[:300])
        resp.raise_for_status()
        result = resp.json()

    voice_id = result.get("voice_id", "")
    if not voice_id:
        raise RuntimeError(f"ElevenLabs did not return a voice_id: {result}")

    logger.info(
        "Voice cloned for user %s: voice_id=%s, name=%s, samples=%d",
        user_id, voice_id, voice_name, len(audio_samples),
    )

    _log_cost(user_id, voice_name)

    return {
        "voice_id": voice_id,
        "voice_name": voice_name,
        "num_samples": len(audio_samples),
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

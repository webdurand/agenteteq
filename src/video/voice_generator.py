"""
Voice generator for video narration.
Primary: ElevenLabs API (mid tier). Fallback: Gemini TTS (free, already in project).
"""

import io
import os
import logging

import httpx

logger = logging.getLogger(__name__)

ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1"

# Default voice: Rachel (female, clear, neutral). User can override.
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"

# PT-BR voices available on ElevenLabs (common ones)
VOICE_PRESETS = {
    "rachel": "21m00Tcm4TlvDq8ikWAM",
    "adam": "pNInz6obpgDQGcFmaJgB",
    "josh": "TxGEqnHWrfWFTfGW9XjX",
    "bella": "EXAVITQu4vr4xnSDxMaL",
    "elli": "MF3mGyEYCl7XYWbV9V6O",
    "sam": "yoZ06aMxZJJ28mfd3POQ",
}


async def generate_voice(
    text: str,
    voice: str = "",
    user_id: str = "",
    channel: str = "web",
) -> tuple[bytes, str, float]:
    """
    Generate narration audio from text.

    Args:
        text: Full narration text.
        voice: Voice name/id. Empty = default.
        user_id: For cost tracking.
        channel: For cost tracking.

    Returns:
        Tuple of (audio_bytes, mime_type, duration_seconds).
    """
    api_key = os.getenv("ELEVENLABS_API_KEY")

    if api_key:
        try:
            result = await _generate_elevenlabs(text, voice, api_key)
            _log_cost(user_id, channel, "elevenlabs", len(text), result[2])
            return result
        except Exception as e:
            logger.warning("ElevenLabs failed, falling back to Gemini TTS: %s", e)

    # Fallback to Gemini TTS
    result = await _generate_gemini_tts(text)
    _log_cost(user_id, channel, "gemini_tts", len(text), result[2])
    return result


async def _generate_elevenlabs(
    text: str,
    voice: str,
    api_key: str,
) -> tuple[bytes, str, float]:
    """Generate audio via ElevenLabs API."""
    voice_id = VOICE_PRESETS.get(voice.lower(), voice) if voice else DEFAULT_VOICE_ID

    # If voice looks like a name but isn't in presets, use default
    if voice and not voice_id.startswith(("2", "p", "T", "E", "M", "y")):
        voice_id = DEFAULT_VOICE_ID

    url = f"{ELEVENLABS_API_URL}/text-to-speech/{voice_id}"

    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.3,
            "use_speaker_boost": True,
        },
    }

    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        audio_bytes = resp.content

    # Estimate duration: MP3 ~128kbps → bytes / 16000 ≈ seconds
    duration_s = max(1.0, len(audio_bytes) / 16000)

    return audio_bytes, "audio/mpeg", duration_s


async def _generate_gemini_tts(text: str) -> tuple[bytes, str, float]:
    """Fallback: Generate audio via Gemini TTS (already in project)."""
    from src.integrations.tts import get_tts

    tts = get_tts()
    audio_bytes, mime_type = await tts.synthesize(text)

    # WAV 24kHz 16-bit mono → duration = (bytes - 44 header) / (24000 * 2)
    duration_s = max(1.0, (len(audio_bytes) - 44) / 48000)

    return audio_bytes, mime_type, duration_s


def _log_cost(user_id: str, channel: str, provider: str, text_len: int, duration_s: float):
    """Track TTS cost."""
    if not user_id:
        return
    try:
        from src.memory.analytics import log_event

        # ElevenLabs Starter: $5/mo for 30 min → ~$0.003/sec
        # Gemini TTS: free tier
        cost = round(duration_s * 0.003, 4) if provider == "elevenlabs" else 0.0

        log_event(
            user_id=user_id,
            channel=channel,
            event_type="video_voice",
            tool_name=provider,
            status="success",
            extra_data={
                "text_length": text_len,
                "duration_seconds": round(duration_s, 1),
                "cost_usd": cost,
            },
        )
    except Exception as e:
        logger.error("Failed to log TTS cost: %s", e)


async def convert_to_wav(audio_bytes: bytes, mime_type: str) -> bytes:
    """
    Convert audio to WAV format if needed (for Whisper compatibility).
    ElevenLabs returns MP3; Gemini TTS returns WAV.
    """
    if mime_type == "audio/wav":
        return audio_bytes

    # MP3 to WAV via ffmpeg subprocess
    import asyncio
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as mp3_f:
        mp3_f.write(audio_bytes)
        mp3_path = mp3_f.name

    wav_path = mp3_path.replace(".mp3", ".wav")

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", mp3_path, "-ar", "16000", "-ac", "1", "-y", wav_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()

    try:
        with open(wav_path, "rb") as f:
            wav_bytes = f.read()
    finally:
        import os as _os
        _os.unlink(mp3_path)
        if _os.path.exists(wav_path):
            _os.unlink(wav_path)

    return wav_bytes

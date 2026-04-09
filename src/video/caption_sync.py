"""
Caption sync via OpenAI Whisper API.
Returns word-level timestamps compatible with @remotion/captions.
"""

import os
import logging

import httpx

logger = logging.getLogger(__name__)

WHISPER_API_URL = "https://api.openai.com/v1/audio/transcriptions"


async def generate_captions(
    audio_bytes: bytes,
    language: str = "pt",
    user_id: str = "",
    channel: str = "web",
) -> list[dict]:
    """
    Transcribe audio with word-level timestamps using OpenAI Whisper API.

    Args:
        audio_bytes: Audio file bytes (WAV or MP3).
        language: Language code.
        user_id: For cost tracking.
        channel: For cost tracking.

    Returns:
        List of caption dicts: [{text, startMs, endMs}]
        Compatible with @remotion/captions Caption type.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not configured")

    headers = {
        "Authorization": f"Bearer {api_key}",
    }

    # Whisper API with word-level timestamps
    files = {
        "file": ("narration.wav", audio_bytes, "audio/wav"),
    }
    data = {
        "model": "whisper-1",
        "language": language,
        "response_format": "verbose_json",
        "timestamp_granularities[]": "word",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            WHISPER_API_URL,
            headers=headers,
            data=data,
            files=files,
        )
        resp.raise_for_status()
        result = resp.json()

    # Extract word-level timestamps
    captions = []
    words = result.get("words", [])

    for word_data in words:
        text = word_data.get("word", "")
        start = word_data.get("start", 0)
        end = word_data.get("end", 0)

        captions.append({
            "text": text,
            "startMs": int(start * 1000),
            "endMs": int(end * 1000),
            "timestampMs": None,
            "confidence": None,
        })

    # Track cost
    _log_cost(user_id, channel, len(audio_bytes), len(captions))

    logger.info(
        "Caption sync: %d words extracted, duration ~%.1fs",
        len(captions),
        captions[-1]["endMs"] / 1000 if captions else 0,
    )

    return captions


def captions_to_srt(captions: list[dict], words_per_line: int = 6) -> str:
    """
    Convert word-level captions to SRT format (for fallback/debugging).
    Groups words into lines of ~words_per_line.
    """
    if not captions:
        return ""

    lines = []
    idx = 1

    for i in range(0, len(captions), words_per_line):
        group = captions[i:i + words_per_line]
        start_ms = group[0]["startMs"]
        end_ms = group[-1]["endMs"]
        text = " ".join(w["text"].strip() for w in group)

        start_ts = _ms_to_srt_time(start_ms)
        end_ts = _ms_to_srt_time(end_ms)

        lines.append(f"{idx}")
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(text)
        lines.append("")
        idx += 1

    return "\n".join(lines)


def _ms_to_srt_time(ms: int) -> str:
    """Convert milliseconds to SRT timestamp format (HH:MM:SS,mmm)."""
    hours = ms // 3600000
    minutes = (ms % 3600000) // 60000
    seconds = (ms % 60000) // 1000
    millis = ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _log_cost(user_id: str, channel: str, audio_size: int, word_count: int):
    """Track Whisper transcription cost."""
    if not user_id:
        return
    try:
        from src.memory.analytics import log_event

        # Whisper: $0.006 per minute of audio
        # WAV 16kHz 16-bit mono: 32000 bytes/sec
        duration_seconds = max(1, audio_size / 32000)
        duration_minutes = duration_seconds / 60
        cost_usd = round(duration_minutes * 0.006, 6)

        log_event(
            user_id=user_id,
            channel=channel,
            event_type="whisper_transcription",
            tool_name="whisper-word-timestamps",
            status="success",
            extra_data={
                "audio_size_bytes": audio_size,
                "duration_seconds": round(duration_seconds, 1),
                "word_count": word_count,
                "cost_usd": cost_usd,
            },
        )
    except Exception as e:
        logger.error("Failed to log Whisper cost: %s", e)

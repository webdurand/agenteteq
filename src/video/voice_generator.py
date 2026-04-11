"""
Voice generator for video narration.
Primary: ElevenLabs API (mid tier). Fallback: Gemini TTS (free, already in project).
"""

import asyncio
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


async def generate_voice_takes(
    text: str,
    voice: str = "",
    num_takes: int = 3,
    user_id: str = "",
    channel: str = "web",
) -> list[tuple[bytes, str, float]]:
    """
    Generate multiple takes of the same narration (v3 is non-deterministic).
    Useful for social media content — pick the best take in post-production.

    Returns:
        List of (audio_bytes, mime_type, duration_seconds) tuples.
    """
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        result = await _generate_gemini_tts(text)
        _log_cost(user_id, channel, "gemini_tts", len(text), result[2])
        return [result]

    num_takes = max(1, min(num_takes, 5))

    async def _one_take():
        return await _generate_elevenlabs(text, voice, api_key)

    results = await asyncio.gather(*[_one_take() for _ in range(num_takes)], return_exceptions=True)

    takes = [r for r in results if not isinstance(r, Exception)]
    if not takes:
        logger.warning("All ElevenLabs takes failed, falling back to Gemini TTS")
        result = await _generate_gemini_tts(text)
        _log_cost(user_id, channel, "gemini_tts", len(text), result[2])
        return [result]

    for take in takes:
        _log_cost(user_id, channel, "elevenlabs", len(text), take[2])

    logger.info("Generated %d/%d takes successfully", len(takes), num_takes)
    return takes


ELEVENLABS_CHAR_LIMIT = 5000


async def _generate_elevenlabs(
    text: str,
    voice: str,
    api_key: str,
) -> tuple[bytes, str, float]:
    """Generate audio via ElevenLabs API. Splits long texts automatically."""
    if not voice:
        voice_id = DEFAULT_VOICE_ID
    elif voice.lower() in VOICE_PRESETS:
        voice_id = VOICE_PRESETS[voice.lower()]
    else:
        voice_id = voice

    if len(text) <= ELEVENLABS_CHAR_LIMIT:
        return await _call_elevenlabs(text, voice_id, api_key)

    # Text exceeds limit — split semantically, generate in parallel, concat with ffmpeg
    chunks = _split_text(text, ELEVENLABS_CHAR_LIMIT)
    logger.info("Text too long (%d chars), split into %d chunks", len(text), len(chunks))

    chunk_results = await asyncio.gather(
        *[_call_elevenlabs(chunk, voice_id, api_key) for chunk in chunks]
    )

    total_duration = sum(r[2] for r in chunk_results)

    if len(chunk_results) == 1:
        return chunk_results[0]

    # Concatenate MP3 chunks via ffmpeg concat demuxer (raw byte concat is invalid)
    all_audio = await _concat_mp3_chunks([r[0] for r in chunk_results])

    for i, (_, _, dur) in enumerate(chunk_results):
        logger.info("Chunk %d/%d done: %d chars, %.1fs", i + 1, len(chunks), len(chunks[i]), dur)

    return all_audio, "audio/mpeg", total_duration


async def _concat_mp3_chunks(chunks: list[bytes]) -> bytes:
    """Concatenate multiple MP3 byte chunks into a single valid MP3 using ffmpeg concat demuxer."""
    import tempfile
    import os as _os

    tmpdir = tempfile.mkdtemp(prefix="teq_mp3_")
    try:
        # Write each chunk to a temp file
        chunk_paths = []
        for i, chunk in enumerate(chunks):
            path = _os.path.join(tmpdir, f"chunk_{i}.mp3")
            with open(path, "wb") as f:
                f.write(chunk)
            chunk_paths.append(path)

        # Create concat list file
        list_path = _os.path.join(tmpdir, "concat.txt")
        with open(list_path, "w") as f:
            for p in chunk_paths:
                f.write(f"file '{p}'\n")

        output_path = _os.path.join(tmpdir, "output.mp3")

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_path, "-c", "copy", output_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning("ffmpeg concat failed (rc=%d), falling back to raw concat: %s",
                           proc.returncode, stderr.decode()[:200])
            return b"".join(chunks)

        with open(output_path, "rb") as f:
            return f.read()
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


async def _call_elevenlabs(
    text: str,
    voice_id: str,
    api_key: str,
) -> tuple[bytes, str, float]:
    """Single ElevenLabs API call (text must be within char limit)."""
    url = f"{ELEVENLABS_API_URL}/text-to-speech/{voice_id}"
    model_id = os.getenv("ELEVENLABS_MODEL", "eleven_v3")

    payload = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.50,
            "similarity_boost": 0.75,
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

    duration_s = max(1.0, len(audio_bytes) / 16000)
    return audio_bytes, "audio/mpeg", duration_s


def _split_text(text: str, max_chars: int) -> list[str]:
    """
    Split text into chunks that fit within max_chars using AI.
    Preserves natural speech flow — never breaks mid-sentence.
    """
    if len(text) <= max_chars:
        return [text]

    try:
        return _split_text_ai(text, max_chars)
    except Exception as e:
        logger.warning("AI split failed, using simple fallback: %s", e)
        return _split_text_simple(text, max_chars)


def _split_text_ai(text: str, max_chars: int) -> list[str]:
    """Use Gemini Flash to split text semantically."""
    from google import genai
    from google.genai.types import GenerateContentConfig

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    client = genai.Client(api_key=api_key)

    prompt = (
        f"Divida o texto abaixo em pedacos de no maximo {max_chars} caracteres cada.\n"
        "Regras:\n"
        "- Quebre em pontos naturais de pausa (fim de frase, fim de paragrafo, mudanca de assunto)\n"
        "- NUNCA quebre no meio de uma frase\n"
        "- Mantenha o texto EXATAMENTE como esta, sem alterar nenhuma palavra\n"
        "- Retorne um JSON array de strings: [\"pedaco1\", \"pedaco2\", ...]\n\n"
        f"Texto:\n{text}"
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
        ),
    )

    import json
    chunks = json.loads(response.text)

    # Validate: all chunks must be within limit
    for chunk in chunks:
        if len(chunk) > max_chars:
            logger.warning("AI returned chunk with %d chars (limit %d), falling back", len(chunk), max_chars)
            return _split_text_simple(text, max_chars)

    return [c for c in chunks if c.strip()]


def _split_text_simple(text: str, max_chars: int) -> list[str]:
    """Fallback: split on sentence boundaries."""
    import re
    sentences = re.split(r'(?<=[.!?…])\s+', text)
    chunks = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks


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

"""
HeyGen API client for avatar video generation.

Handles the full lifecycle:
- Upload assets (photos, audio)
- Create & train photo avatar groups
- Generate looks/variations
- Generate multi-scene avatar videos
- Text-to-speech with cloned voice
- Poll video status

API Docs: https://docs.heygen.com/
Auth: X-Api-Key header
"""

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.heygen.com"
UPLOAD_BASE = "https://upload.heygen.com"


def _get_api_key() -> str:
    key = os.getenv("HEYGEN_API_KEY")
    if not key:
        raise ValueError("HEYGEN_API_KEY not configured in .env")
    return key


def _headers() -> dict:
    return {
        "X-Api-Key": _get_api_key(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ──────────────────────────────────────────────
# Asset Upload
# ──────────────────────────────────────────────

async def upload_asset(file_bytes: bytes, content_type: str = "image/jpeg") -> dict:
    """
    Upload a file (image, audio, video) to HeyGen.

    Args:
        file_bytes: Raw binary content.
        content_type: MIME type (image/jpeg, image/png, audio/mpeg, video/mp4).

    Returns:
        dict with keys: id, file_type, url, image_key (for images).
    """
    headers = {
        "X-Api-Key": _get_api_key(),
        "Content-Type": content_type,
    }

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{UPLOAD_BASE}/v1/asset",
            headers=headers,
            content=file_bytes,
        )
        resp.raise_for_status()
        result = resp.json()

    if result.get("code") != 100:
        raise RuntimeError(f"HeyGen upload failed: {result}")

    data = result.get("data", {})
    logger.info("HeyGen asset uploaded: type=%s, id=%s", data.get("file_type"), data.get("id"))
    return data


async def upload_image_from_url(image_url: str) -> dict:
    """Download an image from URL and upload to HeyGen. Returns asset data with image_key."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(image_url)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "image/jpeg")
        if "png" in content_type:
            content_type = "image/png"
        else:
            content_type = "image/jpeg"
        return await upload_asset(resp.content, content_type)


async def upload_audio_from_url(audio_url: str) -> dict:
    """Download audio from URL and upload to HeyGen. Returns asset data."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(audio_url)
        resp.raise_for_status()
        return await upload_asset(resp.content, "audio/mpeg")


# ──────────────────────────────────────────────
# Photo Avatar Group
# ──────────────────────────────────────────────

async def create_avatar_group(name: str, image_key: str) -> dict:
    """
    Create a photo avatar group with an initial image.

    Args:
        name: Avatar name (e.g. "Meu Avatar Profissional").
        image_key: From upload_asset() response.

    Returns:
        dict with group_id, id, status, image_url.
    """
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{API_BASE}/v2/photo_avatar/avatar_group/create",
            headers=_headers(),
            json={"name": name, "image_key": image_key},
        )
        resp.raise_for_status()
        result = resp.json()

    if result.get("error"):
        raise RuntimeError(f"HeyGen create avatar group failed: {result['error']}")

    data = result.get("data", {})
    logger.info("HeyGen avatar group created: group_id=%s, status=%s",
                data.get("group_id"), data.get("status"))
    return data


async def add_looks_to_group(group_id: str, image_keys: list[str], name: str = "look") -> dict:
    """
    Add additional photo looks to an existing avatar group.

    Args:
        group_id: Avatar group ID.
        image_keys: List of image_key values (max 4 per request).
        name: Look name.

    Returns:
        dict with photo_avatar_list.
    """
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{API_BASE}/v2/photo_avatar/avatar_group/add",
            headers=_headers(),
            json={
                "group_id": group_id,
                "image_keys": image_keys,
                "name": name,
            },
        )
        resp.raise_for_status()
        result = resp.json()

    if result.get("error"):
        raise RuntimeError(f"HeyGen add looks failed: {result['error']}")

    data = result.get("data", {})
    logger.info("HeyGen looks added to group %s: %d photos",
                group_id, len(data.get("photo_avatar_list", [])))
    return data


async def train_avatar_group(group_id: str) -> str:
    """
    Start training for a photo avatar group.
    Training teaches HeyGen to recognize the person's features.

    Returns:
        flow_id for tracking training status.
    """
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{API_BASE}/v2/photo_avatar/train",
            headers=_headers(),
            json={"group_id": group_id},
        )
        resp.raise_for_status()
        result = resp.json()

    if result.get("error"):
        raise RuntimeError(f"HeyGen train failed: {result['error']}")

    data = result.get("data", {})
    flow_id = data.get("data", {}).get("flow_id", "")
    logger.info("HeyGen training started: group_id=%s, flow_id=%s", group_id, flow_id)
    return flow_id


async def check_training_status(flow_id: str) -> dict:
    """Check status of avatar training job."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{API_BASE}/v2/training_jobs/{flow_id}",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json().get("data", {})


async def wait_for_training(flow_id: str, max_attempts: int = 120, interval_s: int = 15) -> bool:
    """
    Poll until avatar training completes.
    Training typically takes 2-10 minutes.

    Returns:
        True if training succeeded.
    """
    for attempt in range(max_attempts):
        try:
            status_data = await check_training_status(flow_id)
            status = status_data.get("status", "")
            logger.info("Training poll %d/%d: status=%s", attempt + 1, max_attempts, status)

            if status in ("completed", "success", "done"):
                return True
            if status in ("failed", "error"):
                raise RuntimeError(f"HeyGen training failed: {status_data}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning("Training job %s not found yet, retrying...", flow_id)
            else:
                raise

        await asyncio.sleep(interval_s)

    raise RuntimeError(f"HeyGen training timed out after {max_attempts * interval_s}s")


# ──────────────────────────────────────────────
# Generate Looks (AI-generated variations)
# ──────────────────────────────────────────────

async def generate_look(
    group_id: str,
    prompt: str,
    orientation: str = "vertical",
    pose: str = "half_body",
    style: str = "Realistic",
) -> str:
    """
    Generate an AI look variation of the avatar.

    Args:
        group_id: Avatar group ID.
        prompt: Description (e.g. "Professional suit in modern office").
        orientation: "square", "horizontal", or "vertical".
        pose: "half_body", "close_up", or "full_body".
        style: "Realistic", "Cinematic", "Pixar", etc.

    Returns:
        generation_id for status tracking.
    """
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{API_BASE}/v2/photo_avatar/look/generate",
            headers=_headers(),
            json={
                "group_id": group_id,
                "prompt": prompt,
                "orientation": orientation,
                "pose": pose,
                "style": style,
            },
        )
        resp.raise_for_status()
        result = resp.json()

    if result.get("error"):
        raise RuntimeError(f"HeyGen generate look failed: {result['error']}")

    generation_id = result.get("data", {}).get("generation_id", "")
    logger.info("HeyGen look generation started: group_id=%s, generation_id=%s", group_id, generation_id)
    return generation_id


async def check_look_status(generation_id: str) -> dict:
    """Check status of look generation."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{API_BASE}/v2/photo_avatars/{generation_id}/status",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json().get("data", {})


async def wait_for_look(generation_id: str, max_attempts: int = 60, interval_s: int = 10) -> dict:
    """Poll until look generation completes. Returns look data."""
    for attempt in range(max_attempts):
        status_data = await check_look_status(generation_id)
        status = status_data.get("status", "")
        logger.info("Look poll %d/%d: status=%s", attempt + 1, max_attempts, status)

        if status in ("completed", "success"):
            return status_data
        if status in ("failed", "error"):
            raise RuntimeError(f"HeyGen look generation failed: {status_data}")

        await asyncio.sleep(interval_s)

    raise RuntimeError(f"HeyGen look generation timed out after {max_attempts * interval_s}s")


# ──────────────────────────────────────────────
# List Avatars & Voices
# ──────────────────────────────────────────────

async def list_avatars() -> list[dict]:
    """List all available avatars (including photo avatars)."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{API_BASE}/v2/avatars", headers=_headers())
        resp.raise_for_status()
        return resp.json().get("data", {}).get("avatars", [])


async def list_avatar_group_looks(group_id: str) -> list[dict]:
    """List all looks/avatars within a specific group."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{API_BASE}/v2/avatar_groups/{group_id}/avatars",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json().get("data", {}).get("avatars", [])


async def list_voices() -> list[dict]:
    """List all available voices (including cloned ones)."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{API_BASE}/v2/voices", headers=_headers())
        resp.raise_for_status()
        return resp.json().get("data", {}).get("voices", [])


# ──────────────────────────────────────────────
# Voice Cloning
# ──────────────────────────────────────────────

async def clone_voice(audio_bytes: bytes, voice_name: str = "Minha voz") -> str:
    """
    Clone a voice from an audio sample.

    Args:
        audio_bytes: Raw audio content (MP3, WAV, WebM, etc.)
        voice_name: Display name for the cloned voice.

    Returns:
        voice_id of the cloned voice.
    """
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{API_BASE}/v2/voices/clone",
            headers={
                "X-Api-Key": _get_api_key(),
                "Accept": "application/json",
            },
            files={
                "file": ("voice_sample.webm", audio_bytes, "audio/webm"),
            },
            data={
                "voice_name": voice_name,
            },
        )
        resp.raise_for_status()
        result = resp.json()

    if result.get("error"):
        raise RuntimeError(f"HeyGen voice clone failed: {result['error']}")

    voice_id = result.get("data", {}).get("voice_id", "")
    logger.info("HeyGen voice cloned: voice_id=%s, name=%s", voice_id, voice_name)
    return voice_id


async def clone_voice_from_urls(audio_urls: list[str], voice_name: str = "Minha voz") -> str:
    """
    Clone a voice from multiple audio URLs.
    Downloads all samples, concatenates them, and sends to HeyGen.
    HeyGen only accepts one file, so we send the first sample.
    More samples = better quality with ElevenLabs, but HeyGen uses single file.

    Args:
        audio_urls: List of audio URLs (Cloudinary).
        voice_name: Display name.

    Returns:
        voice_id of the cloned voice.
    """
    if not audio_urls:
        raise ValueError("At least one audio URL is required")

    # Download the first (or best) sample
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(audio_urls[0])
        resp.raise_for_status()
        audio_bytes = resp.content

    return await clone_voice(audio_bytes, voice_name)


async def delete_voice(voice_id: str) -> bool:
    """Delete a cloned voice from HeyGen."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.delete(
            f"{API_BASE}/v2/voices/{voice_id}",
            headers=_headers(),
        )
        if resp.status_code == 200:
            logger.info("HeyGen voice deleted: %s", voice_id)
            return True
        logger.warning("HeyGen voice delete failed: %s %s", resp.status_code, resp.text[:200])
        return False


async def generate_voice_preview(voice_id: str, preview_text: str = "") -> dict:
    """
    Generate a TTS preview to let the user hear how the voice sounds.

    Args:
        voice_id: HeyGen voice ID.
        preview_text: Text to speak. Defaults to a standard test phrase.

    Returns:
        dict with audio_url, duration.
    """
    if not preview_text:
        preview_text = (
            "Oi, tudo bem? Esse e um preview da minha voz clonada. "
            "Estou testando pra ver se ficou legal. O que voce acha?"
        )

    return await text_to_speech(text=preview_text, voice_id=voice_id)


# ──────────────────────────────────────────────
# Text-to-Speech
# ──────────────────────────────────────────────

async def text_to_speech(
    text: str,
    voice_id: str,
    speed: float = 1.0,
) -> dict:
    """
    Generate speech audio from text using a HeyGen voice.

    Returns:
        dict with audio_url, duration, word_timestamps.
    """
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{API_BASE}/v1/audio/text_to_speech",
            headers=_headers(),
            json={
                "text": text,
                "voice_id": voice_id,
                "speed": str(speed),
            },
        )
        resp.raise_for_status()
        result = resp.json()

    if result.get("error"):
        raise RuntimeError(f"HeyGen TTS failed: {result['error']}")

    data = result.get("data", {})
    logger.info("HeyGen TTS generated: duration=%.1fs, voice=%s", data.get("duration", 0), voice_id)
    return data


# ──────────────────────────────────────────────
# Video Generation (Multi-Scene)
# ──────────────────────────────────────────────

def _build_video_input(
    talking_photo_id: str,
    voice_id: str = "",
    narration: str = "",
    background: dict | None = None,
    emotion: str = "Friendly",
    speed: float = 1.0,
    use_avatar_iv: bool = True,
    audio_url: str = "",
) -> dict:
    """
    Build a single scene (video_input) for the HeyGen video generation API.

    Args:
        talking_photo_id: Photo avatar look ID.
        voice_id: HeyGen voice ID (used when audio_url is empty).
        narration: Text the avatar will speak (used when audio_url is empty).
        background: {"type": "color|image|video", "value": "#hex or URL"}.
        emotion: Voice emotion (only for text mode).
        speed: Voice speed (only for text mode).
        use_avatar_iv: Use Avatar IV model.
        audio_url: Pre-generated audio URL (e.g. ElevenLabs). If provided, uses audio mode instead of text.
    """
    bg = background or {"type": "color", "value": "#000000"}

    character = {
        "type": "talking_photo",
        "talking_photo_id": talking_photo_id,
        "avatar_style": "normal",
    }
    if use_avatar_iv:
        character["use_avatar_iv_model"] = True

    # Voice: use pre-generated audio if available, otherwise text-to-speech
    if audio_url:
        voice_config = {
            "type": "audio",
            "audio_url": audio_url,
        }
    else:
        voice_config = {
            "type": "text",
            "voice_id": voice_id,
            "input_text": narration,
            "speed": speed,
            "emotion": emotion,
            "locale": "pt-BR",
        }

    scene = {
        "character": character,
        "voice": voice_config,
        "background": {},
    }

    # Background configuration
    bg_type = bg.get("type", "color")
    bg_url = bg.get("value", bg.get("url", ""))

    if bg_type == "image" and (not bg_url or not bg_url.startswith("http")):
        # Fallback: image background without real URL → use color
        logger.warning("Image background without URL, falling back to color: %s", bg)
        bg_type = "color"
        bg_url = bg.get("value", "#1a1a2e") if bg.get("value", "").startswith("#") else "#1a1a2e"

    if bg_type == "video" and (not bg_url or not bg_url.startswith("http")):
        logger.warning("Video background without URL, falling back to color: %s", bg)
        bg_type = "color"
        bg_url = "#1a1a2e"

    if bg_type == "color":
        scene["background"] = {
            "type": "color",
            "value": bg_url if bg_url.startswith("#") else "#000000",
        }
    elif bg_type == "image":
        scene["background"] = {
            "type": "image",
            "url": bg_url,
        }
    elif bg_type == "video":
        scene["background"] = {
            "type": "video",
            "url": bg_url,
            "play_style": bg.get("play_style", "fit_to_scene"),
        }

    return scene


async def generate_video(
    scenes: list[dict],
    talking_photo_id: str,
    voice_id: str,
    title: str = "",
    width: int = 1080,
    height: int = 1920,
) -> str:
    """
    Generate a multi-scene avatar video.

    Args:
        scenes: List of scene dicts, each with:
            - narration (str): Text the avatar speaks in this scene.
            - background (dict): {"type": "color|image|video", "value": "..."}.
            - emotion (str): Voice emotion for this scene.
            - speed (float): Voice speed for this scene.
        talking_photo_id: Photo avatar look ID.
        voice_id: HeyGen voice ID.
        title: Optional video title.
        width: Video width (1080 for 9:16).
        height: Video height (1920 for 9:16).

    Returns:
        video_id for status polling.
    """
    video_inputs = []
    for scene in scenes:
        video_input = _build_video_input(
            talking_photo_id=talking_photo_id,
            voice_id=voice_id,
            narration=scene.get("narration", ""),
            background=scene.get("background"),
            emotion=scene.get("emotion", "Friendly"),
            speed=scene.get("speed", 1.0),
            audio_url=scene.get("audio_url", ""),
        )
        video_inputs.append(video_input)

    payload = {
        "video_inputs": video_inputs,
        "dimension": {"width": width, "height": height},
    }
    if title:
        payload["title"] = title

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{API_BASE}/v2/video/generate",
            headers=_headers(),
            json=payload,
        )
        if resp.status_code != 200:
            logger.error("HeyGen video generate failed: status=%d body=%s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
        result = resp.json()

    if result.get("error"):
        raise RuntimeError(f"HeyGen video generation failed: {result['error']}")

    video_id = result.get("data", {}).get("video_id", "")
    logger.info("HeyGen video generation started: video_id=%s, scenes=%d", video_id, len(scenes))
    return video_id


# ──────────────────────────────────────────────
# Video Status Polling
# ──────────────────────────────────────────────

async def get_video_status(video_id: str) -> dict:
    """
    Check video generation status.

    Returns:
        dict with status, video_url, duration, thumbnail_url, etc.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{API_BASE}/v1/video_status.get",
            headers=_headers(),
            params={"video_id": video_id},
        )
        resp.raise_for_status()
        result = resp.json()

    return result.get("data", {})


async def wait_for_video(
    video_id: str,
    max_attempts: int = 180,
    interval_s: int = 10,
    on_progress: callable = None,
) -> dict:
    """
    Poll until video generation completes.

    Args:
        video_id: From generate_video().
        max_attempts: Max polling iterations.
        interval_s: Seconds between polls.
        on_progress: Optional callback(status_str) for progress updates.

    Returns:
        dict with video_url, duration, thumbnail_url.

    Raises:
        RuntimeError if video fails or times out.
    """
    for attempt in range(max_attempts):
        data = await get_video_status(video_id)
        status = data.get("status", "unknown")

        logger.info("Video poll %d/%d: status=%s", attempt + 1, max_attempts, status)

        if on_progress:
            on_progress(status)

        if status == "completed":
            logger.info("HeyGen video ready: url=%s, duration=%s",
                        data.get("video_url", "")[:80], data.get("duration"))
            return data

        if status == "failed":
            error = data.get("error", {})
            raise RuntimeError(f"HeyGen video failed: {error}")

        await asyncio.sleep(interval_s)

    raise RuntimeError(f"HeyGen video timed out after {max_attempts * interval_s}s")


# ──────────────────────────────────────────────
# Digital Twin (required for Seedance 2.0)
# ──────────────────────────────────────────────

async def create_digital_twin(
    video_url: str,
    consent_video_url: str,
    avatar_name: str = "Meu Digital Twin",
) -> str:
    """
    Submit a video to create a Digital Twin avatar.
    Required for Avatar Shots / Seedance 2.0.

    NOTE: This endpoint requires Enterprise/Scale API plan.
    If 403, user should create Digital Twin via app.heygen.com and link via manage_digital_twin.

    Returns:
        avatar_id of the Digital Twin, or raises RuntimeError with guidance.
    """
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{API_BASE}/v2/video_avatar",
            headers=_headers(),
            json={
                "training_footage_url": video_url,
                "video_consent_url": consent_video_url,
                "avatar_name": avatar_name,
            },
        )
        if resp.status_code == 403:
            raise RuntimeError(
                "PLANO_SEM_ACESSO: A criacao de Digital Twin pela API requer plano Scale/Enterprise. "
                "Crie pelo site app.heygen.com e vincule o ID pelo Teq."
            )
        if resp.status_code != 200:
            logger.error("Digital Twin creation failed: %s %s", resp.status_code, resp.text[:300])
            resp.raise_for_status()
        result = resp.json()

    avatar_id = result.get("data", {}).get("avatar_id", "")
    logger.info("Digital Twin creation started: avatar_id=%s", avatar_id)
    return avatar_id


async def check_digital_twin_status(avatar_id: str) -> dict:
    """Check Digital Twin training status. Returns dict with status field."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{API_BASE}/v2/video_avatars/{avatar_id}/status",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json().get("data", {})


# ──────────────────────────────────────────────
# Video Agent (Seedance 2.0 / Avatar Shots)
# ──────────────────────────────────────────────

async def generate_video_agent(
    prompt: str,
    avatar_id: str,
    duration_sec: int = 10,
    orientation: str = "portrait",
) -> str:
    """
    Generate a cinematic video using Seedance 2.0 Avatar Shots.
    Requires a Digital Twin avatar (not photo avatar).

    Args:
        prompt: Cinematic scene description.
            Good: "Golden hour light, presenter walking through modern office, slow dolly-in"
            Bad: "a person talking"
        avatar_id: Digital Twin avatar ID.
        duration_sec: 5-15 seconds per clip.
        orientation: "portrait" (9:16) or "landscape" (16:9).

    Returns:
        video_id for status polling.
    """
    payload = {
        "prompt": prompt,
        "config": {
            "avatar_id": avatar_id,
            "duration_sec": duration_sec,
            "orientation": orientation,
        },
    }

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{API_BASE}/v1/video_agent/generate",
            headers=_headers(),
            json=payload,
        )
        if resp.status_code != 200:
            logger.error("Video Agent failed: %s %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
        result = resp.json()

    if result.get("error"):
        raise RuntimeError(f"Video Agent failed: {result['error']}")

    video_id = result.get("data", {}).get("video_id", "")
    logger.info("Video Agent (Seedance) started: video_id=%s", video_id)
    return video_id


async def generate_seedance_multi_scene(
    scenes: list[dict],
    avatar_id: str,
    orientation: str = "portrait",
) -> list[dict]:
    """
    Generate multiple Seedance clips for a multi-scene video.
    Each scene generates a 5-15s clip. Results can be stitched later.

    Args:
        scenes: List of dicts with:
            - prompt (str): Cinematic scene description.
            - narration (str): What the avatar says (included in prompt).
            - duration_sec (int): 5-15 seconds.
        avatar_id: Digital Twin avatar ID.

    Returns:
        List of dicts with video_id and status for each scene.
    """
    results = []
    for i, scene in enumerate(scenes):
        # Build cinematic prompt combining visual description + narration
        prompt_parts = []
        if scene.get("prompt"):
            prompt_parts.append(scene["prompt"])
        if scene.get("narration"):
            prompt_parts.append(f'The presenter says: "{scene["narration"]}"')

        full_prompt = ". ".join(prompt_parts)
        duration = min(15, max(5, scene.get("duration_sec", 10)))

        try:
            video_id = await generate_video_agent(
                prompt=full_prompt,
                avatar_id=avatar_id,
                duration_sec=duration,
                orientation=orientation,
            )
            results.append({"scene": i, "video_id": video_id, "status": "submitted"})
            logger.info("Seedance scene %d/%d submitted: %s", i + 1, len(scenes), video_id)
        except Exception as e:
            logger.error("Seedance scene %d failed: %s", i + 1, e)
            results.append({"scene": i, "video_id": "", "status": "failed", "error": str(e)})

        # Stagger requests (avoid rate limits)
        if i < len(scenes) - 1:
            await asyncio.sleep(5)

    return results


def estimate_seedance_cost_cents(duration_s: float) -> int:
    """Seedance costs 4 credits per second. 1 credit ≈ $1."""
    return int(duration_s * 4 * 100)  # 4 credits/s * 100 cents/credit


# ──────────────────────────────────────────────
# Full Avatar Setup Flow
# ──────────────────────────────────────────────

async def setup_full_avatar(
    photo_urls: list[str],
    avatar_name: str = "Meu Avatar",
    train: bool = True,
) -> dict:
    """
    Complete avatar setup: upload photos → create group → add looks → train.

    Args:
        photo_urls: List of 1-4 photo URLs (Cloudinary or any public URL).
        avatar_name: Name for the avatar group.
        train: Whether to start training immediately.

    Returns:
        dict with group_id, avatar_looks, flow_id (if training).
    """
    if not photo_urls:
        raise ValueError("At least one photo URL is required")

    # Step 1: Upload all photos to HeyGen
    logger.info("Uploading %d photos to HeyGen...", len(photo_urls))
    uploaded = []
    for url in photo_urls:
        asset_data = await upload_image_from_url(url)
        uploaded.append(asset_data)

    # Step 2: Create avatar group with the first photo
    first_image_key = uploaded[0].get("image_key", "")
    if not first_image_key:
        raise RuntimeError("First photo upload did not return image_key")

    group_data = await create_avatar_group(name=avatar_name, image_key=first_image_key)
    group_id = group_data.get("group_id", "")

    # Step 3: Add remaining photos as additional looks
    if len(uploaded) > 1:
        extra_keys = [u["image_key"] for u in uploaded[1:] if u.get("image_key")]
        if extra_keys:
            await add_looks_to_group(group_id=group_id, image_keys=extra_keys, name="uploaded_look")

    result = {
        "group_id": group_id,
        "avatar_id": group_data.get("id", ""),
        "image_url": group_data.get("image_url", ""),
        "uploaded_count": len(uploaded),
        "image_keys": [u.get("image_key", "") for u in uploaded],
    }

    # Step 4: Train the group
    if train:
        flow_id = await train_avatar_group(group_id)
        result["flow_id"] = flow_id
        result["training_status"] = "started"

    return result


# ──────────────────────────────────────────────
# Cost Estimation
# ──────────────────────────────────────────────

def estimate_video_cost_cents(duration_s: float) -> int:
    """Estimate cost in cents for a HeyGen video. Standard: ~1 credit/min ≈ $1/min."""
    minutes = max(duration_s / 60, 1 / 60)  # Minimum billing
    return int(minutes * 100)  # $1/min = 100 cents/min


def estimate_tts_cost_cents(text_length: int) -> int:
    """TTS cost is included in video credits, minimal standalone cost."""
    return 0

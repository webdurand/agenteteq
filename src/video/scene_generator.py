"""
B-roll scene generation via Kling AI API.
Generates short video clips (5-10s) from text prompts.
"""

import asyncio
import os
import time
import logging

import httpx
import jwt

logger = logging.getLogger(__name__)

KLING_API_URL = "https://api.klingai.com"


def _get_kling_token() -> str:
    """Generate JWT token for Kling API authentication."""
    access_key = os.getenv("KLING_API_KEY")
    secret_key = os.getenv("KLING_API_SECRET")

    if not access_key or not secret_key:
        raise ValueError("KLING_API_KEY and KLING_API_SECRET not configured in .env")

    now = int(time.time())
    payload = {
        "iss": access_key,
        "exp": now + 1800,
        "nbf": now - 5,
    }
    return jwt.encode(payload, secret_key, algorithm="HS256",
                      headers={"typ": "JWT", "alg": "HS256"})


async def generate_broll(
    prompt: str,
    duration: int = 5,
    aspect_ratio: str = "9:16",
    user_id: str = "",
    channel: str = "web",
) -> str:
    """
    Generate a B-roll video clip from a text prompt using Kling AI.

    Args:
        prompt: Description of the scene to generate.
        duration: Duration in seconds (5 or 10).
        aspect_ratio: Video ratio. "9:16" for vertical Reels, "16:9" for horizontal.
        user_id: For cost tracking.
        channel: For cost tracking.

    Returns:
        URL of the generated video clip (MP4).
    """
    token = _get_kling_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    duration_str = str(max(5, min(10, duration)))

    payload = {
        "prompt": prompt,
        "negative_prompt": "blurry, low quality, distorted, watermark, text overlay",
        "model_name": "kling-v1",
        "mode": "std",
        "duration": duration_str,
        "aspect_ratio": aspect_ratio,
        "cfg_scale": 0.5,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{KLING_API_URL}/v1/videos/text2video",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(f"Kling API error: {data.get('message', data)}")

        task_id = data["data"]["task_id"]

    logger.info("Kling task created: %s (prompt: %s...)", task_id, prompt[:50])

    # Poll for completion
    video_url = await _poll_task_status(task_id)

    # Track cost
    _log_cost(user_id, channel, task_id, int(duration_str), prompt)

    return video_url


async def generate_multiple_brolls(
    prompts: list[str],
    duration: int = 5,
    aspect_ratio: str = "9:16",
    user_id: str = "",
    channel: str = "web",
) -> list[str]:
    """
    Generate multiple B-roll clips in parallel.

    Args:
        prompts: List of scene descriptions.
        duration: Duration per clip.
        aspect_ratio: Video ratio.
        user_id: For cost tracking.
        channel: For cost tracking.

    Returns:
        List of video URLs (in same order as prompts). Failed ones are empty strings.
    """
    tasks = [
        generate_broll(prompt, duration, aspect_ratio, user_id, channel)
        for prompt in prompts
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    urls = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error("B-roll generation failed for prompt %d: %s", i, result)
            urls.append("")
        else:
            urls.append(result)

    return urls


async def _poll_task_status(
    task_id: str,
    max_attempts: int = 120,
    interval_s: int = 10,
) -> str:
    """Poll Kling API until task succeeds or fails."""
    token = _get_kling_token()
    headers = {
        "Authorization": f"Bearer {token}",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(max_attempts):
            await asyncio.sleep(interval_s)

            # Refresh token periodically (expires in 30 min)
            if attempt % 12 == 0 and attempt > 0:
                headers["Authorization"] = f"Bearer {_get_kling_token()}"

            resp = await client.get(
                f"{KLING_API_URL}/v1/videos/text2video/{task_id}",
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                raise RuntimeError(f"Kling poll error: {data.get('message', data)}")

            task_data = data.get("data", {})
            status = task_data.get("task_status", "")

            if status == "succeed":
                videos = task_data.get("task_result", {}).get("videos", [])
                if videos:
                    url = videos[0].get("url", "")
                    logger.info("Kling task done: %s", task_id)
                    return url
                raise RuntimeError(f"Kling task succeed but no videos: {task_data}")

            if status == "failed":
                reason = task_data.get("task_status_msg", str(task_data))
                raise RuntimeError(f"Kling task failed: {reason}")

            logger.debug("Kling task %s status: %s (attempt %d)", task_id, status, attempt + 1)

    raise TimeoutError(f"Kling task {task_id} timed out after {max_attempts * interval_s}s")


def _log_cost(user_id: str, channel: str, task_id: str, duration: int, prompt: str):
    """Track Kling cost. ~$0.07-0.14 per 5 seconds of video."""
    if not user_id:
        return
    try:
        from src.memory.analytics import log_event
        # Standard mode: ~$0.14 per 5s clip
        cost = 0.14 * (duration / 5)
        log_event(
            user_id=user_id,
            channel=channel,
            event_type="video_broll",
            tool_name="kling",
            status="success",
            extra_data={
                "task_id": task_id,
                "duration_seconds": duration,
                "prompt": prompt[:100],
                "cost_usd": round(cost, 4),
            },
        )
    except Exception as e:
        logger.error("Failed to log Kling cost: %s", e)

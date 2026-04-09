"""
Kling Avatar v2 Pro lip-sync via fal.ai.
Takes a portrait photo + narration audio → generates video with natural lip-sync.

Endpoint: fal-ai/kling-video/ai-avatar/v2/pro
Pricing: ~$0.115/second (pro), ~$0.056/second (std)
Max: 5 minutes, 1080p, 48fps
"""

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

FAL_QUEUE_URL = "https://queue.fal.run"
FAL_MODEL_PRO = "fal-ai/kling-video/ai-avatar/v2/pro"
FAL_MODEL_STD = "fal-ai/kling-video/ai-avatar/v2/standard"


def _get_fal_headers() -> dict:
    key = os.getenv("FAL_KEY")
    if not key:
        raise ValueError("FAL_KEY not configured in .env")
    return {
        "Authorization": f"Key {key}",
        "Content-Type": "application/json",
    }


async def generate_lipsync_video(
    image_url: str,
    audio_url: str,
    mode: str = "",
) -> str:
    """
    Generate a lip-synced talking head video using Kling Avatar v2 Pro via fal.ai.

    Args:
        image_url: URL of the portrait photo (Cloudinary or any public URL).
        audio_url: URL of the narration audio (MP3/WAV).
        mode: "pro" (default) or "std" — pro has better quality, std is cheaper.

    Returns:
        URL of the generated lip-synced video (MP4).
    """
    if not mode:
        mode = os.getenv("KLING_AVATAR_MODE", "pro")
    model = FAL_MODEL_PRO if mode == "pro" else FAL_MODEL_STD
    headers = _get_fal_headers()

    payload = {
        "image_url": image_url,
        "audio_url": audio_url,
    }

    logger.info("Submitting lip-sync job: model=%s, image=%s, audio=%s",
                model, image_url[:60], audio_url[:60])

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Submit to queue
        resp = await client.post(
            f"{FAL_QUEUE_URL}/{model}",
            headers=headers,
            json=payload,
        )
        if resp.status_code >= 400:
            logger.error("fal.ai submit error %d: %s", resp.status_code, resp.text[:300])
        resp.raise_for_status()
        data = resp.json()
        request_id = data.get("request_id")
        if not request_id:
            raise RuntimeError(f"fal.ai did not return request_id: {data}")

        # Use pre-built URLs from response (more reliable than constructing manually)
        status_url = data.get("status_url", f"{FAL_QUEUE_URL}/{model}/requests/{request_id}/status")
        response_url = data.get("response_url", f"{FAL_QUEUE_URL}/{model}/requests/{request_id}")

    logger.info("Lip-sync job submitted: request_id=%s, status_url=%s", request_id, status_url)

    # Poll for completion
    video_url = await _poll_fal_status(status_url, response_url, request_id)
    return video_url


async def _poll_fal_status(
    status_url: str,
    response_url: str,
    request_id: str,
    max_attempts: int = 180,
    interval_s: int = 10,
) -> str:
    """Poll fal.ai queue until job completes or fails."""
    headers = _get_fal_headers()
    # Don't send Content-Type for GET requests
    poll_headers = {"Authorization": headers["Authorization"]}

    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(max_attempts):
            await asyncio.sleep(interval_s)

            # Try status URL first, fall back to response URL
            try:
                resp = await client.get(status_url, headers=poll_headers)
                if resp.status_code == 405:
                    # Some fal.ai models don't support /status — poll result URL directly
                    logger.info("Status URL returned 405, switching to response URL polling")
                    resp = await client.get(response_url, headers=poll_headers)

                if resp.status_code == 202:
                    # Still in progress (202 Accepted)
                    status_data = resp.json()
                    status = status_data.get("status", "IN_QUEUE")
                    logger.debug("fal.ai lip-sync %s: status=%s (attempt %d/%d)",
                                 request_id, status, attempt + 1, max_attempts)
                    continue

                resp.raise_for_status()
                result_data = resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 202:
                    continue
                logger.warning("fal.ai poll error (attempt %d): %s", attempt + 1, e)
                continue
            except Exception as e:
                logger.warning("fal.ai poll error (attempt %d): %s", attempt + 1, e)
                continue

            # Check if we got a completed result
            status = result_data.get("status", "")

            if status == "COMPLETED" or "video" in result_data:
                # Status endpoint returns COMPLETED but no video — fetch from response_url
                video = result_data.get("video", {})
                url = video.get("url", "")
                if not url:
                    logger.info("Status COMPLETED, fetching result from response_url...")
                    try:
                        result_resp = await client.get(response_url, headers=poll_headers)
                        result_resp.raise_for_status()
                        full_result = result_resp.json()
                        video = full_result.get("video", {})
                        url = video.get("url", "")
                    except Exception as e:
                        logger.warning("Failed to fetch response_url: %s", e)
                if not url:
                    raise RuntimeError(f"fal.ai completed but no video URL: {result_data}")
                logger.info("Lip-sync job done: %s (url: %s...)", request_id, url[:80])
                return url

            if status == "FAILED":
                error = result_data.get("error", str(result_data))
                raise RuntimeError(f"fal.ai lip-sync failed: {error}")

            logger.debug("fal.ai lip-sync %s: status=%s (attempt %d/%d)",
                         request_id, status, attempt + 1, max_attempts)

    raise TimeoutError(
        f"fal.ai lip-sync timed out after {max_attempts * interval_s}s (request_id={request_id})"
    )


def estimate_lipsync_cost_cents(duration_s: int, mode: str = "") -> int:
    """Estimate cost in cents for a lip-sync video."""
    if not mode:
        mode = os.getenv("KLING_AVATAR_MODE", "pro")
    rate_per_second = 0.115 if mode == "pro" else 0.056
    return int(duration_s * rate_per_second * 100)

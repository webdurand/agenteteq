"""
Kling O1 Reference-to-Video via fal.ai.
Generates cinematographic video clips with character consistency using Subject Binding.
The user's identity is maintained across scenes via Element Library (3-4 reference photos).

Endpoint: fal-ai/kling-video/o1/reference-to-video
Pricing: ~$0.56/5s, ~$1.12/10s
"""

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

FAL_QUEUE_URL = "https://queue.fal.run"
FAL_MODEL = "fal-ai/kling-video/o1/reference-to-video"


def _get_fal_headers() -> dict:
    key = os.getenv("FAL_KEY")
    if not key:
        raise ValueError("FAL_KEY not configured in .env")
    return {
        "Authorization": f"Key {key}",
        "Content-Type": "application/json",
    }


async def generate_reference_clip(
    prompt: str,
    elements: list[dict],
    duration: str = "5",
    aspect_ratio: str = "9:16",
) -> str:
    """
    Generate a cinematographic video clip with character consistency via Subject Binding.

    Args:
        prompt: Scene description using @Element1 reference.
            Example: "@Element1 walks confidently through a modern office, golden hour lighting"
        elements: List of element dicts, each with:
            - frontal_image_url: Front-facing photo URL (required)
            - reference_image_urls: List of 1-3 additional angle URLs (optional)
        duration: "5" or "10" seconds
        aspect_ratio: "9:16" (vertical), "16:9" (horizontal), "1:1" (square)

    Returns:
        URL of the generated video clip (MP4).
    """
    headers = _get_fal_headers()

    payload = {
        "prompt": prompt,
        "elements": elements,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
    }

    logger.info("Submitting reference-to-video job: prompt=%s..., elements=%d, duration=%s",
                prompt[:80], len(elements), duration)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{FAL_QUEUE_URL}/{FAL_MODEL}",
            headers=headers,
            json=payload,
        )
        if resp.status_code >= 400:
            logger.error("fal.ai reference submit error %d: %s", resp.status_code, resp.text[:500])
        resp.raise_for_status()
        data = resp.json()

        request_id = data.get("request_id")
        if not request_id:
            raise RuntimeError(f"fal.ai did not return request_id: {data}")

        status_url = data.get("status_url", f"{FAL_QUEUE_URL}/{FAL_MODEL}/requests/{request_id}/status")
        response_url = data.get("response_url", f"{FAL_QUEUE_URL}/{FAL_MODEL}/requests/{request_id}")

    logger.info("Reference-to-video job submitted: request_id=%s", request_id)

    video_url = await _poll_fal_result(status_url, response_url, request_id)
    return video_url


async def generate_multiple_reference_clips(
    scenes: list[dict],
    elements: list[dict],
    aspect_ratio: str = "9:16",
    user_id: str = "",
    channel: str = "web",
) -> dict[str, str]:
    """
    Generate multiple scene clips with character consistency.
    Stagger: 2 at a time with 5s delay between batches.

    Args:
        scenes: List of dicts with keys: name, prompt, duration
        elements: Element dicts (same for all scenes)
        aspect_ratio: Video aspect ratio

    Returns:
        Dict mapping scene name → video URL. Failed scenes have empty string.
    """
    BATCH_SIZE = 2
    BATCH_DELAY_S = 5.0

    async def _generate_one(scene: dict) -> tuple[str, str]:
        name = scene["name"]
        try:
            url = await generate_reference_clip(
                prompt=scene["prompt"],
                elements=elements,
                duration=str(scene.get("duration", 5)),
                aspect_ratio=aspect_ratio,
            )
            logger.info("Reference clip done: %s", name)
            return name, url
        except Exception as e:
            logger.warning("Reference clip failed for '%s': %s", name, e)
            return name, ""

    all_results = []
    total_batches = (len(scenes) + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, len(scenes), BATCH_SIZE):
        batch = scenes[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1

        if i > 0:
            logger.info("Reference stagger: waiting %.1fs before batch %d/%d",
                        BATCH_DELAY_S, batch_num, total_batches)
            await asyncio.sleep(BATCH_DELAY_S)

        batch_tasks = [_generate_one(scene) for scene in batch]
        batch_results = await asyncio.gather(*batch_tasks)
        all_results.extend(batch_results)

        done = sum(1 for _, url in all_results if url)
        logger.info("Reference progress: %d/%d scenes done", done, len(scenes))

    return dict(all_results)


async def _poll_fal_result(
    status_url: str,
    response_url: str,
    request_id: str,
    max_attempts: int = 180,
    interval_s: int = 10,
) -> str:
    """Poll fal.ai queue until job completes or fails."""
    poll_headers = {"Authorization": _get_fal_headers()["Authorization"]}

    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(max_attempts):
            await asyncio.sleep(interval_s)

            try:
                resp = await client.get(status_url, headers=poll_headers)

                if resp.status_code == 405:
                    resp = await client.get(response_url, headers=poll_headers)

                if resp.status_code == 202:
                    if attempt % 6 == 5:  # Log every ~60s
                        logger.info("Reference job %s still processing (attempt %d)",
                                    request_id[:12], attempt + 1)
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

            status = result_data.get("status", "")

            if status == "COMPLETED" or "video" in result_data:
                video = result_data.get("video", {})
                url = video.get("url", "")

                if not url:
                    # Status endpoint returned COMPLETED but no video — fetch from response_url
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

                logger.info("Reference job done: %s (url: %s...)", request_id[:12], url[:80])
                return url

            if status == "FAILED":
                error = result_data.get("error", str(result_data))
                raise RuntimeError(f"fal.ai reference-to-video failed: {error}")

    raise TimeoutError(
        f"fal.ai reference-to-video timed out after {max_attempts * interval_s}s (request_id={request_id})"
    )


def estimate_reference_cost_cents(duration: int) -> int:
    """Estimate cost in cents for one reference-to-video clip."""
    if duration <= 5:
        return 56  # $0.56
    return 112  # $1.12 for 10s

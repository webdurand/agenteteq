"""
Kling AI Image-to-Video provider.
Uses POST /v1/videos/image2video for realistic person-in-scene generation.
Reuses the JWT auth pattern from scene_generator.py.
"""

import asyncio
import logging
import os
import time

import httpx
import jwt

from src.video.providers.base import VideoProvider

logger = logging.getLogger(__name__)

KLING_API_URL = "https://api.klingai.com"


class KlingProvider(VideoProvider):
    """Kling AI Image-to-Video — generates realistic clips of a person in different scenarios."""

    @property
    def name(self) -> str:
        return "kling"

    def estimate_cost_cents(self, duration: int) -> int:
        """Cost depends on mode: std ~$0.14/5s, pro ~$0.49/5s."""
        mode = os.getenv("KLING_MODE", "std")
        if mode == "pro":
            return 49 if duration <= 5 else 90
        return 14 if duration <= 5 else 28

    async def generate_clip(
        self,
        prompt: str,
        reference_image_base64: str,
        duration: int = 5,
        aspect_ratio: str = "9:16",
        camera_control: dict | None = None,
    ) -> str:
        """Generate a single clip via Kling Image-to-Video API."""
        token = _get_kling_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        # Kling v3 supports 3-15 seconds; v1/v2 only 5 or 10
        duration = max(3, min(15, duration))
        duration_str = str(duration)

        payload = {
            "model_name": os.getenv("KLING_MODEL", "kling-v3"),
            "mode": os.getenv("KLING_MODE", "std"),
            "image": reference_image_base64,
            "prompt": prompt,
            "negative_prompt": (
                "blurry, low quality, distorted, watermark, text overlay, "
                "deformed face, extra limbs, disfigured, bad anatomy"
            ),
            "duration": duration_str,
            "aspect_ratio": aspect_ratio,
            "cfg_scale": 0.5,
        }

        if camera_control:
            payload["camera_control"] = camera_control

        # Retry with exponential backoff for rate limits (429)
        MAX_RETRIES = 4
        task_id = None

        async with httpx.AsyncClient(timeout=60.0) as client:
            for attempt in range(MAX_RETRIES):
                resp = await client.post(
                    f"{KLING_API_URL}/v1/videos/image2video",
                    headers=headers,
                    json=payload,
                )

                if resp.status_code == 429:
                    wait = (attempt + 1) * 5  # 5s, 10s, 15s, 20s
                    logger.warning(
                        "Kling I2V rate limited (429), retry %d/%d in %ds",
                        attempt + 1, MAX_RETRIES, wait,
                    )
                    await asyncio.sleep(wait)
                    # Refresh token in case it expired
                    headers["Authorization"] = f"Bearer {_get_kling_token()}"
                    continue

                if resp.status_code >= 400:
                    logger.error("Kling I2V error %d: %s", resp.status_code, resp.text[:300])
                resp.raise_for_status()

                data = resp.json()
                if data.get("code") != 0:
                    raise RuntimeError(f"Kling I2V API error: {data.get('message', data)}")

                task_id = data["data"]["task_id"]
                break
            else:
                raise RuntimeError(f"Kling I2V failed after {MAX_RETRIES} retries (rate limited)")

        logger.info("Kling I2V task created: %s (prompt: %s...)", task_id, prompt[:60])

        video_url = await _poll_task_status(task_id, endpoint_type="image2video")
        return video_url

    async def generate_multiple_clips(
        self,
        scenes: list[dict],
        reference_image_base64: str,
        aspect_ratio: str = "9:16",
        user_id: str = "",
        channel: str = "web",
    ) -> dict[str, str]:
        """Generate multiple I2V clips in parallel with per-scene fallback."""

        async def _generate_one(scene: dict) -> tuple[str, str]:
            name = scene["name"]
            try:
                url = await self.generate_clip(
                    prompt=scene["prompt"],
                    reference_image_base64=reference_image_base64,
                    duration=scene.get("duration", 5),
                    aspect_ratio=aspect_ratio,
                    camera_control=scene.get("camera_control"),
                )
                _log_cost(user_id, channel, name, scene.get("duration", 5), scene["prompt"])
                return name, url
            except Exception as e:
                logger.warning("Kling I2V failed for scene '%s': %s", name, e)
                # Fallback: try text-to-video B-roll if broll_prompt exists
                broll_prompt = scene.get("broll_prompt", "")
                if broll_prompt:
                    try:
                        from src.video.scene_generator import generate_broll
                        url = await generate_broll(
                            prompt=broll_prompt,
                            duration=scene.get("duration", 5),
                            aspect_ratio=aspect_ratio,
                            user_id=user_id,
                            channel=channel,
                        )
                        logger.info("Fallback T2V succeeded for scene '%s'", name)
                        return name, url
                    except Exception as e2:
                        logger.warning("Fallback T2V also failed for '%s': %s", name, e2)
                return name, ""

        # Stagger: send max 2 at a time with 5s delay between batches
        BATCH_SIZE = 2
        BATCH_DELAY_S = 5.0
        all_results = []

        for i in range(0, len(scenes), BATCH_SIZE):
            batch = scenes[i:i + BATCH_SIZE]
            if i > 0:
                logger.info("Kling stagger: waiting %.1fs before next batch (%d/%d)",
                            BATCH_DELAY_S, i // BATCH_SIZE + 1,
                            (len(scenes) + BATCH_SIZE - 1) // BATCH_SIZE)
                await asyncio.sleep(BATCH_DELAY_S)
            batch_tasks = [_generate_one(scene) for scene in batch]
            batch_results = await asyncio.gather(*batch_tasks)
            all_results.extend(batch_results)

        return dict(all_results)


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


async def _poll_task_status(
    task_id: str,
    endpoint_type: str = "image2video",
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
                f"{KLING_API_URL}/v1/videos/{endpoint_type}/{task_id}",
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                raise RuntimeError(f"Kling poll error: {data.get('message', data)}")

            task_data = data.get("data", {})
            status = task_data.get("task_status", "")

            if status in ("succeed", "completed"):
                videos = task_data.get("task_result", {}).get("videos", [])
                if videos:
                    url = videos[0].get("url", "")
                    logger.info("Kling I2V task done: %s", task_id)
                    return url
                raise RuntimeError(f"Kling task completed but no videos: {task_data}")

            if status == "failed":
                reason = task_data.get("task_status_msg", str(task_data))
                raise RuntimeError(f"Kling I2V task failed: {reason}")

            logger.debug(
                "Kling I2V task %s status: %s (attempt %d/%d)",
                task_id, status, attempt + 1, max_attempts,
            )

    raise TimeoutError(f"Kling I2V task {task_id} timed out after {max_attempts * interval_s}s")


def _log_cost(user_id: str, channel: str, scene_name: str, duration: int, prompt: str):
    """Track Kling I2V cost."""
    if not user_id:
        return
    try:
        from src.memory.analytics import log_event
        cost = 0.49 if duration <= 5 else 0.90
        log_event(
            user_id=user_id,
            channel=channel,
            event_type="video_scene_i2v",
            tool_name="kling-i2v",
            status="success",
            extra_data={
                "scene_name": scene_name,
                "duration_seconds": duration,
                "prompt": prompt[:100],
                "cost_usd": round(cost, 4),
            },
        )
    except Exception as e:
        logger.error("Failed to log Kling I2V cost: %s", e)

"""
Abstract interface for video generation providers.
Swap providers by changing VIDEO_PROVIDER env var — zero pipeline changes.
"""

from abc import ABC, abstractmethod


class VideoProvider(ABC):
    """Interface for image-to-video generation providers (Kling, Runway, Vidu, etc.)."""

    @abstractmethod
    async def generate_clip(
        self,
        prompt: str,
        reference_image_base64: str,
        duration: int = 5,
        aspect_ratio: str = "9:16",
        camera_control: dict | None = None,
    ) -> str:
        """
        Generate a video clip from a reference image + text prompt.

        Args:
            prompt: Scene description (person + action + scenario + lighting + camera angle).
            reference_image_base64: Base64-encoded reference image of the person.
            duration: Clip duration in seconds (provider may round to nearest supported value).
            aspect_ratio: Video aspect ratio ("9:16" for vertical, "16:9" for horizontal).
            camera_control: Provider-specific camera movement parameters.

        Returns:
            URL of the generated video clip (MP4).
        """

    @abstractmethod
    def estimate_cost_cents(self, duration: int) -> int:
        """Estimate cost in cents for generating one clip of the given duration."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name for logging and cost tracking (e.g., 'kling', 'runway', 'vidu')."""

    @abstractmethod
    async def generate_multiple_clips(
        self,
        scenes: list[dict],
        reference_image_base64: str,
        aspect_ratio: str = "9:16",
        user_id: str = "",
        channel: str = "web",
    ) -> dict[str, str]:
        """
        Generate multiple scene clips in parallel.

        Args:
            scenes: List of dicts with keys: name, prompt, duration, camera_control.
            reference_image_base64: Base64-encoded reference image.
            aspect_ratio: Video aspect ratio.
            user_id: For cost tracking.
            channel: For cost tracking.

        Returns:
            Dict mapping scene name → video URL. Failed scenes have empty string values.
        """

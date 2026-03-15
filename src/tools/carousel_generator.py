"""
Backwards-compatibility shim — all logic moved to image_generator.py.

Re-exports the public API so existing imports continue to work.
"""

from src.tools.image_generator import (
    expand_slides_from_description,
    _process_image_background as _process_carousel_background,
    _notify_user,
    _notify_whatsapp,
    _send_destination_feedback,
    create_image_tools,
)


def create_carousel_tools(user_id: str, channel: str = "web"):
    """Shim: returns (generate_image_tool, list_gallery_tool) via unified factory."""
    return create_image_tools(user_id, channel=channel)

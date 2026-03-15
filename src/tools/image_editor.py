"""
Backwards-compatibility shim — all logic moved to image_generator.py + image_session.py.

Re-exports the public API so existing imports continue to work.
"""

from src.tools.image_session import (
    store_session_images,
    store_generated_image,
    get_session_images,
    clear_session_images,
    _upsert_image_session,
    _get_image_sessions,
    _try_recover_last_image,
)

from src.tools.image_generator import (
    _process_edit_flow,
    create_image_tools,
)


async def _process_edit_background(
    user_id: str,
    edit_prompt: str,
    reference_bytes: bytes,
    aspect_ratio: str = "1:1",
    channel: str = "web",
    task_id=None,
):
    """Shim: delegates to unified _process_image_background via edit flow."""
    from src.tools.image_generator import _process_image_background
    from src.models.carousel import create_carousel

    carousel_id = create_carousel(user_id, f"Edição: {edit_prompt[:60]}", [{"prompt": edit_prompt}])
    await _process_image_background(
        carousel_id=carousel_id,
        user_id=user_id,
        slides=[{"prompt": edit_prompt}],
        channel=channel,
        aspect_ratio=aspect_ratio,
        reference_image=reference_bytes,
        task_id=task_id,
        is_edit=True,
    )


def create_image_editor_tools(user_id: str, channel: str = "web"):
    """Shim: returns generate_image_tool (the edit_image_tool is now unified)."""
    generate_image, _ = create_image_tools(user_id, channel=channel)
    return generate_image

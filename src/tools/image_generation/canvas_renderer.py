"""
Canvas Renderer — composable layer-based image compositor using PIL.

Takes a canvas document (JSON dict) and renders it to PNG bytes by
iterating over layers sorted by z_index and compositing each one.

Reuses helpers from text_overlay.py for fonts, text fitting, shadows,
gradients, and glassmorphism effects.
"""

import io
import logging
from typing import Tuple, Optional

import httpx
from PIL import Image, ImageDraw, ImageFilter

from src.tools.image_generation.text_overlay import (
    _load_font,
    _fit_text_size,
    _wrap_text,
    _get_line_height,
    _hex_to_rgba,
    _draw_gradient_overlay,
    _apply_glassmorphism,
    _draw_rounded_rect,
    _apply_blur_shadow,
)

logger = logging.getLogger(__name__)

# Cache for downloaded images during a render session
_image_cache: dict[str, bytes] = {}


def render_canvas(canvas_doc: dict) -> bytes:
    """Render a canvas document to PNG bytes."""
    w = canvas_doc.get("width", 1080)
    h = canvas_doc.get("height", 1080)

    # 1. Create base image from background
    base = _render_background(canvas_doc.get("background", {}), w, h)

    # 2. Sort layers by z_index and composite each
    layers = canvas_doc.get("layers", [])
    layers_sorted = sorted(layers, key=lambda l: l.get("z_index", 0))

    for layer in layers_sorted:
        if not layer.get("visible", True):
            continue
        try:
            base = _render_layer(base, layer, canvas_doc)
        except Exception as e:
            logger.error("Error rendering layer %s: %s", layer.get("id"), e)

    # 3. Convert to PNG bytes
    result_rgb = base.convert("RGB")
    buf = io.BytesIO()
    result_rgb.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Background rendering
# ---------------------------------------------------------------------------

def _render_background(bg: dict, w: int, h: int) -> Image.Image:
    """Create the base image from background config."""
    bg_type = bg.get("type", "color")

    if bg_type == "image" and bg.get("image_url"):
        try:
            img_bytes = _download_image(bg["image_url"])
            img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
            return _fit_image(img, w, h, "cover")
        except Exception as e:
            logger.error("Failed to load background image: %s", e)
            return Image.new("RGBA", (w, h), _hex_to_rgba("#1A1A2E"))

    elif bg_type == "gradient" and bg.get("gradient"):
        grad = bg["gradient"]
        colors = grad.get("colors", ["#000000", "#333333"])
        return _create_gradient_bg(w, h, colors)

    else:
        color = bg.get("value", "#1A1A2E")
        return Image.new("RGBA", (w, h), _hex_to_rgba(color))


def _create_gradient_bg(w: int, h: int, colors: list) -> Image.Image:
    """Create a vertical gradient background."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if len(colors) < 2:
        return Image.new("RGBA", (w, h), _hex_to_rgba(colors[0] if colors else "#000000"))

    c1 = _hex_to_rgba(colors[0])
    c2 = _hex_to_rgba(colors[1])
    draw = ImageDraw.Draw(img)

    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(c1[0] + (c2[0] - c1[0]) * t)
        g = int(c1[1] + (c2[1] - c1[1]) * t)
        b = int(c1[2] + (c2[2] - c1[2]) * t)
        a = int(c1[3] + (c2[3] - c1[3]) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b, a))

    return img


# ---------------------------------------------------------------------------
# Layer dispatching
# ---------------------------------------------------------------------------

def _render_layer(base: Image.Image, layer: dict, canvas_doc: dict) -> Image.Image:
    """Dispatch to the correct layer renderer."""
    layer_type = layer.get("type", "")
    renderers = {
        "text": _render_text_layer,
        "image": _render_image_layer,
        "shape": _render_shape_layer,
        "overlay": _render_overlay_layer,
        "icon": _render_icon_layer,
    }
    renderer = renderers.get(layer_type)
    if renderer:
        return renderer(base, layer, canvas_doc)
    logger.warning("Unknown layer type: %s", layer_type)
    return base


# ---------------------------------------------------------------------------
# Text layer
# ---------------------------------------------------------------------------

def _render_text_layer(base: Image.Image, layer: dict, canvas_doc: dict) -> Image.Image:
    """Render a text layer with optional shadow."""
    w, h = base.size
    content = layer.get("content", "")
    if not content:
        return base

    x = layer.get("x", 0)
    y = layer.get("y", 0)
    max_width = layer.get("width", int(w * 0.8))
    font_family = layer.get("font_family", "Montserrat")
    font_size = layer.get("font_size", 48)
    font_weight = layer.get("font_weight", 700)
    color = layer.get("color", "#FFFFFF")
    align = layer.get("align", "center")
    shadow_config = layer.get("shadow")

    # Map font family to actual file
    font_file = "Montserrat-Variable.ttf" if "montserrat" in font_family.lower() else "Inter-Variable.ttf"

    # Try to fit text, use specified size as max
    font, lines, actual_size = _fit_text_size(
        content,
        font_file,
        max_width,
        int(h * 0.5),
        min_size=max(16, font_size // 3),
        max_size=font_size,
        max_lines=layer.get("max_lines", 6),
        weight=font_weight,
    )

    text_overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(text_overlay)
    text_color = _hex_to_rgba(color)

    # Shadow
    shadow_layer = None
    if shadow_config:
        shadow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow_layer)
        shadow_color = _hex_to_rgba(
            shadow_config.get("color", "#000000"),
            shadow_config.get("opacity", 160),
        )
        shadow_offset_x = shadow_config.get("offset_x", 0)
        shadow_offset_y = shadow_config.get("offset_y", 4)

        line_height = _get_line_height(font)
        sy = y + shadow_offset_y
        for line in lines:
            bbox = font.getbbox(line)
            text_w = bbox[2] - bbox[0]
            if align == "center":
                sx = x + max_width // 2 - text_w // 2 + shadow_offset_x
            elif align == "right":
                sx = x + max_width - text_w + shadow_offset_x
            else:
                sx = x + shadow_offset_x
            shadow_draw.text((sx, sy), line, font=font, fill=shadow_color)
            sy += line_height

        blur_radius = shadow_config.get("blur", 8)
        base = _apply_blur_shadow(base, shadow_layer, radius=blur_radius)

    # Draw text
    line_height = _get_line_height(font)
    ty = y
    for line in lines:
        bbox = font.getbbox(line)
        text_w = bbox[2] - bbox[0]
        if align == "center":
            tx = x + max_width // 2 - text_w // 2
        elif align == "right":
            tx = x + max_width - text_w
        else:
            tx = x
        draw.text((tx, ty), line, font=font, fill=text_color)
        ty += line_height

    return Image.alpha_composite(base, text_overlay)


# ---------------------------------------------------------------------------
# Image layer
# ---------------------------------------------------------------------------

def _render_image_layer(base: Image.Image, layer: dict, canvas_doc: dict) -> Image.Image:
    """Render an image layer with optional border-radius and border."""
    source_url = layer.get("source_url", "")
    if not source_url:
        return base

    try:
        img_bytes = _download_image(source_url)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    except Exception as e:
        logger.error("Failed to load image layer: %s", e)
        return base

    w_base, h_base = base.size
    x = layer.get("x", 0)
    y = layer.get("y", 0)
    target_w = layer.get("width", img.width)
    target_h = layer.get("height", img.height)
    border_radius = layer.get("border_radius", 0)
    border = layer.get("border")
    opacity = layer.get("opacity", 255)
    fit = layer.get("fit", "cover")

    # Resize
    img = _fit_image(img, target_w, target_h, fit)

    # Apply border-radius mask
    if border_radius > 0:
        mask = Image.new("L", (target_w, target_h), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle(
            (0, 0, target_w, target_h),
            radius=min(border_radius, target_w // 2, target_h // 2),
            fill=255,
        )
        img.putalpha(mask)

    # Apply opacity
    if opacity < 255:
        alpha = img.split()[3]
        alpha = alpha.point(lambda a: int(a * opacity / 255))
        img.putalpha(alpha)

    # Border: draw a slightly larger rounded rect behind
    if border and border.get("width", 0) > 0:
        bw = border["width"]
        bc = _hex_to_rgba(border.get("color", "#FFFFFF"))
        border_layer = Image.new("RGBA", (w_base, h_base), (0, 0, 0, 0))
        border_draw = ImageDraw.Draw(border_layer)
        border_draw.rounded_rectangle(
            (x - bw, y - bw, x + target_w + bw, y + target_h + bw),
            radius=border_radius + bw if border_radius > 0 else 0,
            fill=bc,
        )
        base = Image.alpha_composite(base, border_layer)

    # Shadow
    shadow_config = layer.get("shadow")
    if shadow_config:
        shadow_layer = Image.new("RGBA", (w_base, h_base), (0, 0, 0, 0))
        shadow_color = _hex_to_rgba(
            shadow_config.get("color", "#000000"),
            shadow_config.get("opacity", 100),
        )
        shadow_draw = ImageDraw.Draw(shadow_layer)
        sx = x + shadow_config.get("offset_x", 0)
        sy = y + shadow_config.get("offset_y", 4)
        shadow_draw.rounded_rectangle(
            (sx, sy, sx + target_w, sy + target_h),
            radius=border_radius,
            fill=shadow_color,
        )
        blurred = shadow_layer.filter(ImageFilter.GaussianBlur(radius=shadow_config.get("blur", 8)))
        base = Image.alpha_composite(base, blurred)

    # Composite image onto base
    comp_layer = Image.new("RGBA", (w_base, h_base), (0, 0, 0, 0))
    comp_layer.paste(img, (x, y), img)
    return Image.alpha_composite(base, comp_layer)


# ---------------------------------------------------------------------------
# Shape layer
# ---------------------------------------------------------------------------

def _render_shape_layer(base: Image.Image, layer: dict, canvas_doc: dict) -> Image.Image:
    """Render shape: rect, circle, line, arrow."""
    w_base, h_base = base.size
    shape = layer.get("shape", "rect")
    x = layer.get("x", 0)
    y = layer.get("y", 0)
    sw = layer.get("width", 100)
    sh = layer.get("height", 100)
    fill = layer.get("fill", "#E94560")
    stroke_color = layer.get("stroke_color")
    stroke_width = layer.get("stroke_width", 0)
    opacity = layer.get("opacity", 255)
    border_radius = layer.get("border_radius", 0)

    shape_layer = Image.new("RGBA", (w_base, h_base), (0, 0, 0, 0))
    draw = ImageDraw.Draw(shape_layer)

    fill_rgba = _hex_to_rgba(fill, opacity) if fill else None
    stroke_rgba = _hex_to_rgba(stroke_color) if stroke_color else None

    if shape == "rect":
        if border_radius > 0:
            draw.rounded_rectangle(
                (x, y, x + sw, y + sh),
                radius=border_radius,
                fill=fill_rgba,
                outline=stroke_rgba,
                width=stroke_width or 0,
            )
        else:
            draw.rectangle(
                (x, y, x + sw, y + sh),
                fill=fill_rgba,
                outline=stroke_rgba,
                width=stroke_width or 0,
            )

    elif shape == "circle":
        draw.ellipse(
            (x, y, x + sw, y + sh),
            fill=fill_rgba,
            outline=stroke_rgba,
            width=stroke_width or 0,
        )

    elif shape == "line":
        x2 = layer.get("x2", x + sw)
        y2 = layer.get("y2", y)
        draw.line(
            [(x, y), (x2, y2)],
            fill=fill_rgba or (255, 255, 255, 255),
            width=stroke_width or 2,
        )

    elif shape == "arrow":
        direction = layer.get("direction", "right")
        _draw_arrow(draw, x, y, sw, sh, direction, fill_rgba or (255, 255, 255, 255), stroke_width or 3)

    return Image.alpha_composite(base, shape_layer)


def _draw_arrow(
    draw: ImageDraw.ImageDraw,
    x: int, y: int, w: int, h: int,
    direction: str,
    color: Tuple,
    line_width: int = 3,
):
    """Draw an arrow with line + arrowhead."""
    head_size = min(w, h) // 3

    if direction == "right":
        # Line from left to right
        draw.line([(x, y + h // 2), (x + w - head_size, y + h // 2)], fill=color, width=line_width)
        # Arrowhead
        draw.polygon([
            (x + w, y + h // 2),
            (x + w - head_size, y),
            (x + w - head_size, y + h),
        ], fill=color)
    elif direction == "left":
        draw.line([(x + head_size, y + h // 2), (x + w, y + h // 2)], fill=color, width=line_width)
        draw.polygon([
            (x, y + h // 2),
            (x + head_size, y),
            (x + head_size, y + h),
        ], fill=color)
    elif direction == "down":
        draw.line([(x + w // 2, y), (x + w // 2, y + h - head_size)], fill=color, width=line_width)
        draw.polygon([
            (x + w // 2, y + h),
            (x, y + h - head_size),
            (x + w, y + h - head_size),
        ], fill=color)
    elif direction == "up":
        draw.line([(x + w // 2, y + head_size), (x + w // 2, y + h)], fill=color, width=line_width)
        draw.polygon([
            (x + w // 2, y),
            (x, y + head_size),
            (x + w, y + head_size),
        ], fill=color)


# ---------------------------------------------------------------------------
# Overlay layer (gradient / glassmorphism)
# ---------------------------------------------------------------------------

def _render_overlay_layer(base: Image.Image, layer: dict, canvas_doc: dict) -> Image.Image:
    """Render gradient or glass overlay."""
    w, h = base.size
    overlay_type = layer.get("overlay_type", "gradient")
    color = layer.get("color", "#000000")
    opacity = layer.get("opacity", 180)
    position = layer.get("position", "bottom")

    # Determine region box
    box = _position_to_box(position, w, h, layer)

    if overlay_type == "glass":
        return _apply_glassmorphism(
            base, box,
            color=_hex_to_rgba(color)[:3],
            alpha=opacity,
            blur_radius=layer.get("blur_radius", 20),
            corner_radius=layer.get("border_radius", 30),
        )
    else:
        # Gradient overlay
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        direction = layer.get("direction", position)
        _draw_gradient_overlay(overlay, box, _hex_to_rgba(color)[:3], max_alpha=opacity, direction=direction)
        return Image.alpha_composite(base, overlay)


# ---------------------------------------------------------------------------
# Icon layer (SVG rasterization)
# ---------------------------------------------------------------------------

def _render_icon_layer(base: Image.Image, layer: dict, canvas_doc: dict) -> Image.Image:
    """Render an SVG icon from shared_assets DB or inline svg_content."""
    w_base, h_base = base.size
    x = layer.get("x", 0)
    y = layer.get("y", 0)
    size = layer.get("size", 48)
    color = layer.get("color", "#FFFFFF")
    svg_content = layer.get("svg_content", "")

    icon_img = None

    if svg_content:
        icon_img = _rasterize_svg(svg_content, size, color)

    if icon_img is None:
        # Placeholder: colored circle
        icon_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(icon_img)
        d.ellipse((0, 0, size, size), fill=_hex_to_rgba(color))

    comp_layer = Image.new("RGBA", (w_base, h_base), (0, 0, 0, 0))
    comp_layer.paste(icon_img, (x, y), icon_img)
    return Image.alpha_composite(base, comp_layer)


def _rasterize_svg(svg_content: str, size: int, color: str = "#FFFFFF") -> Optional[Image.Image]:
    """Convert SVG string to PIL Image with color tinting."""
    try:
        import cairosvg
    except ImportError:
        logger.warning("cairosvg not installed, icon rendering will use placeholders")
        return None

    # Replace currentColor with desired color
    svg_colored = svg_content.replace("currentColor", color)
    # Also try to set stroke/fill color
    if 'stroke="' not in svg_colored and 'fill="' not in svg_colored:
        svg_colored = svg_colored.replace("<svg", f'<svg fill="{color}"', 1)

    try:
        png_bytes = cairosvg.svg2png(
            bytestring=svg_colored.encode("utf-8"),
            output_width=size,
            output_height=size,
        )
        return Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    except Exception as e:
        logger.error("SVG rasterization failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _download_image(url: str) -> bytes:
    """Download image from URL with caching."""
    if url in _image_cache:
        return _image_cache[url]
    resp = httpx.get(url, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    _image_cache[url] = resp.content
    return resp.content


def clear_image_cache():
    """Clear the download cache between sessions."""
    _image_cache.clear()


def _fit_image(img: Image.Image, target_w: int, target_h: int, fit: str = "cover") -> Image.Image:
    """Resize image to target dimensions with fit mode."""
    if fit == "fill":
        return img.resize((target_w, target_h), Image.Resampling.LANCZOS)
    elif fit == "contain":
        img.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
        # Center on transparent background
        result = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
        offset_x = (target_w - img.width) // 2
        offset_y = (target_h - img.height) // 2
        result.paste(img, (offset_x, offset_y), img)
        return result
    else:  # cover
        src_ratio = img.width / img.height
        tgt_ratio = target_w / target_h
        if src_ratio > tgt_ratio:
            new_h = target_h
            new_w = int(target_h * src_ratio)
        else:
            new_w = target_w
            new_h = int(target_w / src_ratio)
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        # Center crop
        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        return img.crop((left, top, left + target_w, top + target_h))


def _position_to_box(position: str, w: int, h: int, layer: dict) -> Tuple[int, int, int, int]:
    """Convert position string to (x1, y1, x2, y2) box."""
    if position == "top":
        height = layer.get("height", int(h * 0.3))
        return (0, 0, w, height)
    elif position == "bottom":
        height = layer.get("height", int(h * 0.5))
        return (0, h - height, w, h)
    elif position == "full":
        return (0, 0, w, h)
    elif position == "center-card":
        margin = int(w * 0.06)
        top = int(h * 0.12)
        bottom = int(h * 0.88)
        return (margin, top, w - margin, bottom)
    else:
        # Custom box from layer x, y, width, height
        x = layer.get("x", 0)
        y = layer.get("y", 0)
        lw = layer.get("width", w)
        lh = layer.get("height", int(h * 0.5))
        return (x, y, x + lw, y + lh)

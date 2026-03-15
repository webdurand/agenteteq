"""
Motor de text overlay para carrosséis premium.
Aplica tipografia profissional sobre imagens de fundo usando Pillow.
"""

import io
import os
import textwrap
from dataclasses import dataclass, field
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageFilter

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Diretório de fontes
# ---------------------------------------------------------------------------
_FONTS_DIR = os.path.join(os.path.dirname(__file__), "fonts")

_font_cache: dict = {}


def _load_font(name: str, size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    """Carrega fonte com cache. weight: 100-900 (400=regular, 700=bold)."""
    key = (name, size, weight)
    if key not in _font_cache:
        path = os.path.join(_FONTS_DIR, name)
        font = ImageFont.truetype(path, size=size)
        try:
            axes = font.get_variation_axes()
            values = []
            for axis in axes:
                axis_name = axis["name"]
                if isinstance(axis_name, bytes):
                    axis_name = axis_name.decode()
                if "weight" in axis_name.lower():
                    clamped = max(axis["minimum"], min(axis["maximum"], weight))
                    values.append(clamped)
                else:
                    values.append(axis["default"])
            if values:
                font.set_variation_by_axes(values)
        except Exception:
            pass
        _font_cache[key] = font
    return _font_cache[key]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class SlideText:
    role: str  # 'capa', 'conteudo', 'fechamento'
    title: str
    body: str = ""
    slide_number: int = 1
    total_slides: int = 5
    cta_text: str = ""
    text_align: str = ""  # "left", "center" — vazio usa default do role


@dataclass
class ColorPalette:
    primary: str = "#1A1A2E"       # fundo de overlay
    accent: str = "#E94560"        # destaque / CTA
    text_primary: str = "#FFFFFF"  # cor do texto principal
    text_secondary: str = "#D0D0D0"  # cor do texto secundário


# ---------------------------------------------------------------------------
# Helpers de cor
# ---------------------------------------------------------------------------
def _hex_to_rgba(hex_color: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    """Converte hex (#RRGGBB) para tupla RGBA."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (r, g, b, alpha)


def _luminance(r: int, g: int, b: int) -> float:
    """Luminância relativa (0-1)."""
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def _region_luminance(img: Image.Image, box: Tuple[int, int, int, int]) -> float:
    """Calcula luminância média de uma região da imagem."""
    region = img.crop(box).resize((50, 50))
    pixels = list(region.getdata())
    if not pixels:
        return 0.5
    total = sum(_luminance(p[0], p[1], p[2]) for p in pixels)
    return total / len(pixels)


def _adaptive_text_color(
    img: Image.Image,
    box: Tuple[int, int, int, int],
    palette: ColorPalette,
) -> str:
    """Retorna cor de texto adaptada à luminância do fundo."""
    lum = _region_luminance(img, box)
    # Se o fundo é claro, usa texto escuro; se escuro, usa texto claro
    if lum > 0.55:
        return "#1A1A2E"
    return palette.text_primary


def _resolve_align(slide: SlideText, default: str) -> str:
    """Resolve o alinhamento efetivo: usa text_align do slide se definido, senão o default do role."""
    if slide.text_align in ("left", "center"):
        return slide.text_align
    return default


# ---------------------------------------------------------------------------
# Medição e wrapping de texto
# ---------------------------------------------------------------------------
def _fit_text_size(
    text: str,
    font_name: str,
    max_width: int,
    max_height: int,
    min_size: int = 20,
    max_size: int = 120,
    max_lines: int = 4,
    weight: int = 400,
) -> Tuple[ImageFont.FreeTypeFont, list[str], int]:
    """
    Encontra o maior tamanho de fonte que faz o texto caber no bounding box.
    Retorna (font, lines, font_size).
    """
    best_font = None
    best_lines = [text]
    best_size = min_size

    for size in range(max_size, min_size - 1, -2):
        font = _load_font(font_name, size, weight)
        lines = _wrap_text(text, font, max_width)
        if len(lines) > max_lines:
            continue

        # Mede altura total
        line_height = _get_line_height(font)
        total_height = line_height * len(lines)

        if total_height <= max_height:
            best_font = font
            best_lines = lines
            best_size = size
            break

    if best_font is None:
        best_font = _load_font(font_name, min_size, weight)
        best_lines = _wrap_text(text, best_font, max_width)[:max_lines]

    return best_font, best_lines, best_size


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Quebra texto em linhas que cabem na largura máxima."""
    words = text.split()
    if not words:
        return []

    lines = []
    current_line = words[0]

    for word in words[1:]:
        test_line = current_line + " " + word
        bbox = font.getbbox(test_line)
        width = bbox[2] - bbox[0]
        if width <= max_width:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = word

    lines.append(current_line)
    return lines


def _get_line_height(font: ImageFont.FreeTypeFont) -> int:
    """Altura de uma linha incluindo espaçamento."""
    bbox = font.getbbox("Ágjpq")
    return int((bbox[3] - bbox[1]) * 1.3)


def _draw_text_block(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    x_anchor: int,
    y_start: int,
    color: Tuple[int, int, int, int],
    shadow: bool = True,
    align: str = "center",
    shadow_layer: Optional[Image.Image] = None,
) -> int:
    """Desenha bloco de texto. Retorna y final.
    align: 'center' centraliza em x_anchor, 'left' alinha à esquerda em x_anchor."""
    line_height = _get_line_height(font)
    y = y_start

    for line in lines:
        bbox = font.getbbox(line)
        text_width = bbox[2] - bbox[0]

        if align == "center":
            x = x_anchor - text_width // 2
        else:
            x = x_anchor

        if shadow and shadow_layer is not None:
            shadow_draw = ImageDraw.Draw(shadow_layer)
            shadow_color = (0, 0, 0, 160)
            shadow_draw.text((x, y), line, font=font, fill=shadow_color)

        draw.text((x, y), line, font=font, fill=color)
        y += line_height

    return y


def _apply_blur_shadow(base: Image.Image, shadow_layer: Image.Image, radius: int = 6) -> Image.Image:
    """Aplica blur gaussian na camada de sombra e compõe com a imagem base."""
    blurred = shadow_layer.filter(ImageFilter.GaussianBlur(radius=radius))
    return Image.alpha_composite(base, blurred)


# ---------------------------------------------------------------------------
# Desenho de overlays (gradiente, card, glassmorphism)
# ---------------------------------------------------------------------------
def _draw_gradient_overlay(
    overlay: Image.Image,
    box: Tuple[int, int, int, int],
    color: Tuple[int, int, int],
    max_alpha: int = 180,
    direction: str = "bottom",
):
    """Desenha gradiente semi-transparente na região.
    direction: 'bottom' (transparente→opaco com ease-in), 'top' (opaco→transparente), 'full' (opacidade uniforme)."""
    x1, y1, x2, y2 = box
    height = y2 - y1
    draw = ImageDraw.Draw(overlay)

    for i in range(height):
        t = i / height if height > 0 else 0
        if direction == "bottom":
            # Curva ease-in (quadrática) para transição mais suave
            alpha = int(max_alpha * (t ** 2))
        elif direction == "top":
            alpha = int(max_alpha * ((1 - t) ** 2))
        else:
            alpha = max_alpha

        draw.line(
            [(x1, y1 + i), (x2, y1 + i)],
            fill=(*color, alpha),
        )


def _draw_rounded_rect(
    draw: ImageDraw.ImageDraw,
    box: Tuple[int, int, int, int],
    fill: Tuple[int, int, int, int],
    radius: int = 20,
):
    """Desenha retângulo com cantos arredondados."""
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _apply_glassmorphism(
    img: Image.Image,
    box: Tuple[int, int, int, int],
    color: Tuple[int, int, int],
    alpha: int = 140,
    blur_radius: int = 20,
    corner_radius: int = 30,
) -> Image.Image:
    """Aplica efeito glassmorphism: blur do fundo + overlay semi-transparente com bordas arredondadas."""
    w, h = img.size
    x1, y1, x2, y2 = box

    # 1. Cria máscara com bordas arredondadas
    mask = Image.new("L", (w, h), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(box, radius=corner_radius, fill=255)

    # 2. Aplica blur na imagem inteira
    blurred = img.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    # 3. Compõe: usa imagem original onde não tem card, blurred onde tem
    result = img.copy()
    result.paste(blurred, mask=mask)

    # 4. Overlay de cor semi-transparente por cima do blur
    color_overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    color_draw = ImageDraw.Draw(color_overlay)
    color_draw.rounded_rectangle(box, radius=corner_radius, fill=(*color, alpha))
    result = Image.alpha_composite(result.convert("RGBA"), color_overlay)

    return result


# ---------------------------------------------------------------------------
# Layouts por role
# ---------------------------------------------------------------------------
def _layout_capa(
    img: Image.Image,
    slide: SlideText,
    palette: ColorPalette,
) -> Image.Image:
    """Layout de capa: título grande no terço inferior com gradiente ease-in e accent bar."""
    w, h = img.size
    padding = int(w * 0.08)
    max_text_width = w - padding * 2
    align = _resolve_align(slide, "center")

    # Área do gradiente: 50% inferior com ease-in
    grad_top = int(h * 0.45)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    bg_color = _hex_to_rgba(palette.primary)[:3]
    _draw_gradient_overlay(overlay, (0, grad_top, w, h), bg_color, max_alpha=210)

    img = Image.alpha_composite(img.convert("RGBA"), overlay)

    # Camada de sombra (blur gaussian)
    shadow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    # Texto
    text_overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(text_overlay)

    # Accent bar acima do título
    accent_color = _hex_to_rgba(palette.accent)
    bar_y = int(h * 0.56)
    if align == "center":
        bar_x = w // 2 - 30
    else:
        bar_x = padding
    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + 60, bar_y + 5),
        radius=3,
        fill=accent_color,
    )

    # Título
    title_area_top = bar_y + 18
    title_area_height = int(h * 0.30)
    title_font, title_lines, _ = _fit_text_size(
        slide.title,
        "Montserrat-Variable.ttf",
        max_text_width,
        title_area_height,
        min_size=32,
        max_size=90,
        max_lines=3,
        weight=700,
    )

    title_region = (padding, title_area_top, w - padding, title_area_top + title_area_height)
    adaptive_color = _adaptive_text_color(img, title_region, palette)
    title_color = _hex_to_rgba(adaptive_color)

    x_anchor = w // 2 if align == "center" else padding
    _draw_text_block(draw, title_lines, title_font, x_anchor, title_area_top, title_color,
                     shadow=True, align=align, shadow_layer=shadow_layer)

    # Subtítulo / body (se houver)
    if slide.body:
        body_top = title_area_top + title_area_height + 10
        body_font, body_lines, _ = _fit_text_size(
            slide.body,
            "Inter-Variable.ttf",
            max_text_width,
            int(h * 0.10),
            min_size=18,
            max_size=32,
            max_lines=2,
            weight=400,
        )
        body_color = _hex_to_rgba(palette.text_secondary if adaptive_color == palette.text_primary else "#4A4A4A")
        _draw_text_block(draw, body_lines, body_font, x_anchor, body_top, body_color,
                         shadow=False, align=align)

    # Compor: sombra blur + texto
    img = _apply_blur_shadow(img, shadow_layer, radius=8)
    return Image.alpha_composite(img, text_overlay)


def _layout_conteudo(
    img: Image.Image,
    slide: SlideText,
    palette: ColorPalette,
) -> Image.Image:
    """Layout de conteúdo: card glassmorphism com título e body."""
    w, h = img.size
    padding = int(w * 0.08)
    align = _resolve_align(slide, "left")

    # Card glassmorphism no centro
    card_margin = int(w * 0.06)
    card_top = int(h * 0.12)
    card_bottom = int(h * 0.88)
    card_box = (card_margin, card_top, w - card_margin, card_bottom)

    bg_color = _hex_to_rgba(palette.primary)[:3]
    img = _apply_glassmorphism(img.convert("RGBA"), card_box, bg_color, alpha=140, blur_radius=20, corner_radius=30)

    # Texto sobre o card
    text_overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(text_overlay)

    content_left = card_margin + padding
    content_top = card_top + int(h * 0.05)
    content_width = w - (card_margin + padding) * 2

    # Número do slide (indicador discreto)
    num_font = _load_font("Inter-Variable.ttf", 24, weight=500)
    num_text = f"{slide.slide_number:02d}/{slide.total_slides:02d}"
    num_color = _hex_to_rgba(palette.text_secondary, alpha=180)
    draw.text((content_left, content_top), num_text, font=num_font, fill=num_color)

    # Accent bar
    accent_color = _hex_to_rgba(palette.accent)
    bar_y = content_top + 40
    draw.rounded_rectangle(
        (content_left, bar_y, content_left + 50, bar_y + 4),
        radius=2,
        fill=accent_color,
    )

    # Título
    title_top = bar_y + 20
    title_area_height = int(h * 0.18)
    title_font, title_lines, _ = _fit_text_size(
        slide.title,
        "Montserrat-Variable.ttf",
        content_width,
        title_area_height,
        min_size=24,
        max_size=56,
        max_lines=3,
        weight=700,
    )

    title_color = _hex_to_rgba(palette.text_primary)
    y = title_top
    line_height = _get_line_height(title_font)
    for line in title_lines:
        if align == "center":
            bbox = title_font.getbbox(line)
            text_w = bbox[2] - bbox[0]
            x = content_left + (content_width - text_w) // 2
        else:
            x = content_left
        draw.text((x, y), line, font=title_font, fill=title_color)
        y += line_height

    # Body
    if slide.body:
        body_top = y + 20
        body_area_height = card_bottom - body_top - padding
        body_font, body_lines, _ = _fit_text_size(
            slide.body,
            "Inter-Variable.ttf",
            content_width,
            body_area_height,
            min_size=18,
            max_size=32,
            max_lines=6,
            weight=400,
        )
        body_color = _hex_to_rgba(palette.text_secondary)
        for line in body_lines:
            if align == "center":
                bbox = body_font.getbbox(line)
                text_w = bbox[2] - bbox[0]
                x = content_left + (content_width - text_w) // 2
            else:
                x = content_left
            draw.text((x, body_top), line, font=body_font, fill=body_color)
            body_top += _get_line_height(body_font)

    return Image.alpha_composite(img, text_overlay)


def _layout_fechamento(
    img: Image.Image,
    slide: SlideText,
    palette: ColorPalette,
) -> Image.Image:
    """Layout de fechamento/CTA: card glassmorphism com bordas arredondadas, texto centralizado."""
    w, h = img.size
    padding = int(w * 0.08)
    align = _resolve_align(slide, "center")

    # Card glassmorphism (mesmo estilo do conteudo, mantém consistência visual)
    card_margin = int(w * 0.06)
    card_top = int(h * 0.12)
    card_bottom = int(h * 0.88)
    card_box = (card_margin, card_top, w - card_margin, card_bottom)

    bg_color = _hex_to_rgba(palette.primary)[:3]
    img = _apply_glassmorphism(img.convert("RGBA"), card_box, bg_color, alpha=160, blur_radius=20, corner_radius=30)

    # Camada de sombra
    shadow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    text_overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(text_overlay)

    content_left = card_margin + padding
    content_width = w - (card_margin + padding) * 2

    # CTA ou título como texto principal
    main_text = slide.cta_text or slide.title
    center_y = int(h * 0.30)
    title_area_height = int(h * 0.30)

    title_font, title_lines, _ = _fit_text_size(
        main_text,
        "Montserrat-Variable.ttf",
        content_width,
        title_area_height,
        min_size=28,
        max_size=72,
        max_lines=3,
        weight=700,
    )

    title_color = _hex_to_rgba(palette.text_primary)
    x_anchor = content_left + content_width // 2 if align == "center" else content_left
    y_after = _draw_text_block(draw, title_lines, title_font, x_anchor, center_y, title_color,
                               shadow=True, align=align, shadow_layer=shadow_layer)

    # Accent bar abaixo do título
    accent_color = _hex_to_rgba(palette.accent)
    bar_w = 80
    bar_y = y_after + 15
    if align == "center":
        bar_x = x_anchor - bar_w // 2
    else:
        bar_x = content_left
    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + bar_w, bar_y + 5),
        radius=3,
        fill=accent_color,
    )

    # Body / texto secundário
    if slide.body:
        body_top = bar_y + 25
        body_font, body_lines, _ = _fit_text_size(
            slide.body,
            "Inter-Variable.ttf",
            content_width,
            int(h * 0.20),
            min_size=18,
            max_size=30,
            max_lines=3,
            weight=400,
        )
        body_color = _hex_to_rgba(palette.text_secondary)
        _draw_text_block(draw, body_lines, body_font, x_anchor, body_top, body_color,
                         shadow=False, align=align)

    # Compor: sombra blur + texto
    img = _apply_blur_shadow(img, shadow_layer, radius=6)
    return Image.alpha_composite(img, text_overlay)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
def apply_text_overlay(
    image_bytes: bytes,
    slide_text: SlideText,
    color_palette: Optional[ColorPalette] = None,
) -> bytes:
    """
    Aplica texto profissional sobre a imagem de fundo.

    Args:
        image_bytes: Imagem de fundo em bytes (PNG/JPEG/WebP)
        slide_text: Definição do texto do slide
        color_palette: Paleta de cores (usa defaults se não fornecida)

    Returns:
        Imagem com overlay em bytes PNG
    """
    if not slide_text.title and not slide_text.cta_text:
        return image_bytes

    palette = color_palette or ColorPalette()

    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")

        layout_fn = {
            "capa": _layout_capa,
            "conteudo": _layout_conteudo,
            "fechamento": _layout_fechamento,
        }.get(slide_text.role, _layout_conteudo)

        result = layout_fn(img, slide_text, palette)

        # Converte de volta para RGB (sem alpha) e salva como PNG
        result_rgb = result.convert("RGB")
        buf = io.BytesIO()
        result_rgb.save(buf, format="PNG")
        logger.info(
            "Text overlay aplicado | role=%s | title=%s chars | size=%s bytes",
            slide_text.role,
            len(slide_text.title),
            buf.tell(),
        )
        return buf.getvalue()

    except Exception as e:
        logger.error("Erro no text overlay, retornando imagem original: %s", e)
        return image_bytes

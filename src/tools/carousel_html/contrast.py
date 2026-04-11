"""WCAG 2.1 contrast ratio checker for carousel HTML slides."""

import re
import colorsys


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))  # type: ignore


def relative_luminance(r: int, g: int, b: int) -> float:
    """Calculate relative luminance per WCAG 2.1."""
    def linearize(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4
    return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)


def contrast_ratio(color1: str, color2: str) -> float:
    """Calculate WCAG contrast ratio between two hex colors."""
    r1, g1, b1 = hex_to_rgb(color1)
    r2, g2, b2 = hex_to_rgb(color2)
    l1 = relative_luminance(r1, g1, b1)
    l2 = relative_luminance(r2, g2, b2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def check_slide_contrast(html: str) -> list[dict]:
    """
    Extract color pairs from inline CSS in HTML and check contrast.
    Returns list of issues found.
    """
    issues = []

    # Extract background-color and color from inline styles
    bg_matches = re.findall(r'background(?:-color)?:\s*(#[0-9a-fA-F]{3,8})', html)
    text_matches = re.findall(r'(?<!background-)color:\s*(#[0-9a-fA-F]{3,8})', html)

    if not bg_matches or not text_matches:
        return issues

    # Check the most common bg against all text colors
    bg_color = bg_matches[0] if bg_matches else "#1a1a2e"

    for text_color in text_matches:
        try:
            ratio = contrast_ratio(bg_color, text_color)
            if ratio < 4.5:
                issues.append({
                    "bg": bg_color,
                    "text": text_color,
                    "ratio": round(ratio, 2),
                    "required": 4.5,
                    "level": "AA",
                })
        except (ValueError, IndexError):
            continue

    return issues


def suggest_fix(bg_color: str, low_contrast_color: str) -> str:
    """Suggest a high-contrast alternative for a given bg/text pair."""
    r, g, b = hex_to_rgb(bg_color)
    luminance = relative_luminance(r, g, b)

    # If bg is dark, suggest white/light. If bg is light, suggest dark.
    if luminance < 0.5:
        return "#ffffff"
    else:
        return "#111111"

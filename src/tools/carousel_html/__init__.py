"""Carousel HTML engine — gera carrosséis via HTML/CSS + Playwright."""

from .engine import CarouselHTMLEngine
from .renderer import PlaywrightRenderer

__all__ = ["CarouselHTMLEngine", "PlaywrightRenderer"]

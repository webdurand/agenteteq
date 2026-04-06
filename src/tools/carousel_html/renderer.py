"""
Playwright renderer — converte HTML string em PNG bytes.

Usa Chromium headless para renderização pixel-perfect com suporte
completo a CSS moderno, Google Fonts e tipografia web.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class PlaywrightRenderer:
    """
    Renderiza HTML → PNG via Playwright (Chromium headless).
    Cria browser por instância (não singleton) para evitar conflitos de event loop.
    """

    def __init__(self):
        self._playwright = None
        self._browser = None

    async def _ensure_browser(self):
        """Inicializa browser Chromium."""
        if self._browser and self._browser.is_connected():
            return

        try:
            from playwright.async_api import async_playwright

            if self._playwright is None:
                self._playwright = await async_playwright().start()

            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            logger.info("Playwright browser inicializado")
        except Exception as e:
            logger.error("Falha ao inicializar Playwright: %s", e)
            raise

    async def render_slide(
        self,
        html: str,
        width: int = 1080,
        height: int = 1080,
        wait_for_fonts: bool = True,
    ) -> bytes:
        """
        Renderiza uma string HTML em PNG bytes.

        Args:
            html: HTML completo (<!DOCTYPE html>...</html>)
            width: Largura em pixels
            height: Altura em pixels
            wait_for_fonts: Se True, aguarda fontes carregarem

        Returns:
            PNG bytes
        """
        await self._ensure_browser()

        page = await self._browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=2,  # Retina quality
        )

        try:
            await page.set_content(html, wait_until="networkidle")

            # Aguarda fontes do Google Fonts carregarem
            if wait_for_fonts:
                try:
                    await page.wait_for_function(
                        "document.fonts.ready.then(() => true)",
                        timeout=5000,
                    )
                except Exception:
                    pass  # Continua mesmo se fontes não carregarem

            screenshot = await page.screenshot(
                type="png",
                clip={"x": 0, "y": 0, "width": width, "height": height},
            )
            return screenshot

        finally:
            await page.close()

    async def render_carousel(
        self,
        html_slides: list[str],
        width: int = 1080,
        height: int = 1080,
    ) -> list[bytes]:
        """
        Renderiza múltiplos slides HTML em PNG bytes.
        Processa sequencialmente para não sobrecarregar o browser.

        Args:
            html_slides: Lista de HTML strings completos
            width: Largura de cada slide
            height: Altura de cada slide

        Returns:
            Lista de PNG bytes na mesma ordem
        """
        await self._ensure_browser()
        results = []

        for i, html in enumerate(html_slides):
            try:
                png = await self.render_slide(html, width, height)
                results.append(png)
                logger.info("Slide %d/%d renderizado (%d bytes)", i + 1, len(html_slides), len(png))
            except Exception as e:
                logger.error("Erro ao renderizar slide %d: %s", i + 1, e)
                raise

        return results

    async def render_preview(
        self,
        html: str,
        width: int = 1080,
        height: int = 1080,
    ) -> bytes:
        """Renderiza um preview (alias para render_slide)."""
        return await self.render_slide(html, width, height)

    async def close(self):
        """Fecha browser e playwright."""
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        logger.info("Playwright renderer fechado")

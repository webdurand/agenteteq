"""
ChannelRouter — roteador agnóstico e desacoplado para entregar conteúdo
(texto ou imagem) em qualquer canal suportado.

Uso:
    router = ChannelRouter(user_id)
    result = await router.send_text("Suas tarefas...", "whatsapp")
    result = await router.send_image(url, "web", caption="Slide 1")

Quem chama não precisa saber se é Meta API, Evolution, WebSocket, etc.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

CHANNEL_ALIASES: dict[str, str] = {
    "web": "web_text",
    "app": "web_text",
    "aqui": "web_text",
    "web_text": "web_text",
    "web_voice": "web_voice",
    "voz_web": "web_voice",
    "whatsapp": "whatsapp_text",
    "wpp": "whatsapp_text",
    "zap": "whatsapp_text",
    "whatsapp_text": "whatsapp_text",
    "ambos": "web_whatsapp",
    "web_e_whatsapp": "web_whatsapp",
    "web_whatsapp": "web_whatsapp",
}

SUPPORTED_CHANNELS = {"whatsapp_text", "web_text", "web_voice", "web_whatsapp"}


def resolve_channel(raw: str) -> str | None:
    """Resolve um alias informal para o canal concreto. Retorna None se inválido."""
    return CHANNEL_ALIASES.get(raw.strip().lower())


class ChannelRouter:
    """Router agnóstico para entregar conteúdo em qualquer canal."""

    def __init__(self, user_id: str):
        self.user_id = user_id

    def resolve(self, raw: str) -> list[str]:
        """Resolve alias → lista de canais concretos."""
        channel = resolve_channel(raw)
        if not channel:
            return []
        if channel == "web_whatsapp":
            return ["whatsapp_text", "web_text"]
        return [channel]

    async def send_text(self, text: str, channel: str) -> dict:
        """
        Envia texto para o canal resolvido.
        Retorna {delivered: bool, channels: [...], fallback: str|None}.
        """
        targets = self.resolve(channel)
        if not targets:
            return {"delivered": False, "channels": [], "error": f"Canal '{channel}' nao suportado."}

        results: list[str] = []
        fallback: Optional[str] = None

        for target in targets:
            ok = await self._deliver_text(target, text)
            if ok:
                results.append(target)
            else:
                fallback = target

        return {
            "delivered": len(results) > 0,
            "channels": results,
            "fallback": fallback,
        }

    async def send_image(self, image_url: str, channel: str, caption: str = "") -> dict:
        """
        Envia imagem para o canal resolvido.
        Retorna {delivered: bool, channels: [...]}.
        """
        targets = self.resolve(channel)
        if not targets:
            return {"delivered": False, "channels": [], "error": f"Canal '{channel}' nao suportado."}

        results: list[str] = []

        for target in targets:
            ok = await self._deliver_image(target, image_url, caption)
            if ok:
                results.append(target)

        return {"delivered": len(results) > 0, "channels": results}

    # ------------------------------------------------------------------ #
    #  Internals — quem usa ChannelRouter não precisa saber destes métodos
    # ------------------------------------------------------------------ #

    async def _deliver_text(self, target: str, text: str) -> bool:
        """Entrega texto num canal concreto. Retorna True se entregue."""
        try:
            if target == "whatsapp_text":
                return await self._send_whatsapp_text(text)
            elif target in ("web_text", "web_voice"):
                return await self._send_web_text(text)
            else:
                logger.warning("[ChannelRouter] Canal concreto desconhecido: %s", target)
                return False
        except Exception as e:
            logger.error("[ChannelRouter] Erro ao entregar texto em %s: %s", target, e)
            return False

    async def _deliver_image(self, target: str, image_url: str, caption: str) -> bool:
        """Entrega imagem num canal concreto."""
        try:
            if target == "whatsapp_text":
                return await self._send_whatsapp_image(image_url, caption)
            elif target in ("web_text", "web_voice"):
                return await self._send_web_image(image_url, caption)
            else:
                logger.warning("[ChannelRouter] Canal concreto desconhecido: %s", target)
                return False
        except Exception as e:
            logger.error("[ChannelRouter] Erro ao entregar imagem em %s: %s", target, e)
            return False

    # -- WhatsApp -------------------------------------------------------- #

    async def _send_whatsapp_text(self, text: str) -> bool:
        from src.integrations.whatsapp import whatsapp_client
        await whatsapp_client.send_text_message(self.user_id, text)
        logger.info("[ChannelRouter] Texto enviado via WhatsApp para %s", self.user_id[:8])
        return True

    async def _send_whatsapp_image(self, image_url: str, caption: str) -> bool:
        from src.integrations.whatsapp import whatsapp_client
        await whatsapp_client.send_image(self.user_id, image_url, caption=caption or None)
        logger.info("[ChannelRouter] Imagem enviada via WhatsApp para %s", self.user_id[:8])
        return True

    # -- Web (WebSocket) ------------------------------------------------- #

    async def _send_web_text(self, text: str) -> bool:
        from src.endpoints.web import ws_manager
        if not ws_manager.is_online(self.user_id):
            logger.info("[ChannelRouter] Usuario %s offline na web.", self.user_id[:8])
            return False
        delivered = await ws_manager.send_personal_message(self.user_id, {
            "type": "response",
            "text": text,
            "audio_b64": "",
            "mime_type": "none",
            "needs_follow_up": False,
        })
        return bool(delivered)

    async def _send_web_image(self, image_url: str, caption: str) -> bool:
        from src.endpoints.web import ws_manager
        if not ws_manager.is_online(self.user_id):
            logger.info("[ChannelRouter] Usuario %s offline na web.", self.user_id[:8])
            return False
        delivered = await ws_manager.send_personal_message(self.user_id, {
            "type": "response",
            "text": f"{caption}\n{image_url}" if caption else image_url,
            "audio_b64": "",
            "mime_type": "none",
            "needs_follow_up": False,
        })
        return bool(delivered)

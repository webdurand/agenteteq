"""
Tool factory para enviar mensagens em outro canal.
Wrapper fino em cima do ChannelRouter — expõe como tool para o agente (Agno).
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


def create_send_to_channel_tool(user_phone: str):
    """
    Factory que cria a tool send_to_channel com o user_phone pré-injetado.

    Returns:
        Callable send_to_channel(message, channel) -> str
    """

    def send_to_channel(message: str, channel: str = "whatsapp") -> str:
        """
        Envia uma mensagem de texto para outro canal de comunicacao do usuario.
        Use quando o usuario pedir explicitamente para enviar algo em outro canal
        (ex: 'manda isso no meu WhatsApp', 'envia na web', 'manda nos dois').

        Args:
            message: O texto completo a ser enviado no canal de destino.
                     Deve ser a mensagem final e formatada, pronta para leitura.
            channel: Canal de destino. Opcoes:
                     - 'whatsapp' (ou 'wpp', 'zap') — envia via WhatsApp
                     - 'web' (ou 'app') — envia no chat web (se o usuario estiver online)
                     - 'ambos' — envia no WhatsApp E na web

        Returns:
            Confirmacao de envio ou aviso se o usuario esta offline no canal web.
        """
        from src.integrations.channel_router import ChannelRouter

        router = ChannelRouter(user_phone)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(asyncio.run, router.send_text(message, channel)).result()
        else:
            result = asyncio.run(router.send_text(message, channel))

        if result.get("error"):
            return f"Canal '{channel}' nao suportado. Use: whatsapp, web ou ambos."

        if result["delivered"]:
            channels_str = ", ".join(result["channels"])
            return f"Mensagem enviada com sucesso via {channels_str}."
        else:
            fallback = result.get("fallback", "")
            if "web" in fallback:
                return "O usuario nao esta online na web no momento. A mensagem nao foi entregue."
            return "Nao foi possivel entregar a mensagem no canal solicitado."

    return send_to_channel

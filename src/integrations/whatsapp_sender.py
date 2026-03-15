"""
Helper compartilhado para envio de mensagens WhatsApp com suporte a elementos interativos.
Extraido de endpoints/whatsapp.py para reutilizacao no dispatcher e outros modulos.
"""
import logging

from src.agent.response_utils import parse_interactive_elements, split_whatsapp_messages
from src.integrations.whatsapp import whatsapp_client

logger = logging.getLogger(__name__)


async def send_whatsapp_with_interactive(
    to_number: str,
    text: str,
    reply_to_message_id: str | None = None,
):
    """Envia mensagem WhatsApp com suporte a [BUTTONS] e [LIST].

    Parseia o texto procurando marcadores interativos e envia via API
    apropriada (botoes, lista ou texto simples com split automatico).
    Em caso de falha no envio de botoes/lista, faz fallback para texto.
    """
    parsed = parse_interactive_elements(text)

    # TODO: reabilitar botoes/listas interativos quando Evolution API suportar corretamente
    if parsed["buttons"]:
        body = parsed["body"]
        fallback = body + "\n\n" + "\n".join(f"• {b['title']}" for b in parsed["buttons"])
        parts = split_whatsapp_messages(fallback)
        for i, part in enumerate(parts):
            rid = reply_to_message_id if i == 0 else None
            await whatsapp_client.send_text_message(to_number, part, reply_to_message_id=rid)

    elif parsed["list"]:
        body = parsed["body"]
        text_rows = []
        for sec in parsed["list"]["sections"]:
            for row in sec.get("rows", []):
                desc = f" — {row['description']}" if row.get("description") else ""
                text_rows.append(f"• {row['title']}{desc}")
        fallback = body + "\n\n" + "\n".join(text_rows)
        parts = split_whatsapp_messages(fallback)
        for i, part in enumerate(parts):
            rid = reply_to_message_id if i == 0 else None
            await whatsapp_client.send_text_message(to_number, part, reply_to_message_id=rid)

    else:
        parts = split_whatsapp_messages(text)
        for i, part in enumerate(parts):
            rid = reply_to_message_id if i == 0 else None
            await whatsapp_client.send_text_message(to_number, part, reply_to_message_id=rid)

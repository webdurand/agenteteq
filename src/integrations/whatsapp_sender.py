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

    if parsed["buttons"]:
        body = parsed["body"]
        if len(body) > 1024:
            parts = split_whatsapp_messages(body)
            for i, part in enumerate(parts[:-1]):
                rid = reply_to_message_id if i == 0 else None
                await whatsapp_client.send_text_message(to_number, part, reply_to_message_id=rid)
            try:
                await whatsapp_client.send_button_message(to_number, parts[-1], parsed["buttons"])
            except Exception as e:
                logger.warning("Fallback de botoes para texto: %s", e)
                await whatsapp_client.send_text_message(to_number, parts[-1])
        else:
            try:
                await whatsapp_client.send_button_message(
                    to_number, body or "Escolha uma opcao:", parsed["buttons"],
                )
            except Exception as e:
                logger.warning("Fallback de botoes para texto: %s", e)
                fallback = body + "\n\n" + "\n".join(f"• {b['title']}" for b in parsed["buttons"])
                await whatsapp_client.send_text_message(to_number, fallback, reply_to_message_id=reply_to_message_id)

    elif parsed["list"]:
        body = parsed["body"]
        lst = parsed["list"]
        try:
            await whatsapp_client.send_list_message(
                to_number, body or "Escolha uma opcao:", lst["button_text"], lst["sections"],
            )
        except Exception as e:
            logger.warning("Fallback de lista para texto: %s", e)
            text_rows = []
            for sec in lst["sections"]:
                for row in sec.get("rows", []):
                    desc = f" — {row['description']}" if row.get("description") else ""
                    text_rows.append(f"• {row['title']}{desc}")
            fallback = body + "\n\n" + "\n".join(text_rows)
            await whatsapp_client.send_text_message(to_number, fallback, reply_to_message_id=reply_to_message_id)

    else:
        parts = split_whatsapp_messages(text)
        for i, part in enumerate(parts):
            rid = reply_to_message_id if i == 0 else None
            await whatsapp_client.send_text_message(to_number, part, reply_to_message_id=rid)

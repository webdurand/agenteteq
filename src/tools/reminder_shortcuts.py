import re
from typing import Optional

from src.tools.scheduler_tool import create_scheduler_tools


_TIME_PATTERN = re.compile(
    r"\b(?:daqui|em)\s+(?:a\s+)?(\d{1,4})\s*(?:m|min|mins|minuto|minutos)\b",
    re.IGNORECASE,
)

_TRIGGER_HINTS = (
    "me avisa",
    "me lembra",
    "lembra de",
    "lembrete",
    "aviso",
    "toque",
    "alarme",
    "me manda",
    "manda um",
    "me dá um",
    "alô",
    "alo",
    "avise",
)


def try_schedule_quick_reminder(
    user_phone: str,
    text: str,
    notification_channel: str = "whatsapp_text",
) -> Optional[str]:
    """
    Atalho deterministico para frases como:
    "me avisa daqui 5 min que tenho reuniao".

    Retorna mensagem de confirmacao/erro se detectar e agendar,
    ou None quando a frase nao parece um pedido de lembrete rapido.
    """
    lowered = text.lower()
    if not any(hint in lowered for hint in _TRIGGER_HINTS):
        return None

    time_match = _TIME_PATTERN.search(text)
    if not time_match:
        return None

    minutes = int(time_match.group(1))
    if minutes <= 0:
        return None

    instructions = (
        f'O usuario havia pedido: "{text}". '
        "Atenda ao pedido diretamente. "
        "Se envolver tarefas, use list_tasks. "
        "Se envolver pesquisa, use web_search. "
        "Envie o resultado pronto."
    )

    schedule_message, _, _ = create_scheduler_tools(user_phone)
    return schedule_message(
        task_instructions=instructions,
        trigger_type="date",
        minutes_from_now=minutes,
        title=text[:60],
        notification_channel=notification_channel,
    )

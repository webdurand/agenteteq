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


def _extract_channel_from_text(text: str, preferred_web_channel: str = "web_text") -> Optional[str]:
    """Tenta detectar canal na frase do usuario. Retorna None se nao encontrar."""
    lowered = text.lower().replace("-", " ")

    has_web = bool(
        re.search(r"\bweb\b", lowered)
        or re.search(r"\bapp\b", lowered)
        or re.search(r"\bsite\b", lowered)
    )
    has_whatsapp = bool(re.search(r"\b(whatsapp|whats|wpp|zap)\b", lowered))
    has_both = any(k in lowered for k in ("ambos", "nos dois", "os dois"))

    if has_both or (has_web and has_whatsapp):
        return "web_whatsapp"
    if has_whatsapp:
        return "whatsapp_text"
    if has_web:
        return "web_voice" if ("voz" in lowered or "fala" in lowered) else preferred_web_channel

    return None


def try_schedule_quick_reminder(
    user_phone: str,
    text: str,
    notification_channel: Optional[str] = None,
    preferred_web_channel: str = "web_text",
) -> Optional[str]:
    """
    Atalho deterministico para frases como "me avisa daqui 5 min".

    - Se canal vier preenchido ou for detectavel na frase, agenda direto.
    - Se nao conseguir determinar o canal, retorna None e deixa o agente lidar.
    """
    lowered = text.lower().strip()
    if not any(hint in lowered for hint in _TRIGGER_HINTS):
        return None

    time_match = _TIME_PATTERN.search(text)
    if not time_match:
        return None

    minutes = int(time_match.group(1))
    if minutes <= 0:
        return None

    chosen_channel = notification_channel or _extract_channel_from_text(text, preferred_web_channel)
    if not chosen_channel:
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
        notification_channel=chosen_channel,
    )

"""
Serviço de orquestração compartilhado entre canais (WhatsApp, Web, Voice).

Centraliza a lógica comum de:
- Budget enforcement
- Session management
- Agent creation
- Response extraction com retry
- Logging de métricas
"""
import asyncio
import logging
import os
import time

from src.agent.factory import create_agent_with_tools
from src.agent.prompts import GREETING_INJECTION
from src.agent.response_utils import extract_final_response
from src.config.feature_gates import check_budget
from src.memory.analytics import log_agent_tools, log_event, log_run_metrics
from src.memory.identity import get_or_rotate_session, get_user, is_new_session, update_last_seen

logger = logging.getLogger(__name__)

# Tools that have side effects and should NOT be retried
SIDE_EFFECT_TOOLS = frozenset({
    "generate_carousel_tool", "edit_image_tool", "schedule_message",
    "generate_video", "plan_content", "track_account",
})


def enforce_budget(user_id: str) -> str | None:
    """Returns budget error message if exceeded, None if OK."""
    try:
        return check_budget(user_id)
    except Exception as e:
        logger.error("Erro ao verificar budget para %s: %s", user_id[:4] + "***", e)
        return None


def setup_session(user_id: str, threshold_hours: float = 4.0) -> tuple[str, bool]:
    """
    Returns (session_id, is_new_session).
    Rotates session if user has been inactive for threshold_hours.
    """
    user = get_user(user_id)
    if not user:
        return user_id, False
    new = is_new_session(user, threshold_hours=threshold_hours)
    session_id = get_or_rotate_session(user_id, force_new=new)
    return session_id, new


def create_agent(
    session_id: str,
    user_id: str,
    channel: str,
    notifier=None,
    include_scheduler: bool = True,
    include_knowledge: bool = True,
    extra_instructions: list[str] | None = None,
):
    """Creates an Agno agent with all tools for the given channel."""
    return create_agent_with_tools(
        session_id=session_id,
        notifier=notifier,
        user_id=user_id,
        channel=channel,
        extra_instructions=extra_instructions,
        include_scheduler=include_scheduler,
        include_knowledge=include_knowledge,
    )


async def run_agent(
    agent,
    prompt: str,
    user_id: str,
    channel: str,
    is_new_session: bool = False,
    images=None,
    audios=None,
) -> tuple[str, object]:
    """
    Runs the agent with greeting injection and retry logic.
    Returns (final_text, raw_response).
    """
    if is_new_session:
        prompt = GREETING_INJECTION + "\n\n" + prompt

    kwargs = {"knowledge_filters": {"user_id": user_id}}
    if images:
        kwargs["images"] = images
    if audios:
        kwargs["audio"] = audios

    response = await asyncio.to_thread(agent.run, prompt, **kwargs)
    log_agent_tools(user_id, channel, response)
    asyncio.create_task(asyncio.to_thread(log_run_metrics, user_id, channel, response))

    final_text = extract_final_response(response)

    # Retry if empty and no side-effect tools were called
    if not final_text and not _had_side_effects(response):
        logger.info("[%s] Resposta vazia, retrying agent.run()...", channel)
        response = await asyncio.to_thread(agent.run, prompt, **kwargs)
        final_text = extract_final_response(response)
        if not final_text:
            final_text = "Desculpa, tive um problema ao processar sua mensagem. Pode repetir?"
            log_event(
                user_id=user_id, channel=channel,
                event_type="empty_response", status="error",
                extra_data={"original_message": prompt[:200]},
            )

    update_last_seen(user_id)
    return final_text, response


def _had_side_effects(response) -> bool:
    """Check if any side-effect tools were called in the response."""
    if not hasattr(response, "messages") or not response.messages:
        return False
    for msg in response.messages:
        for tc in getattr(msg, "tool_calls", None) or []:
            fn = getattr(tc, "function", None)
            tc_name = getattr(fn, "name", None) if fn else None
            if tc_name in SIDE_EFFECT_TOOLS:
                return True
    return False


def get_called_tools(response) -> set[str]:
    """Returns set of tool names called in the response."""
    tools = set()
    if not hasattr(response, "messages") or not response.messages:
        return tools
    for msg in response.messages:
        for tc in getattr(msg, "tool_calls", None) or []:
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", None) if fn else None
            if name:
                tools.add(name)
    return tools

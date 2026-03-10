import json
from datetime import datetime, timezone

from src.db.session import get_db
from src.db.models import UsageEvent
import logging

logger = logging.getLogger(__name__)


def log_event(
    user_id: str,
    channel: str,
    event_type: str,
    tool_name: str = None,
    status: str = "success",
    latency_ms: int = None,
    extra_data: dict = None,
):
    try:
        event = UsageEvent(
            user_id=user_id,
            channel=channel,
            event_type=event_type,
            tool_name=tool_name,
            status=status,
            latency_ms=latency_ms,
            extra_data=json.dumps(extra_data) if extra_data else None,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with get_db() as session:
            session.add(event)
    except Exception as e:
        logger.error("Erro ao gravar evento %s para %s: %s", event_type, user_id, e)


def log_run_metrics(user_id: str, channel: str, response):
    """Grava tokens reais e custo do LLM a partir do RunOutput do agno."""
    try:
        metrics = getattr(response, "metrics", None)
        if not metrics:
            return

        input_tokens = getattr(metrics, "input_tokens", 0) or 0
        output_tokens = getattr(metrics, "output_tokens", 0) or 0
        total_tokens = getattr(metrics, "total_tokens", 0) or 0
        reasoning_tokens = getattr(metrics, "reasoning_tokens", 0) or 0
        audio_in = getattr(metrics, "audio_input_tokens", 0) or 0
        audio_out = getattr(metrics, "audio_output_tokens", 0) or 0
        cache_read = getattr(metrics, "cache_read_tokens", 0) or 0
        cost = getattr(metrics, "cost", None)
        duration = getattr(metrics, "duration", None)
        ttft = getattr(metrics, "time_to_first_token", None)
        model_name = getattr(response, "model", None)

        if total_tokens == 0 and input_tokens == 0:
            return

        meta = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "reasoning_tokens": reasoning_tokens,
            "audio_input_tokens": audio_in,
            "audio_output_tokens": audio_out,
            "cache_read_tokens": cache_read,
            "cost_usd": cost,
            "duration_s": round(duration, 3) if duration else None,
            "ttft_s": round(ttft, 3) if ttft else None,
            "model": model_name,
        }

        duration_ms = int(duration * 1000) if duration else None

        log_event(
            user_id=user_id,
            channel=channel,
            event_type="llm_usage",
            status="success",
            latency_ms=duration_ms,
            extra_data=meta,
        )
    except Exception as e:
        logger.error("Erro ao gravar run_metrics para %s: %s", user_id, e)


def log_agent_tools(user_id: str, channel: str, response):
    """Grava tool_called / tool_failed a partir do RunOutput do agno."""
    try:
        if response is None:
            return

        # Preferred path: RunOutput.tools (List[ToolExecution])
        tools = getattr(response, "tools", None)
        if tools:
            for t in tools:
                name = getattr(t, "tool_name", None) or "unknown_tool"
                is_error = bool(getattr(t, "tool_call_error", False))
                log_event(
                    user_id=user_id,
                    channel=channel,
                    event_type="tool_failed" if is_error else "tool_called",
                    tool_name=name,
                    status="error" if is_error else "success",
                )
            return

        # Fallback: scan response.messages for tool-role messages
        messages = getattr(response, "messages", None)
        if not messages:
            return
        for msg in messages:
            role = getattr(msg, "role", "")
            tool_name = getattr(msg, "tool_name", None)
            if not tool_name:
                continue
            if role == "tool" or tool_name:
                is_error = bool(getattr(msg, "tool_call_error", False))
                if not is_error:
                    content_str = str(getattr(msg, "content", "") or "").lower()
                    is_error = any(kw in content_str for kw in ("erro", "error", "falha", "failed", "exception", "traceback"))
                log_event(
                    user_id=user_id,
                    channel=channel,
                    event_type="tool_failed" if is_error else "tool_called",
                    tool_name=tool_name,
                    status="error" if is_error else "success",
                )
    except Exception as e:
        logger.error("Erro ao buscar tools: %s", e)

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
):
    try:
        event = UsageEvent(
            user_id=user_id,
            channel=channel,
            event_type=event_type,
            tool_name=tool_name,
            status=status,
            latency_ms=latency_ms,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with get_db() as session:
            session.add(event)
    except Exception as e:
        logger.error("Erro ao gravar evento %s para %s: %s", event_type, user_id, e)


def log_agent_tools(user_id: str, channel: str, agent):
    try:
        if not agent or getattr(agent, "run_response", None) is None:
            return

        if hasattr(agent.memory, "messages"):
            recent_msgs = agent.memory.messages[-10:]
            for msg in recent_msgs:
                role = getattr(msg, "role", "")
                if not role and isinstance(msg, dict):
                    role = msg.get("role", "")

                if role in ("tool", "function"):
                    name = getattr(msg, "name", getattr(msg, "tool_name", "unknown_tool"))
                    if isinstance(msg, dict):
                        name = msg.get("name", msg.get("tool_name", "unknown_tool"))

                    log_event(
                        user_id=user_id,
                        channel=channel,
                        event_type="tool_called",
                        tool_name=name,
                        status="success",
                    )
    except Exception as e:
        logger.error("Erro ao buscar tools: %s", e)

import json
import logging
import os
import asyncio
from src.endpoints.web import ws_manager

logger = logging.getLogger(__name__)

_asyncpg_pool = None

async def _get_pool():
    global _asyncpg_pool
    if _asyncpg_pool is None:
        url = os.getenv("DATABASE_URL")
        if url:
            url = url.replace("postgresql+psycopg2://", "postgresql://").replace("postgresql+psycopg://", "postgresql://")
            import asyncpg
            _asyncpg_pool = await asyncpg.create_pool(url)
    return _asyncpg_pool

async def broadcast_event(user_id: str, event_type: str, data: dict):
    sent = await ws_manager.send_personal_message(user_id, {"type": event_type, **data})
    
    if not sent:
        pool = await _get_pool()
        if pool:
            payload = json.dumps({"user_id": user_id, "type": event_type, "data": data})
            async with pool.acquire() as conn:
                await conn.execute("SELECT pg_notify('ws_events', $1)", payload)


async def emit_action_log(user_id: str, action: str, summary: str, channel: str = "unknown"):
    """Persiste uma notificacao de acao importante no chat e faz broadcast em tempo real."""
    # Evita ruido/duplicacao no proprio chat web em modo texto.
    # Ex.: usuario cria carousel no chat web e recebe resposta + action_log redundante.
    if channel in {"web", "web_text"}:
        return

    try:
        from src.models.chat_messages import save_message
        display = f"[{channel}] {action}: {summary}"
        await asyncio.to_thread(save_message, user_id, user_id, "system", display)
    except Exception as e:
        logger.error("Erro ao persistir action_log: %s", e)

    await broadcast_event(user_id, "action_log", {
        "action": action,
        "summary": summary,
        "channel": channel,
    })


def emit_action_log_sync(user_id: str, action: str, summary: str, channel: str = "unknown"):
    """Versao sincrona para ser chamada de tools rodando em threads."""
    from src.events import _main_loop
    if _main_loop and _main_loop.is_running():
        try:
            asyncio.run_coroutine_threadsafe(
                emit_action_log(user_id, action, summary, channel),
                _main_loop,
            )
        except Exception as e:
            logger.error("Erro ao emitir action_log sincrono: %s", e)


async def listen_ws_events():
    pool = await _get_pool()
    if pool:
        async with pool.acquire() as conn:
            await conn.add_listener("ws_events", _on_notification)
            while True:
                await asyncio.sleep(3600)

def _on_notification(conn, pid, channel, payload):
    try:
        data = json.loads(payload)
        asyncio.ensure_future(ws_manager.send_personal_message(data["user_id"], {
            "type": data["type"], **data.get("data", {})
        }))
    except Exception as e:
        logger.error("Erro ao processar notificacao: %s", e)

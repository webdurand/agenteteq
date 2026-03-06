import json
import asyncio
from src.endpoints.web import ws_manager
from src.config.system_config import _get_db_url

_asyncpg_pool = None

async def _get_pool():
    global _asyncpg_pool
    if _asyncpg_pool is None:
        url = _get_db_url()
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
        print(f"[BROADCAST] Erro ao processar notificacao: {e}")

import asyncio
from src.endpoints.web import ws_manager

_main_loop = None

def set_main_loop(loop: asyncio.AbstractEventLoop):
    global _main_loop
    _main_loop = loop

async def emit_event(user_id: str, event_type: str, data: dict = None):
    """
    Emite um evento via WebSocket para um usuário específico, se ele estiver conectado.
    """
    if data is None:
        data = {}
        
    try:
        from src.events_broadcast import broadcast_event
        await broadcast_event(user_id, event_type, data)
    except Exception as e:
        print(f"[EVENTS] Erro no broadcast: {e}")
        # Fallback local
        await ws_manager.send_personal_message(user_id, {
            "type": event_type,
            **data
        })

def emit_event_sync(user_id: str, event_type: str, data: dict = None):
    """
    Versão síncrona para ser chamada de ferramentas rodando em threads (ex: tools do Agno).
    """
    if _main_loop and _main_loop.is_running():
        try:
            asyncio.run_coroutine_threadsafe(
                emit_event(user_id, event_type, data), 
                _main_loop
            )
        except Exception as e:
            print(f"[EVENTS] Erro ao emitir evento síncrono: {e}")


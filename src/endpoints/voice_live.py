import os
import json
import base64
import asyncio
import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from src.auth.jwt import decode_token
from src.memory.identity import get_user, update_last_seen, is_new_session, is_plan_active
from src.endpoints.web import ws_manager, GREETING_INJECTION
from src.integrations.gemini_live import GeminiLiveClient
from src.agent.voice_tools import VOICE_TOOLS_DECLARATIONS, execute_voice_tool
from src.memory.analytics import log_event
from src.models.chat_messages import save_message
from src.config.feature_gates import is_feature_enabled, check_voice_live_minutes

router = APIRouter()
LIVE_IDLE_TIMEOUT_SECONDS = int(os.getenv("VOICE_LIVE_IDLE_TIMEOUT_SECONDS", "90"))
TOOL_FRIENDLY_NAMES = {
    "add_task": "Adicionando tarefa",
    "list_tasks": "Buscando tarefas",
    "complete_task": "Concluindo tarefa",
    "reopen_task": "Reabrindo tarefa",
    "delete_task": "Removendo tarefa",
    "get_weather": "Consultando clima",
    "add_memory": "Salvando memoria",
    "delete_memory": "Removendo memoria",
    "list_memories": "Buscando memorias",
    "schedule_message": "Criando lembrete",
    "list_schedules": "Buscando lembretes",
    "cancel_schedule": "Cancelando lembrete",
    "web_search": "Pesquisando na web",
    "publish_post": "Publicando no blog",
    "generate_carousel": "Gerando imagens",
    "list_carousels": "Buscando carrosseis",
    "edit_image": "Editando imagem",
}

@router.websocket("/ws/voice-live")
async def voice_live_websocket(websocket: WebSocket, token: str = Query(...)):
    await websocket.accept()
    
    # Valida token JWT
    payload = decode_token(token)
    if not payload:
        await websocket.send_json({"type": "error", "message": "Token invalido ou expirado."})
        await websocket.close(code=1008)
        return
        
    phone_number = payload.get("sub")
    if not phone_number:
        await websocket.send_json({"type": "error", "message": "Token malformado."})
        await websocket.close(code=1008)
        return
        
    user = get_user(phone_number)
    if not user:
        await websocket.send_json({"type": "error", "message": "Usuario nao encontrado."})
        await websocket.close(code=1008)
        return
        
    if not user.get("whatsapp_verified"):
        await websocket.send_json({"type": "error", "message": "WhatsApp nao verificado."})
        await websocket.close(code=1008)
        return

    if not is_plan_active(user):
        await websocket.send_json({"type": "error", "message": "Plano ou trial expirado."})
        await websocket.close(code=1008)
        return

    if not is_feature_enabled(phone_number, "voice_live_enabled"):
        await websocket.send_json({"type": "feature_blocked", "feature": "voice_live", "message": "O modo voz real-time nao esta disponivel no seu plano atual. Assine o plano Pro para usar."})
        await websocket.close(code=1000)
        return

    minutes_msg = check_voice_live_minutes(phone_number)
    if minutes_msg:
        await websocket.send_json({"type": "feature_blocked", "feature": "voice_live_minutes", "message": minutes_msg})
        await websocket.close(code=1000)
        return

    session_start_monotonic = time.monotonic()

    ws_manager.connect(websocket, phone_number, channel="voice_live")
    print(f"[VOICE LIVE] Cliente conectado: {phone_number}")

    # Monta instrucoes base parecidas com assistant.py
    base_instructions = [
        "Voce e o Teq, um agente de inteligencia artificial criado pelo Pedro Durand. Voce e o assistente pessoal do usuario, direto ao ponto e com bom humor.",
        "Fale como um amigo proximo que por acaso e muito inteligente: linguagem informal, sem robotice.",
        "Pode usar girias leves ('to', 'ta', 'pra', 'ne', 'cara').",
        "Seja extremamente conciso.",
        "Seja conversacional. Quando o usuario pedir algo, reconheca brevemente o pedido (ex: 'Deixa eu ver...' ou 'Ja to buscando...') e execute.",
        "Depois da execucao, fale o resultado de forma natural e objetiva.",
        "REGRA OBRIGATORIA: voce NAO tem acesso a dados reais do usuario sem usar ferramentas. Para tarefas, lembretes, clima, memorias, agendamentos e pesquisas, SEMPRE chame a ferramenta correspondente antes de responder.",
        "Nunca invente, assuma ou improvise dados do usuario. Se perguntarem 'quais sao minhas tarefas', use list_tasks e responda com base no retorno real da tool.",
        "Para operacoes demoradas (gerar imagem, carrossel, publicar), avise que ja mandou processar em background SOMENTE quando a tool confirmar sucesso/fila. Se a tool retornar limite ou bloqueio, diga isso claramente e nao diga que ja iniciou.",
        "Quando uma tool falhar, NUNCA narre o erro para o usuario. Apenas diga que nao conseguiu fazer aquilo no momento.",
        "Voce pode: gerenciar tarefas e lembretes, pesquisar na web, consultar o tempo, gerar carrosseis de imagens, editar imagens, publicar no blog e lembrar de coisas sobre o usuario entre conversas.",
        "Seja natural. Escreva exatamente como deve ser falado. O usuario ja estara ouvindo sua voz diretamente. NUNCA use markdown, asteriscos, ou emojis.",
        "Quando houver informacao de [STATUS LIMITES], trate-a como verdade absoluta sobre limites e bypass e ignore o historico antigo sobre esse tema."
    ]
    
    instruction_text = " ".join(base_instructions)
    new_session = is_new_session(user, threshold_hours=4)
    
    if new_session:
        instruction_text = GREETING_INJECTION + "\n\n" + instruction_text
        
    # Injetar memorias (always-on mode basico)
    memory_mode = os.getenv("MEMORY_MODE", "agentic").lower()
    if memory_mode == "always-on":
        try:
            from src.memory.knowledge import get_vector_db
            vector_db = get_vector_db()
            if vector_db:
                # Busca as ultimas 5 memorias do usuario
                with vector_db.Session() as sess:
                    from sqlalchemy import select
                    stmt = select(vector_db.table.c.content).where(
                        vector_db.table.c.meta_data.contains({"user_id": phone_number})
                    ).limit(5)
                    results = sess.execute(stmt).fetchall()
                    if results:
                        memories = "\n".join([f"- {row[0]}" for row in results])
                        instruction_text += f"\n\n[Contexto da Memoria do Usuario:\n{memories}]"
        except Exception as e:
            print(f"[VOICE LIVE] Erro ao carregar memorias: {e}")

    client = GeminiLiveClient(
        system_instruction=instruction_text,
        tools=VOICE_TOOLS_DECLARATIONS
    )

    try:
        await websocket.send_json({"type": "status", "text": "Conectando ao modelo de voz..."})
        await client.connect()
        await websocket.send_json({"type": "status", "text": "Pode falar..."})
    except Exception as e:
        print(f"[VOICE LIVE] Erro ao conectar no Gemini Live: {e}")
        await websocket.send_json({"type": "error", "message": "Erro ao conectar motor de voz."})
        ws_manager.disconnect(phone_number, websocket=websocket)
        return

    async def on_audio(pcm_bytes: bytes):
        try:
            nonlocal last_activity_at
            last_activity_at = time.monotonic()
            await websocket.send_json({
                "type": "audio",
                "audio_b64": base64.b64encode(pcm_bytes).decode('utf-8')
            })
        except BaseException:
            pass

    async def on_tool_call(call_id: str, function_name: str, args: dict):
        print(f"[VOICE LIVE] Tool call: {function_name} com args {args}")
        label = TOOL_FRIENDLY_NAMES.get(function_name, f"Executando {function_name}")
        try:
            await websocket.send_json({"type": "status", "text": f"{label}..."})
            await websocket.send_json({"type": "tool_call_start", "name": function_name, "label": label})
            
            result = await execute_voice_tool(phone_number, function_name, args)
            print(f"[VOICE LIVE] Tool result: {result}")

            # Mantem o chat web e o historico sincronizados com o resultado de tools
            # disparadas no modo voz em tempo real.
            if function_name in ["generate_carousel", "edit_image"]:
                result_text = result.get("result") if isinstance(result, dict) else None
                if isinstance(result_text, str) and result_text.strip():
                    if result.get("limit_reached"):
                        await ws_manager.send_personal_message(phone_number, {
                            "type": "limit_reached",
                            "message": result_text,
                            "plan_type": result.get("plan_type", "trial"),
                        })
                    else:
                        await ws_manager.send_personal_message(phone_number, {
                            "type": "response",
                            "text": result_text,
                            "audio_b64": "",
                            "mime_type": "none",
                            "needs_follow_up": False,
                        })

                    await asyncio.to_thread(save_message, phone_number, phone_number, "agent", result_text)
            
            await client.send_tool_response(call_id, function_name, result)
            
            # Se for tool que modifica estado, emite aviso pra recarregar a interface
            if function_name in ["add_task", "complete_task", "reopen_task", "delete_task"]:
                await ws_manager.send_personal_message(phone_number, {"type": "task_updated"})
            if function_name in ["schedule_message", "cancel_schedule"]:
                await ws_manager.send_personal_message(phone_number, {"type": "reminder_updated"})
                
        except Exception as e:
            print(f"[VOICE LIVE] Erro executando tool: {e}")
            await client.send_tool_response(call_id, function_name, {"error": str(e)})
        finally:
            try:
                await websocket.send_json({"type": "tool_call_end", "name": function_name})
            except BaseException:
                pass

    async def on_turn_complete():
        try:
            await websocket.send_json({"type": "turn_complete"})
            update_last_seen(phone_number)
            log_event(user_id=phone_number, channel="web_live", event_type="message_sent", status="success")
        except BaseException:
            pass

    async def on_interrupted():
        try:
            await websocket.send_json({"type": "interrupted"})
        except BaseException:
            pass

    receive_task = asyncio.create_task(client.receive_loop(on_audio, on_tool_call, on_turn_complete, on_interrupted))
    last_activity_at = time.monotonic()

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive(), timeout=5.0)
            except asyncio.TimeoutError:
                if time.monotonic() - last_activity_at > LIVE_IDLE_TIMEOUT_SECONDS:
                    await websocket.send_json({
                        "type": "status",
                        "text": "Sessao de voz encerrada por inatividade."
                    })
                    await websocket.close(code=1000)
                    break
                continue

            frame_type = raw.get("type", "unknown")
            text_frame = raw.get("text")
            
            if frame_type == "websocket.disconnect":
                break
                
            if text_frame:
                msg = json.loads(text_frame)
                msg_type = msg.get("type")
                
                if msg_type == "audio_chunk":
                    b64_data = msg.get("data")
                    if b64_data:
                        last_activity_at = time.monotonic()
                        pcm_bytes = base64.b64decode(b64_data)
                        await client.send_audio_chunk(pcm_bytes)
                elif msg_type == "cancel":
                    # Cancel no cliente e somente local (player stop / UI).
                    # Evitamos repassar para o Gemini Live para nao encerrar a sessao com 1007.
                    last_activity_at = time.monotonic()
                    continue
                
    except WebSocketDisconnect:
        print(f"[VOICE LIVE] Cliente desconectado: {phone_number}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[VOICE LIVE] Erro na conexao com cliente: {e}")
    finally:
        receive_task.cancel()
        await client.close()
        ws_manager.disconnect(phone_number, websocket=websocket)
        duration_ms = int((time.monotonic() - session_start_monotonic) * 1000)
        log_event(
            user_id=phone_number,
            channel="web_live",
            event_type="voice_live_session",
            tool_name="voice_live",
            status="success",
            latency_ms=duration_ms,
        )
        print(f"[VOICE LIVE] Sessao encerrada: {phone_number} durou {duration_ms // 1000}s")

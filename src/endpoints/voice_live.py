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
from src.agent.voice_tools import get_voice_tools_for_user, execute_voice_tool
from src.memory.analytics import log_event
from src.models.chat_messages import save_message
from src.config.feature_gates import is_feature_enabled, check_voice_live_minutes
from src.utils.privacy import mask_phone
import logging

logger = logging.getLogger(__name__)

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
    "send_to_channel": "Enviando para outro canal",
    "get_brand_profile": "Consultando branding",
    "update_brand_profile": "Salvando branding",
    "list_brand_profiles": "Buscando perfis de marca",
    # Social monitoring
    "preview_account": "Buscando perfil social",
    "track_account": "Salvando conta para monitoramento",
    "untrack_account": "Removendo monitoramento",
    "list_tracked_accounts": "Buscando contas monitoradas",
    "get_account_insights": "Analisando conteudo",
    "get_trending_content": "Buscando conteudo em alta",
    "analyze_posts": "Analisando posts",
    "create_content_script": "Criando roteiro de conteudo",
    "toggle_alerts": "Configurando alertas",
    # Research & Web
    "fetch_page": "Lendo pagina web",
    "deep_research": "Pesquisando em profundidade",
    # Carousel presets
    "save_carousel_preset": "Salvando preset de carrossel",
    "list_carousel_presets": "Buscando presets",
    # Google integrations
    "read_emails": "Lendo emails",
    "get_calendar_events": "Consultando agenda",
    "create_calendar_event": "Criando evento na agenda",
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
    logger.info("[VOICE LIVE] Cliente conectado: %s", mask_phone(phone_number))

    # Monta instrucoes base parecidas com assistant.py
    base_instructions = [
        "Voce e o Teq, um agente de inteligencia artificial criado pelo Pedro Durand. Voce e o assistente pessoal do usuario, direto ao ponto e com bom humor.",
        "Fale como um amigo proximo que por acaso e muito inteligente: linguagem informal, sem robotice.",
        "Pode usar girias leves ('to', 'ta', 'pra', 'ne', 'cara').",
        "Seja extremamente conciso.",
        "REGRA DE FLUXO: ANTES de chamar qualquer ferramenta, SEMPRE fale uma frase curta reconhecendo o pedido (ex: 'Beleza, vou criar isso pra voce...', 'Deixa eu ver...', 'Ja to buscando...'). Depois da execucao, fale o resultado de forma natural e objetiva.",
        "REGRA OBRIGATORIA: voce NAO tem acesso a dados reais do usuario sem usar ferramentas. Para tarefas, lembretes, clima, memorias, agendamentos e pesquisas, SEMPRE chame a ferramenta correspondente antes de responder.",
        "Nunca invente, assuma ou improvise dados do usuario. Se perguntarem 'quais sao minhas tarefas', use list_tasks e responda com base no retorno real da tool.",
        "Para operacoes demoradas (gerar imagem, carrossel, publicar), avise que ja mandou processar em background SOMENTE quando a tool confirmar sucesso/fila. Se a tool retornar limite ou bloqueio, diga isso claramente e nao diga que ja iniciou.",
        "Quando uma tool falhar, NUNCA narre o erro para o usuario. Apenas diga que nao conseguiu fazer aquilo no momento.",
        "Voce pode: gerenciar tarefas e lembretes, pesquisar na web (busca rapida e pesquisa aprofundada), ler paginas web, consultar o tempo, gerar carrosseis de imagens, editar imagens, publicar no blog, monitorar contas de redes sociais (Instagram e YouTube), analisar posts e criar roteiros de conteudo, gerenciar presets de carrossel, configurar branding, e lembrar de coisas sobre o usuario entre conversas.",
        "SOCIAL MONITORING POR VOZ: Quando o usuario mencionar um perfil de rede social, use preview_account para buscar e descrever o perfil. "
        "Plataformas suportadas: instagram e youtube. Passe platform='instagram' ou platform='youtube' conforme o caso. "
        "Depois pergunte se quer salvar para monitoramento. Use track_account para salvar. "
        "Use get_trending_content para ver o que bomba. Use get_account_insights para analises detalhadas. "
        "Use create_content_script para gerar roteiros inspirados em referencias. "
        "ALERTAS: Apos salvar uma conta, ofereca ativar alertas com toggle_alerts. NAO ative automaticamente, pergunte primeiro.",
        "PESQUISA APROFUNDADA: Use deep_research quando o usuario pedir pesquisa detalhada, investigacao ou analise de um tema complexo. "
        "Para buscas rapidas, use web_search. Use fetch_page para ler o conteudo de links especificos.",
        "PRESETS DE CARROSSEL: Use save_carousel_preset para salvar estilos de carrossel que o usuario gostar. "
        "Use list_carousel_presets para listar presets salvos. NAO salve presets automaticamente, pergunte primeiro.",
        "REGRA DE IMAGENS E CARROSSEL: Para gerar imagens ou carrossel, use a tool generate_carousel passando title, description e num_slides. "
        "CARROSSEL (REGRA CRITICA): Quando o usuario pedir um carrossel (multiplas imagens), NUNCA gere direto. Siga este fluxo: "
        "1. Se o tema for vago, pergunte o objetivo, publico e tom. "
        "2. Descreva verbalmente o plano: 'Vou fazer assim: slide 1 seria a capa com tal titulo, slide 2 sobre tal tema, e o ultimo com um convite pra acao. O que acha?' "
        "3. Apos confirmacao do usuario, chame generate_carousel. "
        "ESTRUTURA: Slide 1 = capa impactante, slides do meio = desenvolvimento com 1 ponto de valor cada, ultimo slide = fechamento com CTA. "
        "O backend cuida de expandir os prompts detalhados a partir da description.",
        "Seja natural. Escreva exatamente como deve ser falado. O usuario ja estara ouvindo sua voz diretamente. NUNCA use markdown, asteriscos, ou emojis.",
        "Quando houver informacao de [STATUS LIMITES], trate-a como verdade absoluta sobre limites e bypass e ignore o historico antigo sobre esse tema.",
        "CROSS-CHANNEL (OBRIGATORIO): Quando o usuario mencionar QUALQUER canal de destino ('manda no zap', 'envia no whatsapp', 'manda na web', 'manda nos dois'), voce DEVE passar o parametro delivery_channel na tool. "
        "Para TEXTO use send_to_channel. Para IMAGENS use delivery_channel em generate_carousel ou edit_image. "
        "Mapeamento: 'whatsapp'/'zap'/'wpp' -> delivery_channel='whatsapp'. 'web'/'aqui' -> delivery_channel='web'. 'ambos'/'nos dois' -> delivery_channel='ambos'. "
        "Se o usuario NAO mencionar canal, NAO passe delivery_channel (entrega no canal atual). "
        "NUNCA ignore um pedido explicito de canal."
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
            logger.error("[VOICE LIVE] Erro ao carregar memorias: %s", e)

    voice_tools = get_voice_tools_for_user(phone_number)

    client = GeminiLiveClient(
        system_instruction=instruction_text,
        tools=voice_tools
    )

    try:
        await websocket.send_json({"type": "status", "text": "Conectando ao modelo de voz..."})
        await client.connect()
        await websocket.send_json({"type": "status", "text": "Pode falar..."})
    except Exception as e:
        logger.error("[VOICE LIVE] Erro ao conectar no Gemini Live: %s", e)
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
        logger.info("[VOICE LIVE] Tool call: %s com args %s", function_name, args)
        label = TOOL_FRIENDLY_NAMES.get(function_name, f"Executando {function_name}")
        try:
            await websocket.send_json({"type": "status", "text": f"{label}..."})
            await websocket.send_json({"type": "tool_call_start", "name": function_name, "label": label})
            
            result = await execute_voice_tool(phone_number, function_name, args)
            logger.info("[VOICE LIVE] Tool result: %s", result)
            log_event(user_id=phone_number, channel="web_live", event_type="tool_called", tool_name=function_name, status="success")

            # Mantem o chat web e o historico sincronizados com o resultado de tools
            # disparadas no modo voz em tempo real.
            if function_name in ["generate_carousel", "edit_image"]:
                result_text = result.get("result") if isinstance(result, dict) else None
                if isinstance(result_text, str) and result_text.strip():
                    if result.get("limit_reached"):
                        await ws_manager.send_personal_message(phone_number, {
                            "type": "limit_reached",
                            "message": result_text,
                            "plan_type": result.get("plan_type", "free"),
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
            logger.error("[VOICE LIVE] Erro executando tool: %s", e)
            log_event(user_id=phone_number, channel="web_live", event_type="tool_failed", tool_name=function_name, status="error")
            await client.send_tool_response(call_id, function_name, {"error": str(e)})
        finally:
            try:
                await websocket.send_json({"type": "tool_call_end", "name": function_name})
            except BaseException:
                pass

    async def on_turn_complete():
        nonlocal turn_active
        try:
            turn_active = False
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

    turn_active = False  # flag p/ logar message_received 1x por turno de fala

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
                        if not turn_active:
                            turn_active = True
                            log_event(user_id=phone_number, channel="web_live", event_type="message_received", status="success")
                        pcm_bytes = base64.b64decode(b64_data)
                        await client.send_audio_chunk(pcm_bytes)
                elif msg_type == "cancel":
                    # Cancel no cliente e somente local (player stop / UI).
                    # Evitamos repassar para o Gemini Live para nao encerrar a sessao com 1007.
                    last_activity_at = time.monotonic()
                    continue
                
    except WebSocketDisconnect:
        logger.info("[VOICE LIVE] Cliente desconectado: %s", mask_phone(phone_number))
    except Exception as e:
        import traceback

        traceback.print_exc()
        logger.error("[VOICE LIVE] Erro na conexao com cliente: %s", e)
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
        logger.info("[VOICE LIVE] Sessao encerrada: %s durou %ss", mask_phone(phone_number), duration_ms // 1000)

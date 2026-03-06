import os
import time
import hashlib
import json
import asyncio
from fastapi import APIRouter, Request, HTTPException, Query, BackgroundTasks

from src.integrations.whatsapp import whatsapp_client
from src.integrations.transcriber import transcriber
from src.integrations.status_notifier import StatusNotifier
from src.agent.assistant import get_assistant
from src.agent.response_utils import extract_final_response, split_whatsapp_messages
from src.memory.identity import get_user, create_user, update_user_name, update_last_seen, is_new_session
from src.memory.knowledge import get_vector_db
from src.memory.extractor import extract_and_save_facts
from src.tools.memory_manager import add_memory
from src.tools.web_search import create_web_search_tool, create_fetch_page_tool
from src.tools.deep_research import create_deep_research_tool
from src.memory.analytics import log_event, log_agent_tools

router = APIRouter()

VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "meu_token_super_secreto")

# Cache para evitar processamento duplicado da mesma mensagem
processed_message_ids = {}

def deduplicate(dedup_key: str) -> bool:
    global processed_message_ids
    if dedup_key in processed_message_ids:
        return True
    processed_message_ids[dedup_key] = time.time()
    if len(processed_message_ids) > 1000:
        sorted_keys = sorted(processed_message_ids.keys(), key=lambda k: processed_message_ids[k])
        for k in sorted_keys[:-500]:
            del processed_message_ids[k]
    return False

@router.get("/webhook/whatsapp")
def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    """
    Endpoint para verificação do webhook da Meta.
    """
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        print("[WEBHOOK] Webhook verificado com sucesso!")
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Token de verificação inválido")

@router.post("/webhook/whatsapp")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Endpoint imperativo que recebe as mensagens do WhatsApp (compatível com Meta e Evolution API).
    """
    try:
        data = await request.json()
    except Exception as e:
        print(f"[ERROR] Falha ao ler JSON do request: {e}")
        return {"status": "error"}

    try:
        events = parse_webhook_payload(data)
        for event in events:
            # Pula status, acks não processáveis ou mensagens enviadas por mim
            if not event.get("should_process", False):
                reason = event.get("skip_reason", "Desconhecido")
                if reason not in ["Status message", "Enviado por mim", "Protocol message"] and not reason.startswith("Evento não processável"):
                    print(f"[WEBHOOK] Evento ignorado. Motivo: {reason} | ID: {event.get('id', 'N/A')}")
                continue
                
            dedup_key = event["dedup_key"]
            if deduplicate(dedup_key):
                print(f"[WEBHOOK] Mensagem duplicada ignorada no Dedup. Chave: {dedup_key}")
                continue
                
            print(f"[WEBHOOK] Nova mensagem enfileirada. De: {event['from_number']} | Tipo: {event['type']} | ID: {event['id']}")
            background_tasks.add_task(orchestrate_message, event)
    except Exception as e:
        print(f"[ERROR] Falha ao processar webhook payload: {e}")
        
    return {"status": "success"}

def parse_webhook_payload(data: dict) -> list:
    """Extrai e normaliza os eventos de ambos os providers (Meta/Evolution)."""
    events = []
    
    # Padrão Meta API
    if "entry" in data:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                
                if "messages" in value:
                    for msg in value["messages"]:
                        from_number = msg.get("from")
                        msg_id = msg.get("id")
                        msg_type = msg.get("type", "unknown")
                        
                        wa_id = from_number
                        if "contacts" in value and len(value["contacts"]) > 0:
                            wa_id = value["contacts"][0].get("wa_id", from_number)
                            
                        content_str = ""
                        if msg_type == "text":
                            content_str = msg.get("text", {}).get("body", "")
                        
                        dedup_key = f"meta_{msg_id}_{hashlib.md5(content_str.encode()).hexdigest()}"
                        
                        events.append({
                            "provider": "meta",
                            "should_process": True,
                            "dedup_key": dedup_key,
                            "id": msg_id,
                            "from_number": wa_id,
                            "type": msg_type,
                            "raw_message": msg
                        })
                elif "statuses" in value:
                    for status in value["statuses"]:
                        events.append({
                            "should_process": False,
                            "skip_reason": "Status message"
                        })
                        
    # Padrão Evolution API
    else:
        evo_data = data.get("data", data)
        event_type = data.get("event")
        
        if event_type == "messages.upsert":
            if "key" in evo_data and "message" in evo_data:
                key = evo_data["key"]
                
                if key.get("fromMe"):
                    events.append({"should_process": False, "skip_reason": "Enviado por mim"})
                    return events
                    
                msg_type = evo_data.get("messageType")
                if msg_type == "protocolMessage":
                    events.append({"should_process": False, "skip_reason": "Protocol message"})
                    return events
                    
                if msg_type not in ["conversation", "extendedTextMessage", "audioMessage"]:
                    events.append({"should_process": False, "skip_reason": f"Tipo não suportado: {msg_type}"})
                    return events
                    
                msg_id = key.get("id")
                from_number = key.get("remoteJid", "").split("@")[0]
                
                content_str = ""
                msg_content = evo_data.get("message", {})
                if msg_type in ["conversation", "extendedTextMessage"]:
                    content_str = msg_content.get("conversation", "") or msg_content.get("extendedTextMessage", {}).get("text", "")
                    
                dedup_key = f"evo_{msg_id}_{hashlib.md5(content_str.encode()).hexdigest()}"
                
                normalized_type = "unknown"
                normalized_msg = {}
                
                if msg_type in ["conversation", "extendedTextMessage"]:
                    normalized_type = "text"
                    normalized_msg = {"text": {"body": content_str}}
                    
                    # Extrair mensagem citada (se houver)
                    quoted_msg = msg_content.get("extendedTextMessage", {}).get("contextInfo", {}).get("quotedMessage", {})
                    if quoted_msg:
                        quoted_text = quoted_msg.get("conversation", "") or quoted_msg.get("extendedTextMessage", {}).get("text", "")
                        if quoted_text:
                            normalized_msg["quoted_text"] = quoted_text
                elif msg_type == "audioMessage":
                    normalized_type = "audio"
                    base64_data = evo_data.get("base64", "") or msg_content.get("base64", "")
                    if not base64_data:
                        # getBase64FromMediaMessage exige key + message para identificar a mídia
                        base64_data = json.dumps({"message": {"key": key, "message": msg_content}})
                    normalized_msg = {"audio": {"id": base64_data}}
                    
                events.append({
                    "provider": "evolution",
                    "should_process": True,
                    "dedup_key": dedup_key,
                    "id": msg_id,
                    "from_number": from_number,
                    "type": normalized_type,
                    "raw_message": normalized_msg
                })
        else:
            events.append({"should_process": False, "skip_reason": f"Evento não processável: {event_type}"})
            
    return events


GREETING_INJECTION = (
    "[INSTRUCAO DE SISTEMA: O usuario ficou mais de 4 horas sem enviar mensagens e optou por comecar uma conversa nova. "
    "Comece com uma saudacao descontraida. "
    "ANTES de responder, consulte suas memorias (search_knowledge) para saber quais informacoes o usuario quer no cumprimento. "
    "Por padrao (sem preferencias salvas), inclua: previsao do tempo (use get_weather — busque a cidade nas memorias; "
    "se nao souber, pergunte de forma natural) e tarefas pendentes (use list_tasks). "
    "Integre tudo de forma fluida e casual, sem parecer uma lista robotica. "
    "Mensagem real do usuario: ]"
)

CONTINUATION_INJECTION = (
    "[INSTRUCAO DE SISTEMA: O usuario quer continuar a conversa anterior. "
    "Consulte o historico da sessao e mencione em 1 linha de forma casual o que voces estavam discutindo "
    "(ex: 'ah certo, a gente tava falando de [assunto]...'), "
    "depois responda a mensagem do usuario normalmente. "
    "Mensagem do usuario: ]"
)

# Guarda a mensagem original do usuario enquanto aguarda a resposta de 'continuar ou novo?'
# Chave: numero do usuario | Valor: texto da mensagem original
pending_session_choices: dict[str, str] = {}

async def orchestrate_message(event: dict):
    from_number = event["from_number"]
    message_id = event["id"]
    msg_type = event["type"]
    raw_msg = event["raw_message"]
    
    try:
        user = get_user(from_number)
        
        print(f"[PROCESS] Iniciando orquestracao da mensagem {message_id} de {from_number}")
        
        # Fluxo de Registro (Deterministico)
        if not user or not user.get("whatsapp_verified"):
            print(f"[ONBOARDING] Usuario nao verificado ou inexistente: {from_number}. Solicitando cadastro.")
            frontend_url = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
            await whatsapp_client.send_text_message(
                from_number, 
                f"Para falar comigo, é necessário fazer cadastro em {frontend_url}", 
                reply_to_message_id=message_id
            )
            return

        # --- Resposta ao "continuar ou novo?" ---
        # Prioridade maxima: usuario estava aguardando resposta da pergunta de sessao
        if from_number in pending_session_choices and msg_type == "text":
            original_message = pending_session_choices.pop(from_number)
            response_text = raw_msg["text"]["body"].strip().lower()

            yes_keywords = {"sim", "s", "yes", "continuar", "continua", "pode", "bora", "claro", "vamos", "quero"}
            is_yes = any(response_text == kw or response_text.startswith(kw + " ") for kw in yes_keywords)

            injection = CONTINUATION_INJECTION if is_yes else GREETING_INJECTION
            log_label = "continuacao" if is_yes else "nova sessao (usuario recusou)"
            print(f"[SESSION] Usuario {from_number} escolheu: {log_label}.")

            await whatsapp_client.mark_message_as_read_and_typing(message_id, from_number, is_audio=False)
            notifier = None
            search_tools = [
                create_web_search_tool(notifier),
                create_fetch_page_tool(notifier),
                create_deep_research_tool(notifier, from_number),
            ]
            agent = get_assistant(session_id=from_number, extra_tools=search_tools)
            await process_text_message(from_number, message_id, {"text": {"body": original_message}}, agent, injection=injection)
            update_last_seen(from_number)
            return

        # --- Deteccao de nova sessao (>4h sem contato) ---
        if is_new_session(user, threshold_hours=4):
            if msg_type == "text":
                # Guarda a mensagem original e pergunta ao usuario o que prefere
                original_text = raw_msg["text"]["body"]
                pending_session_choices[from_number] = original_text
                update_last_seen(from_number)  # Evita re-disparar a pergunta na resposta seguinte
                print(f"[SESSION] Nova sessao detectada para {from_number}. Perguntando ao usuario.")
                await whatsapp_client.send_text_message(
                    from_number,
                    "Ei, passou um tempinho desde nossa ultima conversa 👀 Quer continuar de onde a gente parou, ou prefere comecar uma conversa nova?",
                    reply_to_message_id=message_id,
                )
                return
            else:
                # Audio: pula a pergunta, vai direto pro greeting
                print(f"[SESSION] Nova sessao (audio) para {from_number}. Aplicando greeting injection.")
                await whatsapp_client.mark_message_as_read_and_typing(message_id, from_number, is_audio=False)
                notifier = StatusNotifier(to_number=from_number, reply_to_message_id=message_id)
                search_tools = [
                    create_web_search_tool(notifier),
                    create_fetch_page_tool(notifier),
                    create_deep_research_tool(notifier, from_number),
                ]
                agent = get_assistant(session_id=from_number, extra_tools=search_tools)
                await process_audio_message(from_number, message_id, raw_msg, agent, injection=GREETING_INJECTION)
                update_last_seen(from_number)
                return

        # --- Fluxo normal (sessao ativa, <4h) ---
        print(f"[OUT] Enviando indicador de digitando para {from_number}")
        await whatsapp_client.mark_message_as_read_and_typing(message_id, from_number, is_audio=False)

        notifier = StatusNotifier(to_number=from_number, reply_to_message_id=message_id)
        search_tools = [
            create_web_search_tool(notifier),
            create_fetch_page_tool(notifier),
            create_deep_research_tool(notifier, from_number),
        ]

        agent = get_assistant(session_id=from_number, extra_tools=search_tools)
        
        if msg_type == "audio":
            await process_audio_message(from_number, message_id, raw_msg, agent)
        elif msg_type == "text":
            await process_text_message(from_number, message_id, raw_msg, agent)

        update_last_seen(from_number)
            
    except Exception as e:
        print(f"[ERROR] Falha na orquestracao para {from_number}: {e}")
        try:
            if from_number not in ["16315551181", "16505551111"]:
                await whatsapp_client.send_text_message(from_number, "Eita, deu um erro interno aqui. Tenta de novo em breve!", reply_to_message_id=message_id)
        except Exception as e2:
            print(f"[ERROR] Falha ao enviar erro para WhatsApp: {e2}")

async def process_audio_message(from_number: str, message_id: str, raw_msg: dict, agent, injection: str | None = None):
    start_time = time.time()
    log_event(user_id=from_number, channel="whatsapp", event_type="message_received", status="success")
    audio_id = raw_msg["audio"]["id"]
    print(f"[PROCESS] Baixando audio...")
    media_url = await whatsapp_client.get_media_url(audio_id)
    audio_bytes = await whatsapp_client.download_media(media_url) if media_url else b""

    if not audio_bytes:
        print(f"[PROCESS] Audio vazio ou falha no download para {from_number}. Abortando.")
        if from_number not in ["16315551181", "16505551111"]:
            await whatsapp_client.send_text_message(
                from_number,
                "Não consegui processar esse áudio 😕 Pode tentar enviar de novo? Se quiser, pode escrever também!",
                reply_to_message_id=message_id,
            )
        return
    
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    
    if provider == "gemini":
        from agno.media import Audio
        base_prompt = "O usuario enviou este audio. Responda naturalmente ao que foi dito."
        if injection:
            base_prompt = injection + " " + base_prompt
        print(f"[PROCESS] Enviando audio multimodal para Gemini")
        response = agent.run(base_prompt, audio=[Audio(content=audio_bytes)], knowledge_filters={"user_id": from_number})
        final_text = extract_final_response(response)
        asyncio.create_task(asyncio.to_thread(extract_and_save_facts, from_number, "Audio do usuario", final_text))
    else:
        print(f"[PROCESS] Transcrevendo audio com: {provider}")
        transcription = await transcriber.transcribe(audio_bytes)
        print(f"[PROCESS] Transcricao concluida: {transcription[:100]}...")
        
        prompt = f"O usuario enviou um audio com a seguinte transcricao:\n\n{transcription}"
        if injection:
            prompt = injection + "\n\n" + prompt
        response = agent.run(prompt, knowledge_filters={"user_id": from_number})
        log_agent_tools(from_number, "whatsapp", agent)
        final_text = extract_final_response(response)
        asyncio.create_task(asyncio.to_thread(extract_and_save_facts, from_number, transcription, final_text))
    
    if from_number not in ["16315551181", "16505551111"]:
        print(f"[OUT] Enviando resposta para {from_number}")
        parts = split_whatsapp_messages(final_text)
        for i, part in enumerate(parts):
            reply_id = message_id if i == 0 else None
            await whatsapp_client.send_text_message(from_number, part, reply_to_message_id=reply_id)
        latency = int((time.time() - start_time) * 1000)
        log_event(user_id=from_number, channel="whatsapp", event_type="message_sent", status="success", latency_ms=latency)

async def process_text_message(from_number: str, message_id: str, raw_msg: dict, agent, injection: str | None = None):
    start_time = time.time()
    log_event(user_id=from_number, channel="whatsapp", event_type="message_received", status="success")
    text_body = raw_msg["text"]["body"]
    
    quoted = raw_msg.get("quoted_text")
    if quoted:
        text_body = f'[Mensagem citada pelo usuario: "{quoted}"]\n\n{text_body}'
        
    print(f"[PROCESS] Texto recebido: {text_body[:50]}...")

    # Atalho deterministico para pedidos diretos de lembrete rapido (ex: "me avisa daqui 5 min").
    # Evita falso-positivo de "confirmei mas nao agendou" quando o LLM responde sem chamar tool.
    if not injection:
        try:
            from src.tools.reminder_shortcuts import try_schedule_quick_reminder

            shortcut_msg = try_schedule_quick_reminder(
                user_phone=from_number,
                text=text_body,
                notification_channel="whatsapp_text",
            )
            if shortcut_msg:
                if from_number not in ["16315551181", "16505551111"]:
                    await whatsapp_client.send_text_message(from_number, shortcut_msg, reply_to_message_id=message_id)
                    latency = int((time.time() - start_time) * 1000)
                    log_event(user_id=from_number, channel="whatsapp", event_type="message_sent", status="success", latency_ms=latency)
                return
        except Exception as e:
            print(f"[SHORTCUT] Falha no atalho de lembrete rapido: {e}")
    
    # Always-on memory injection
    memory_mode = os.getenv("MEMORY_MODE", "agentic").lower()
    if memory_mode == "always-on":
        vector_db = get_vector_db()
        if vector_db:
            try:
                results = vector_db.search(query=text_body, limit=3, filters={"user_id": from_number})
                if results:
                    memories = "\n".join([f"- {doc.content}" for doc in results])
                    context_text = f"\n\n[Contexto da Memoria para considerar:\n{memories}]"
                    text_body += context_text
            except Exception as e:
                print(f"[ERROR] Falha ao buscar memorias always-on: {e}")

    if injection:
        text_body = injection + "\n\n" + text_body
    
    response = agent.run(text_body, knowledge_filters={"user_id": from_number})
    log_agent_tools(from_number, "whatsapp", agent)
    final_text = extract_final_response(response)
    asyncio.create_task(asyncio.to_thread(extract_and_save_facts, from_number, text_body, final_text))
    
    if from_number not in ["16315551181", "16505551111"]:
        print(f"[OUT] Enviando resposta para {from_number}")
        parts = split_whatsapp_messages(final_text)
        for i, part in enumerate(parts):
            reply_id = message_id if i == 0 else None
            await whatsapp_client.send_text_message(from_number, part, reply_to_message_id=reply_id)
        latency = int((time.time() - start_time) * 1000)
        log_event(user_id=from_number, channel="whatsapp", event_type="message_sent", status="success", latency_ms=latency)

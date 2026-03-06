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
from src.tools.web_search import create_web_search_tool, create_fetch_page_tool, create_explore_site_tool
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

# Buffer global para agrupar mensagens (Debounce Universal)
message_buffer = {}
BUFFER_TIMEOUT = 3.0

async def flush_buffer(from_number: str, background_tasks: BackgroundTasks):
    await asyncio.sleep(BUFFER_TIMEOUT)
    if from_number in message_buffer:
        data = message_buffer.pop(from_number)
        events = data.get("events", [])
        if events:
            aggregated = aggregate_events(events)
            background_tasks.add_task(orchestrate_message, aggregated)

def aggregate_events(events: list) -> dict:
    base = events[0].copy()
    texts = []
    audios = []
    images = []
    message_ids = []
    
    for ev in events:
        message_ids.append(ev["id"])
        msg_type = ev["type"]
        raw = ev["raw_message"]
        
        if msg_type == "text":
            text_body = raw.get("text", {}).get("body", "")
            quoted = raw.get("quoted_text")
            if quoted:
                text_body = f'[Mensagem citada pelo usuario: "{quoted}"]\n\n{text_body}'
            if text_body:
                texts.append(text_body)
        elif msg_type == "audio":
            audios.append(raw["audio"]["id"])
        elif msg_type == "image":
            images.append({
                "id": raw["image"]["id"],
                "caption": raw["image"].get("caption", "")
            })
            if raw["image"].get("caption"):
                texts.append(raw["image"]["caption"])

    base["id"] = message_ids[0]  # usa o id da primeira mensagem para replies
    base["all_ids"] = message_ids
    base["aggregated_text"] = "\n\n".join(texts)
    base["raw_message"] = {
        "images": images,
        "all_audios": audios,
        "text": {"body": base["aggregated_text"]}
    }
    # Mantém o type base consistente, mas a info real está no raw_message agrupado
    return base

@router.get("/webhook/whatsapp")
def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        print("[WEBHOOK] Webhook verificado com sucesso!")
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Token de verificação inválido")

@router.post("/webhook/whatsapp")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
    except Exception as e:
        print(f"[ERROR] Falha ao ler JSON do request: {e}")
        return {"status": "error"}

    try:
        events = parse_webhook_payload(data)
        for event in events:
            if not event.get("should_process", False):
                reason = event.get("skip_reason", "Desconhecido")
                if reason not in ["Status message", "Enviado por mim", "Protocol message"] and not reason.startswith("Evento não processável"):
                    print(f"[WEBHOOK] Evento ignorado. Motivo: {reason} | ID: {event.get('id', 'N/A')}")
                continue
                
            dedup_key = event["dedup_key"]
            if deduplicate(dedup_key):
                print(f"[WEBHOOK] Mensagem duplicada ignorada no Dedup. Chave: {dedup_key}")
                continue
                
            from_number = event["from_number"]
            print(f"[WEBHOOK] Nova mensagem enfileirada no buffer. De: {from_number} | Tipo: {event['type']} | ID: {event['id']}")
            
            if from_number not in message_buffer:
                # O typing indicator é enviado na primeira mensagem do buffer
                try:
                    asyncio.create_task(whatsapp_client.mark_message_as_read_and_typing(event["id"], from_number, is_audio=(event["type"]=="audio")))
                except Exception:
                    pass
                
                message_buffer[from_number] = {
                    "events": [event], 
                    "timer": asyncio.create_task(flush_buffer(from_number, background_tasks))
                }
            else:
                message_buffer[from_number]["events"].append(event)
                message_buffer[from_number]["timer"].cancel()
                message_buffer[from_number]["timer"] = asyncio.create_task(flush_buffer(from_number, background_tasks))
                
    except Exception as e:
        print(f"[ERROR] Falha ao processar webhook payload: {e}")
        
    return {"status": "success"}

def parse_webhook_payload(data: dict) -> list:
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
                        elif msg_type == "image":
                            content_str = msg.get("image", {}).get("id", "")
                        elif msg_type == "audio":
                            content_str = msg.get("audio", {}).get("id", "")
                        
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
                    
                if msg_type not in ["conversation", "extendedTextMessage", "audioMessage", "imageMessage"]:
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
                    
                    quoted_msg = msg_content.get("extendedTextMessage", {}).get("contextInfo", {}).get("quotedMessage", {})
                    if quoted_msg:
                        quoted_text = quoted_msg.get("conversation", "") or quoted_msg.get("extendedTextMessage", {}).get("text", "")
                        if quoted_text:
                            normalized_msg["quoted_text"] = quoted_text
                elif msg_type == "audioMessage":
                    normalized_type = "audio"
                    base64_data = evo_data.get("base64", "") or msg_content.get("base64", "")
                    if not base64_data:
                        base64_data = json.dumps({"message": {"key": key, "message": msg_content}})
                    normalized_msg = {"audio": {"id": base64_data}}
                    dedup_key = f"evo_{msg_id}_{hashlib.md5(base64_data[:50].encode()).hexdigest()}"
                elif msg_type == "imageMessage":
                    normalized_type = "image"
                    base64_data = evo_data.get("base64", "") or msg_content.get("base64", "")
                    if not base64_data:
                        base64_data = json.dumps({"message": {"key": key, "message": msg_content}})
                    caption = msg_content.get("imageMessage", {}).get("caption", "")
                    normalized_msg = {"image": {"id": base64_data, "caption": caption}}
                    dedup_key = f"evo_{msg_id}_{hashlib.md5(base64_data[:50].encode()).hexdigest()}"
                    
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

pending_session_choices: dict[str, str] = {}

async def orchestrate_message(event: dict):
    from_number = event["from_number"]
    message_id = event["id"]
    
    try:
        user = get_user(from_number)
        print(f"[PROCESS] Iniciando orquestracao do evento agrupado {message_id} de {from_number}")
        
        if not user or not user.get("whatsapp_verified"):
            print(f"[ONBOARDING] Usuario nao verificado ou inexistente: {from_number}. Solicitando cadastro.")
            frontend_url = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
            await whatsapp_client.send_text_message(
                from_number, 
                f"Para falar comigo, é necessário fazer cadastro em {frontend_url}", 
                reply_to_message_id=message_id
            )
            return

        aggregated_text = event.get("aggregated_text", "")
        
        # --- Resposta ao "continuar ou novo?" ---
        if from_number in pending_session_choices and aggregated_text:
            original_message = pending_session_choices.pop(from_number)
            response_text = aggregated_text.strip().lower()

            yes_keywords = {"sim", "s", "yes", "continuar", "continua", "pode", "bora", "claro", "vamos", "quero"}
            is_yes = any(response_text == kw or response_text.startswith(kw + " ") for kw in yes_keywords)

            injection = CONTINUATION_INJECTION if is_yes else GREETING_INJECTION
            log_label = "continuacao" if is_yes else "nova sessao (usuario recusou)"
            print(f"[SESSION] Usuario {from_number} escolheu: {log_label}.")

            notifier = None
            search_tools = [
                create_web_search_tool(notifier),
                create_fetch_page_tool(notifier),
                create_deep_research_tool(notifier, from_number),
            ]
            agent = get_assistant(session_id=from_number, extra_tools=search_tools)
            
            # Substitui o texto pelo original que estava pendente (o usuario so respondeu sim/nao)
            event["aggregated_text"] = original_message
            await process_aggregated_message(from_number, message_id, event, agent, injection=injection)
            update_last_seen(from_number)
            return

        # --- Deteccao de nova sessao (>4h sem contato) ---
        if is_new_session(user, threshold_hours=4):
            # Se a mensagem só tiver mídia sem texto importante, aplicamos o greeting direto
            if aggregated_text.strip():
                pending_session_choices[from_number] = aggregated_text
                update_last_seen(from_number)
                print(f"[SESSION] Nova sessao detectada para {from_number}. Perguntando ao usuario.")
                await whatsapp_client.send_text_message(
                    from_number,
                    "Ei, passou um tempinho desde nossa ultima conversa 👀 Quer continuar de onde a gente parou, ou prefere comecar uma conversa nova?",
                    reply_to_message_id=message_id,
                )
                return
            else:
                print(f"[SESSION] Nova sessao (sem texto relevante) para {from_number}. Aplicando greeting injection.")
                notifier = StatusNotifier(to_number=from_number, reply_to_message_id=message_id)
                search_tools = [
                    create_web_search_tool(notifier),
                    create_fetch_page_tool(notifier),
                    create_deep_research_tool(notifier, from_number),
                ]
                agent = get_assistant(session_id=from_number, extra_tools=search_tools)
                await process_aggregated_message(from_number, message_id, event, agent, injection=GREETING_INJECTION)
                update_last_seen(from_number)
                return

        # --- Fluxo normal (sessao ativa, <4h) ---
        notifier = StatusNotifier(to_number=from_number, reply_to_message_id=message_id)
        search_tools = [
            create_web_search_tool(notifier),
            create_fetch_page_tool(notifier),
            create_explore_site_tool(notifier),
            create_deep_research_tool(notifier, from_number),
        ]
        agent = get_assistant(session_id=from_number, extra_tools=search_tools)
        
        await process_aggregated_message(from_number, message_id, event, agent)
        update_last_seen(from_number)
            
    except Exception as e:
        print(f"[ERROR] Falha na orquestracao para {from_number}: {e}")
        import traceback
        print(traceback.format_exc())
        try:
            if from_number not in ["16315551181", "16505551111"]:
                await whatsapp_client.send_text_message(from_number, "Eita, deu um erro interno aqui. Tenta de novo em breve!", reply_to_message_id=message_id)
        except Exception:
            pass


async def process_aggregated_message(from_number: str, message_id: str, event: dict, agent, injection: str | None = None):
    start_time = time.time()
    log_event(user_id=from_number, channel="whatsapp", event_type="message_received", status="success")
    
    texts = event.get("aggregated_text", "")
    images = event.get("raw_message", {}).get("images", [])
    audios = event.get("raw_message", {}).get("all_audios", [])
    
    if len(images) > 10:
        try:
            if from_number not in ["16315551181", "16505551111"]:
                await whatsapp_client.send_text_message(from_number, "Só consigo processar até 10 imagens por vez! 😅 Vou analisar apenas as 10 primeiras, tá?", reply_to_message_id=message_id)
        except Exception:
            pass
        images = images[:10]
    
    # 1. Processar audios (transcrever ou enviar p/ multimodal)
    audio_bytes_list = []
    for aud_id in audios:
        media_url = await whatsapp_client.get_media_url(aud_id)
        if media_url:
            a_bytes = await whatsapp_client.download_media(media_url)
            if a_bytes:
                audio_bytes_list.append(a_bytes)
                
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    
    from agno.media import Audio, Image
    agent_images = []
    agent_audios = []
    
    if provider == "gemini":
        for a_bytes in audio_bytes_list:
            agent_audios.append(Audio(content=a_bytes))
    else:
        for a_bytes in audio_bytes_list:
            transcript = await transcriber.transcribe(a_bytes)
            texts += f"\n\n[Áudio transcrito]: {transcript}"
            
    # 2. Processar imagens
    image_bytes_list = []
    for img in images:
        img_id = img["id"]
        media_url = await whatsapp_client.get_media_url(img_id)
        if media_url:
            i_bytes = await whatsapp_client.download_media(media_url)
            if i_bytes:
                image_bytes_list.append(i_bytes)
                agent_images.append(Image(content=i_bytes))
                
    if image_bytes_list:
        from src.tools.image_editor import store_session_images
        store_session_images(from_number, image_bytes_list)
        from src.integrations.image_storage import describe_and_store_images
        asyncio.create_task(describe_and_store_images(from_number, image_bytes_list))
    
    text_body = texts.strip()
    
    if not text_body and not agent_audios and not agent_images:
        return
        
    print(f"[PROCESS] Texto agregado ({len(agent_images)} imgs, {len(agent_audios)} audios): {text_body[:50]}...")
    
    if not injection and text_body:
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
    
    memory_mode = os.getenv("MEMORY_MODE", "agentic").lower()
    if memory_mode == "always-on" and text_body:
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
        
    if not text_body.strip():
        text_body = "O usuário enviou uma ou mais mídias (áudio/imagem). Responda de acordo."

    # Executa o agente
    kwargs = {"knowledge_filters": {"user_id": from_number}}
    if agent_images:
        kwargs["images"] = agent_images
    if agent_audios:
        kwargs["audio"] = agent_audios
        
    response = await asyncio.to_thread(agent.run, text_body, **kwargs)
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

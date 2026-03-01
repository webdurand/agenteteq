import os
import time
import hashlib
import json
import asyncio
from fastapi import APIRouter, Request, HTTPException, Query, BackgroundTasks

from src.integrations.whatsapp import whatsapp_client
from src.integrations.transcriber import transcriber
from src.agent.assistant import get_assistant
from src.memory.identity import get_user, create_user, update_user_name
from src.memory.knowledge import get_vector_db
from src.memory.extractor import extract_and_save_facts
from src.tools.memory_manager import add_memory

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
                if reason not in ["Status message", "Enviado por mim", "Protocol message"]:
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
                elif msg_type == "audioMessage":
                    normalized_type = "audio"
                    base64_data = evo_data.get("base64", "") or msg_content.get("base64", "")
                    if not base64_data:
                        base64_data = json.dumps({"message": msg_content})
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


async def orchestrate_message(event: dict):
    from_number = event["from_number"]
    message_id = event["id"]
    msg_type = event["type"]
    raw_msg = event["raw_message"]
    
    try:
        user = get_user(from_number)
        
        print(f"[PROCESS] Iniciando orquestração da mensagem {message_id} de {from_number}")
        
        # Fluxo de Onboarding
        if not user:
            print(f"[ONBOARDING] Usuário não encontrado no banco local: {from_number}. Iniciando registro.")
            create_user(from_number)
            await whatsapp_client.send_text_message(
                from_number, 
                "Olá! Parece que é a sua primeira vez por aqui. Como você se chama?", 
                reply_to_message_id=message_id
            )
            return
            
        if user["onboarding_step"] == "asking_name":
            if msg_type == "text":
                name = raw_msg["text"]["body"].strip()
                update_user_name(from_number, name)
                print(f"[ONBOARDING] Usuário {from_number} registrado como {name}.")
                add_memory(f"O nome do usuário é {name}", from_number)
                
                await whatsapp_client.send_text_message(
                    from_number, 
                    f"Prazer em te conhecer, {name}! Como posso te ajudar hoje?", 
                    reply_to_message_id=message_id
                )
            else:
                await whatsapp_client.send_text_message(
                    from_number, 
                    "Por favor, digite apenas o seu nome para continuarmos.", 
                    reply_to_message_id=message_id
                )
            return

        print(f"[OUT] Enviando indicador de digitando/gravando para {from_number}")
        await whatsapp_client.mark_message_as_read_and_typing(message_id, from_number, is_audio=False)
        
        agent = get_assistant(session_id=from_number)
        
        if msg_type == "audio":
            await process_audio_message(from_number, message_id, raw_msg, agent)
        elif msg_type == "text":
            await process_text_message(from_number, message_id, raw_msg, agent)
            
    except Exception as e:
        print(f"[ERROR] Falha na orquestração para {from_number}: {e}")
        try:
            if from_number not in ["16315551181", "16505551111"]:
                await whatsapp_client.send_text_message(from_number, "Desculpe, ocorreu um erro interno ao processar sua mensagem.", reply_to_message_id=message_id)
        except Exception as e2:
            print(f"[ERROR] Falha ao enviar erro para WhatsApp: {e2}")

async def process_audio_message(from_number: str, message_id: str, raw_msg: dict, agent):
    audio_id = raw_msg["audio"]["id"]
    print(f"[PROCESS] Baixando áudio...")
    media_url = await whatsapp_client.get_media_url(audio_id)
    audio_bytes = await whatsapp_client.download_media(media_url)
    
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    
    if provider == "gemini":
        from agno.media import Audio
        prompt = "O usuário enviou este áudio. Responda naturalmente ao que foi dito."
        print(f"[PROCESS] Enviando áudio multimodal para Gemini")
        response = agent.run(prompt, audio=[Audio(content=audio_bytes)], knowledge_filters={"user_id": from_number})
        asyncio.create_task(asyncio.to_thread(extract_and_save_facts, from_number, "Áudio do usuário", response.content))
    else:
        print(f"[PROCESS] Transcrevendo áudio com: {provider}")
        transcription = await transcriber.transcribe(audio_bytes)
        print(f"[PROCESS] Transcrição concluída: {transcription[:100]}...")
        
        prompt = f"O usuário enviou um áudio com a seguinte transcrição:\n\n{transcription}"
        response = agent.run(prompt, knowledge_filters={"user_id": from_number})
        asyncio.create_task(asyncio.to_thread(extract_and_save_facts, from_number, transcription, response.content))
    
    if from_number not in ["16315551181", "16505551111"]:
        print(f"[OUT] Enviando resposta para {from_number}")
        await whatsapp_client.send_text_message(from_number, response.content, reply_to_message_id=message_id)

async def process_text_message(from_number: str, message_id: str, raw_msg: dict, agent):
    text_body = raw_msg["text"]["body"]
    print(f"[PROCESS] Texto recebido: {text_body[:50]}...")
    
    # Always-on memory injection
    memory_mode = os.getenv("MEMORY_MODE", "agentic").lower()
    if memory_mode == "always-on":
        vector_db = get_vector_db()
        if vector_db:
            try:
                results = vector_db.search(query=text_body, limit=3, filters={"user_id": from_number})
                if results:
                    memories = "\n".join([f"- {doc.content}" for doc in results])
                    context_text = f"\n\n[Contexto da Memória para considerar:\n{memories}]"
                    text_body += context_text
            except Exception as e:
                print(f"[ERROR] Falha ao buscar memórias always-on: {e}")
    
    response = agent.run(text_body, knowledge_filters={"user_id": from_number})
    asyncio.create_task(asyncio.to_thread(extract_and_save_facts, from_number, text_body, response.content))
    
    if from_number not in ["16315551181", "16505551111"]:
        print(f"[OUT] Enviando resposta para {from_number}")
        await whatsapp_client.send_text_message(from_number, response.content, reply_to_message_id=message_id)

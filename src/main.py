import os
from fastapi import FastAPI, Request, HTTPException, Query, BackgroundTasks
from dotenv import load_dotenv

load_dotenv()

from src.integrations.whatsapp import whatsapp_client
from src.integrations.transcriber import transcriber
from src.agent.assistant import get_assistant
import asyncio
from src.memory.knowledge import get_vector_db
from src.memory.extractor import extract_and_save_facts

import time

app = FastAPI(title="Agente WhatsApp - Diario Teq")

# Cache simples para evitar processamento duplicado da mesma mensagem
processed_message_ids = {}

VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "meu_token_super_secreto")

@app.get("/")
def read_root():
    return {"status": "ok", "message": "API do Agente WhatsApp está rodando!"}

@app.get("/webhook/whatsapp")
def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    """
    Endpoint para verificação do webhook da Meta.
    """
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        print("Webhook verificado com sucesso!")
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Token de verificação inválido")

async def process_whatsapp_message(message: dict, from_number: str):
    message_id = message.get("id")
    try:
        agent = get_assistant(session_id=from_number)
        msg_type = message.get("type")
        
        if msg_type == "audio":
            if message_id:
                print(f"[OUT] Enviando indicador 'gravando áudio...' para {from_number}")
                await whatsapp_client.mark_message_as_read_and_typing(message_id, from_number, is_audio=True)
                
            audio_id = message["audio"]["id"]
            print(f"[PROCESS] Baixando áudio ID/Base64: {audio_id[:50]}")
            media_url = await whatsapp_client.get_media_url(audio_id)
            audio_bytes = await whatsapp_client.download_media(media_url)
            
            provider = os.getenv("LLM_PROVIDER", "openai").lower()
            
            if provider == "gemini":
                from agno.media import Audio
                prompt = "O autor enviou um áudio. Por favor, ouça e prepare uma sugestão de post para o blog, ou me faça perguntas caso falte alguma informação importante no áudio."
                print(f"[PROCESS] Enviando áudio multimodal para o Agente Agno (Gemini)")
                response = agent.run(prompt, audio=[Audio(content=audio_bytes)], knowledge_filters={"user_id": from_number})
                asyncio.create_task(asyncio.to_thread(extract_and_save_facts, from_number, prompt, response.content))
            else:
                print(f"[PROCESS] Iniciando transcrição de áudio com provedor: {provider}")
                transcription = await transcriber.transcribe(audio_bytes)
                print(f"[PROCESS] Transcrição concluída: {transcription[:100]}...")
                
                prompt = f"O autor enviou um áudio com a seguinte transcrição:\n\n{transcription}"
                response = agent.run(prompt, knowledge_filters={"user_id": from_number})
                asyncio.create_task(asyncio.to_thread(extract_and_save_facts, from_number, transcription, response.content))
            
            if from_number in ["16315551181", "16505551111"]:
                print(f"[TESTE LOCAL] O Agente responderia para {from_number}: {response.content}")
            else:
                print(f"[OUT] Enviando resposta (áudio) para {from_number}: {response.content[:100]}...")
                await whatsapp_client.send_text_message(from_number, response.content, reply_to_message_id=message_id)
            
        elif msg_type == "text":
            if message_id:
                print(f"[OUT] Enviando indicador 'digitando...' para {from_number}")
                await whatsapp_client.mark_message_as_read_and_typing(message_id, from_number, is_audio=False)
                
            text_body = message["text"]["body"]
            print(f"[PROCESS] Texto extraído da mensagem: {text_body[:50]}...")
            
            # Injecting context if memory_mode is always-on
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
            
            if from_number in ["16315551181", "16505551111"]:
                print(f"[TESTE LOCAL] O Agente responderia para {from_number}: {response.content}")
            else:
                print(f"[OUT] Enviando texto para {from_number}: {response.content[:100]}...")
                await whatsapp_client.send_text_message(from_number, response.content, reply_to_message_id=message_id)
            
    except Exception as e:
        print(f"[ERROR] Falha ao processar a mensagem de {from_number}: {e}")
        try:
            if from_number in ["16315551181", "16505551111"]:
                print("[TESTE LOCAL] Ignorando envio de mensagem de erro para número dummy.")
            else:
                print(f"[OUT] Enviando mensagem de fallback (erro) para {from_number}")
                await whatsapp_client.send_text_message(from_number, "Desculpe, ocorreu um erro interno ao processar sua mensagem.", reply_to_message_id=message_id)
        except Exception as e2:
            print(f"[ERROR] Falha ao tentar enviar mensagem de fallback para o Whatsapp: {e2}")

@app.post("/webhook/whatsapp")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Endpoint que recebe as mensagens do WhatsApp (compatível com Meta e Evolution API).
    """
    data = await request.json()
    
    try:
        # Padrão Meta API
        if "entry" in data:
            entry = data["entry"][0]
            changes = entry["changes"][0]
            value = changes["value"]
            
            if "messages" in value:
                message = value["messages"][0]
                from_number = message["from"]
                msg_type = message.get("type", "unknown")
                
                wa_id = from_number
                if "contacts" in value and len(value["contacts"]) > 0:
                    wa_id = value["contacts"][0].get("wa_id", from_number)
                
                print(f"[IN] Mensagem recebida (Meta) de {from_number} (wa_id: {wa_id}, Tipo: {msg_type})")
                background_tasks.add_task(process_whatsapp_message, message, wa_id)
            
            elif "statuses" in value:
                status = value["statuses"][0]
                status_val = status.get("status")
                recipient_id = status.get("recipient_id")
                print(f"[STATUS] Mensagem (Meta) para {recipient_id} mudou para: {status_val}")
                if "errors" in status:
                    print(f"[ERROR] Detalhes do erro no status: {status['errors']}")
                    
        # Padrão Evolution API
        else:
            # Evolution pode mandar num wrapper {"event": "messages.upsert", "data": {...}} ou direto
            evo_data = data.get("data", data)
            
            if "key" in evo_data and "message" in evo_data:
                key = evo_data["key"]
                
                if key.get("fromMe"):
                    return {"status": "success"} # Ignora mensagens enviadas por nós mesmos
                    
                message_id = key.get("id")
                
                # Previne duplicidade usando cache de IDs
                if message_id:
                    if message_id in processed_message_ids:
                        print(f"[PROCESS] Mensagem {message_id} ignorada (duplicada/já processada)")
                        return {"status": "success"}
                    processed_message_ids[message_id] = time.time()
                    
                    # Limpa o cache se ficar muito grande para evitar vazamento de memória
                    if len(processed_message_ids) > 1000:
                        # Mantém apenas os 500 mais recentes
                        sorted_keys = sorted(processed_message_ids.keys(), key=lambda k: processed_message_ids[k])
                        for k in sorted_keys[:-500]:
                            del processed_message_ids[k]

                from_number = key.get("remoteJid", "").split("@")[0]
                message_type = evo_data.get("messageType", "")
                
                normalized_message = {
                    "id": message_id,
                    "type": "unknown"
                }
                
                msg_content = evo_data.get("message", {})
                
                if message_type in ["conversation", "extendedTextMessage"]:
                    normalized_message["type"] = "text"
                    text_body = msg_content.get("conversation") or msg_content.get("extendedTextMessage", {}).get("text", "")
                    normalized_message["text"] = {"body": text_body}
                    
                elif message_type == "audioMessage":
                    normalized_message["type"] = "audio"
                    # Se usarmos webhook_base64 no Evolution, ele vem no campo 'base64'
                    base64_data = evo_data.get("base64", "")
                    normalized_message["audio"] = {"id": base64_data}
                
                if normalized_message["type"] != "unknown":
                    print(f"[IN] Mensagem recebida (Evolution) de {from_number} (Tipo: {normalized_message['type']})")
                    background_tasks.add_task(process_whatsapp_message, normalized_message, from_number)

    except Exception as e:
        print(f"[ERROR] Falha ao parsear webhook: {e}")
        
    return {"status": "success"}

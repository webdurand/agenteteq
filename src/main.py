import os
from fastapi import FastAPI, Request, HTTPException, Query, BackgroundTasks
from dotenv import load_dotenv

load_dotenv()

from src.integrations.whatsapp import whatsapp_client
from src.integrations.transcriber import transcriber
from src.agent.assistant import get_assistant

app = FastAPI(title="Agente WhatsApp - Diario Teq")

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
    try:
        agent = get_assistant(session_id=from_number)
        msg_type = message.get("type")
        
        if msg_type == "audio":
            audio_id = message["audio"]["id"]
            media_url = await whatsapp_client.get_media_url(audio_id)
            audio_bytes = await whatsapp_client.download_media(media_url)
            
            provider = os.getenv("LLM_PROVIDER", "openai").lower()
            
            if provider == "gemini":
                # O Gemini suporta áudio nativamente (multimodal)
                from agno.media import Audio
                prompt = "O autor enviou um áudio. Por favor, ouça e prepare uma sugestão de post para o blog, ou me faça perguntas caso falte alguma informação importante no áudio."
                response = agent.run(prompt, audio=[Audio(content=audio_bytes)])
            else:
                # Fallback para serviços de transcrição de terceiros (Whisper, Groq, etc)
                transcription = await transcriber.transcribe(audio_bytes)
                prompt = f"O autor enviou um áudio com a seguinte transcrição:\n\n{transcription}"
                response = agent.run(prompt)
            
            # Checa se é um número dummy do simulador da Meta para não tentar responder e tomar 401
            if from_number in ["16315551181", "16505551111"]:
                print(f"[TESTE LOCAL] O Agente responderia para {from_number}: {response.content}")
            else:
                await whatsapp_client.send_text_message(from_number, response.content)
            
        elif msg_type == "text":
            text_body = message["text"]["body"]
            
            response = agent.run(text_body)
            
            # Checa se é um número dummy do simulador da Meta para não tentar responder e tomar 401
            if from_number in ["16315551181", "16505551111"]:
                print(f"[TESTE LOCAL] O Agente responderia para {from_number}: {response.content}")
            else:
                await whatsapp_client.send_text_message(from_number, response.content)
            
    except Exception as e:
        print(f"Erro ao processar a mensagem: {e}")
        # Apenas tenta enviar se tivermos as credenciais válidas e não for o banco falhando antes
        try:
            # Em modo de teste de webhook da Meta (com números dummy), pular envio
            if from_number in ["16315551181", "16505551111"]:
                print("Modo de teste: Ignorando envio de mensagem de erro para número dummy.")
            else:
                await whatsapp_client.send_text_message(from_number, "Desculpe, ocorreu um erro interno ao processar sua mensagem.")
        except Exception as e2:
            print(f"Erro ao tentar enviar mensagem de fallback para o Whatsapp: {e2}")

@app.post("/webhook/whatsapp")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Endpoint que recebe as mensagens do WhatsApp.
    """
    data = await request.json()
    
    try:
        if "entry" in data:
            entry = data["entry"][0]
            changes = entry["changes"][0]
            value = changes["value"]
            
            if "messages" in value:
                message = value["messages"][0]
                from_number = message["from"]
                
                # Executa o processamento em background para não travar o webhook da Meta
                background_tasks.add_task(process_whatsapp_message, message, from_number)

    except Exception as e:
        print(f"Erro ao parsear webhook: {e}")
        
    return {"status": "success"}

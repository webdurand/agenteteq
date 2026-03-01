import os
import json
import base64
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.agent.assistant import get_assistant
from src.integrations.tts import get_tts
from src.memory.identity import get_user, create_user, update_user_name, update_last_seen, is_new_session
from src.memory.extractor import extract_and_save_facts
from src.tools.memory_manager import add_memory
from src.tools.web_search import create_web_search_tool, create_fetch_page_tool
from src.tools.deep_research import create_deep_research_tool

router = APIRouter()

GREETING_INJECTION = (
    "[INSTRUCAO DE SISTEMA: O usuario ficou mais de 4 horas sem enviar mensagens. "
    "Comece com uma saudacao descontraida. "
    "ANTES de responder, consulte suas memorias (search_knowledge) para saber quais informacoes o usuario quer no cumprimento. "
    "Por padrao (sem preferencias salvas), inclua: previsao do tempo (use get_weather — busque a cidade nas memorias; "
    "se nao souber, pergunte de forma natural) e tarefas pendentes (use list_tasks). "
    "Integre tudo de forma fluida e casual, sem parecer uma lista robotica. "
    "Mensagem real do usuario: ]"
)


class WebSocketNotifier:
    """
    Notificador para a interface web — equivalente ao StatusNotifier do WhatsApp.
    Como agent.run() roda em thread separada via asyncio.to_thread(), usa
    run_coroutine_threadsafe para enviar mensagens ao WebSocket de forma segura.
    """

    def __init__(self, websocket: WebSocket, loop: asyncio.AbstractEventLoop):
        self.websocket = websocket
        self.loop = loop
        self._sent_messages: set[str] = set()

    def notify(self, message: str) -> None:
        if message in self._sent_messages:
            return
        self._sent_messages.add(message)
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.websocket.send_json({"type": "status", "text": message}),
                self.loop,
            )
            future.result(timeout=5)
        except Exception as e:
            print(f"[WS NOTIFIER] Falha ao enviar status '{message[:40]}': {e}")


@router.websocket("/ws/voice/{phone_number}")
async def voice_websocket(websocket: WebSocket, phone_number: str):
    await websocket.accept()
    loop = asyncio.get_event_loop()
    tts = get_tts()

    print(f"[WEB WS] Cliente conectado: {phone_number}")

    try:
        while True:
            raw = await websocket.receive()
            audio_bytes = raw.get("bytes")
            text_frame = raw.get("text")

            if text_frame:
                msg = json.loads(text_frame)
                if msg.get("type") == "name":
                    name = msg.get("value", "").strip()
                    if name:
                        update_user_name(phone_number, name)
                        add_memory(f"O nome do usuario e {name}", phone_number)
                        update_last_seen(phone_number)
                        await websocket.send_json({
                            "type": "onboarding_complete",
                            "text": f"Prazer, {name}! To por aqui pra ajudar no que precisar 😄",
                        })
                continue

            if not audio_bytes:
                continue

            try:
                user = get_user(phone_number)

                if not user:
                    create_user(phone_number)
                    await websocket.send_json({
                        "type": "onboarding",
                        "text": "Oi! Parece que é a sua primeira vez por aqui 👋 Como você se chama?",
                        "needs_name": True,
                    })
                    continue

                if user.get("onboarding_step") == "asking_name":
                    await websocket.send_json({
                        "type": "onboarding",
                        "text": "Me manda só o seu nome pra a gente começar!",
                        "needs_name": True,
                    })
                    continue

                new_session = is_new_session(user, threshold_hours=4)

                await websocket.send_json({"type": "status", "text": "Ouvindo..."})

                notifier = WebSocketNotifier(websocket, loop)
                search_tools = [
                    create_web_search_tool(notifier),
                    create_fetch_page_tool(notifier),
                    create_deep_research_tool(notifier, phone_number),
                ]
                agent = get_assistant(session_id=phone_number, extra_tools=search_tools)

                llm_provider = os.getenv("LLM_PROVIDER", "openai").lower()

                if llm_provider == "gemini":
                    from agno.media import Audio

                    await websocket.send_json({"type": "transcript", "text": "..."})
                    await websocket.send_json({"type": "status", "text": "Pensando..."})

                    base_prompt = "O usuario enviou este audio. Responda naturalmente ao que foi dito."
                    if new_session:
                        base_prompt = GREETING_INJECTION + " " + base_prompt

                    response = await asyncio.to_thread(
                        agent.run,
                        base_prompt,
                        audio=[Audio(content=audio_bytes)],
                        knowledge_filters={"user_id": phone_number},
                    )
                    asyncio.create_task(asyncio.to_thread(
                        extract_and_save_facts, phone_number, "Áudio do usuário", response.content
                    ))
                else:
                    from src.integrations.transcriber import transcriber

                    transcript = await transcriber.transcribe(audio_bytes, filename="audio.webm")
                    await websocket.send_json({"type": "transcript", "text": transcript})
                    await websocket.send_json({"type": "status", "text": "Pensando..."})

                    prompt = f"O usuario enviou um audio com a seguinte transcricao:\n\n{transcript}"
                    if new_session:
                        prompt = GREETING_INJECTION + "\n\n" + prompt

                    response = await asyncio.to_thread(
                        agent.run,
                        prompt,
                        knowledge_filters={"user_id": phone_number},
                    )
                    asyncio.create_task(asyncio.to_thread(
                        extract_and_save_facts, phone_number, transcript, response.content
                    ))

                update_last_seen(phone_number)

                await websocket.send_json({"type": "status", "text": "Gerando áudio..."})

                audio_out, mime_type = await tts.synthesize(response.content)
                audio_b64 = base64.b64encode(audio_out).decode() if audio_out else ""

                await websocket.send_json({
                    "type": "response",
                    "text": response.content,
                    "audio_b64": audio_b64,
                    "mime_type": mime_type,
                })

            except Exception as e:
                print(f"[WEB WS] Erro ao processar mensagem de {phone_number}: {e}")
                await websocket.send_json({"type": "error", "message": "Erro interno. Tenta de novo!"})

    except WebSocketDisconnect:
        print(f"[WEB WS] Cliente desconectado: {phone_number}")

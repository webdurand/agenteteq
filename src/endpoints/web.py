import os
import re
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


_EMOJI_TAIL_RE = re.compile(
    r'[\s\U0001F000-\U0001FAFF\U0001F600-\U0001F64F\U0001F900-\U0001F9FF'
    r'\u2600-\u27BF\uFE00-\uFE0F\u200d\u2764\u2705\u274C\u2728'
    r'\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF*_)!\s]+$'
)


def _needs_follow_up(text: str) -> bool:
    """Detect deterministically whether the agent response expects user input.

    Strips trailing emojis / decorations and checks for question marks
    in the tail of the message — covers cases where the last visible
    sentence is a CTA like "Manda aí pra mim! 😊" but earlier sentences
    contain the actual questions.
    """
    cleaned = _EMOJI_TAIL_RE.sub('', text.strip())
    if cleaned.endswith('?'):
        return True
    tail = cleaned[-300:] if len(cleaned) > 300 else cleaned
    return '?' in tail


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


async def _process_text(websocket, phone_number: str, user_text: str, tts):
    """Processa texto transcrito do browser e devolve resposta via TTS.
    Suporta cancelamento via asyncio.Task.cancel() em qualquer checkpoint.
    """
    from src.memory.knowledge import get_vector_db

    user = get_user(phone_number)
    if not user:
        create_user(phone_number)
        await websocket.send_json({
            "type": "onboarding",
            "text": "Oi! Parece que é a sua primeira vez por aqui 👋 Como você se chama?",
            "needs_name": True,
        })
        return

    if user.get("onboarding_step") == "asking_name":
        await websocket.send_json({
            "type": "onboarding",
            "text": "Me manda só o seu nome pra a gente começar!",
            "needs_name": True,
        })
        return

    new_session = is_new_session(user, threshold_hours=4)
    loop = asyncio.get_event_loop()

    await websocket.send_json({"type": "status", "text": "Pensando..."})

    notifier = WebSocketNotifier(websocket, loop)
    search_tools = [
        create_web_search_tool(notifier),
        create_fetch_page_tool(notifier),
        create_deep_research_tool(notifier, phone_number),
    ]
    agent = get_assistant(session_id=phone_number, extra_tools=search_tools)

    # Always-on memory injection (igual ao whatsapp.py)
    prompt = user_text
    memory_mode = os.getenv("MEMORY_MODE", "agentic").lower()
    if memory_mode == "always-on":
        vector_db = get_vector_db()
        if vector_db:
            try:
                results = vector_db.search(query=user_text, limit=3, filters={"user_id": phone_number})
                if results:
                    memories = "\n".join([f"- {doc.content}" for doc in results])
                    prompt += f"\n\n[Contexto da Memoria para considerar:\n{memories}]"
            except Exception as e:
                print(f"[WEB WS] Falha ao buscar memorias: {e}")

    if new_session:
        prompt = GREETING_INJECTION + "\n\n" + prompt

    # agent.run() roda em thread separada; ao cancelar a Task, a thread termina
    # naturalmente mas TTS e envio da resposta sao descartados.
    response = await asyncio.to_thread(
        agent.run, prompt, knowledge_filters={"user_id": phone_number}
    )

    # Checkpoint 1: verifica cancelamento antes de chamar TTS (API paga)
    await asyncio.sleep(0)

    asyncio.create_task(asyncio.to_thread(
        extract_and_save_facts, phone_number, user_text, response.content
    ))

    update_last_seen(phone_number)
    print(f"[WEB WS] Resposta ({len(response.content)} chars): {response.content[:80]}...")

    await websocket.send_json({"type": "status", "text": "Gerando áudio..."})

    audio_b64 = ""
    mime_type = "audio/wav"
    try:
        audio_out, mime_type = await tts.synthesize(response.content)
        print(f"[WEB WS] TTS: {len(audio_out)} bytes | {mime_type}")
        audio_b64 = base64.b64encode(audio_out).decode() if audio_out else ""
    except Exception as e:
        print(f"[WEB WS] TTS falhou, enviando só texto: {e}")
        mime_type = "browser"

    # Checkpoint 2: verifica cancelamento antes de enviar ao cliente
    await asyncio.sleep(0)

    follow_up = _needs_follow_up(response.content)
    print(f"[WEB WS] needs_follow_up={follow_up}")

    await websocket.send_json({
        "type": "response",
        "text": response.content,
        "audio_b64": audio_b64,
        "mime_type": mime_type,
        "needs_follow_up": follow_up,
    })


async def _cancel_task(task: asyncio.Task | None) -> None:
    """Cancela uma Task asyncio e aguarda seu encerramento limpo."""
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    print("[WEB WS] Task anterior cancelada")


@router.websocket("/ws/voice/{phone_number}")
async def voice_websocket(websocket: WebSocket, phone_number: str):
    await websocket.accept()
    loop = asyncio.get_event_loop()
    tts = get_tts()
    current_task: asyncio.Task | None = None

    print(f"[WEB WS] Cliente conectado: {phone_number}")

    async def run_text(user_text: str) -> None:
        try:
            await _process_text(websocket, phone_number, user_text, tts)
        except asyncio.CancelledError:
            print(f"[WEB WS] Processamento cancelado: \"{user_text[:60]}\"")
        except Exception as e:
            import traceback
            print(f"[WEB WS] ERRO ao processar texto: {e}\n{traceback.format_exc()}")
            try:
                await websocket.send_json({"type": "error", "message": "Erro interno. Tenta de novo!"})
            except Exception:
                pass

    try:
        while True:
            raw = await websocket.receive()
            frame_type = raw.get("type", "unknown")
            audio_bytes = raw.get("bytes")
            text_frame = raw.get("text")

            print(f"[WEB WS] Frame recebido | tipo={frame_type} | bytes={len(audio_bytes) if audio_bytes else 0} | text={text_frame[:60] if text_frame else None}")

            if frame_type == "websocket.disconnect":
                print(f"[WEB WS] Disconnect recebido: {phone_number}")
                await _cancel_task(current_task)
                break

            if text_frame:
                msg = json.loads(text_frame)
                msg_type = msg.get("type")
                print(f"[WEB WS] Mensagem texto | tipo={msg_type} | conteudo={str(msg)[:80]}")

                if msg_type == "cancel":
                    print(f"[WEB WS] Cancel recebido do cliente: {phone_number}")
                    await _cancel_task(current_task)
                    current_task = None
                    await websocket.send_json({"type": "status", "text": ""})
                    continue

                if msg_type == "name":
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

                if msg_type == "user_message":
                    user_text = msg.get("text", "").strip()
                    if not user_text:
                        continue
                    print(f"[WEB WS] Texto do usuario: \"{user_text[:80]}\"")
                    await _cancel_task(current_task)
                    current_task = asyncio.create_task(run_text(user_text))
                    continue

                continue

            if not audio_bytes:
                print(f"[WEB WS] Frame sem bytes e sem texto — ignorando (tipo={frame_type})")
                continue

            print(f"[WEB WS] Audio recebido: {len(audio_bytes)} bytes de {phone_number}")

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
                print(f"[WEB WS] Processando audio | provider={llm_provider} | new_session={new_session}")

                if llm_provider == "gemini":
                    from agno.media import Audio

                    await websocket.send_json({"type": "transcript", "text": "..."})
                    await websocket.send_json({"type": "status", "text": "Pensando..."})

                    base_prompt = "O usuario enviou este audio via interface web (formato webm/opus). Responda naturalmente ao que foi dito."
                    if new_session:
                        base_prompt = GREETING_INJECTION + " " + base_prompt

                    print(f"[WEB WS] Enviando {len(audio_bytes)} bytes de audio webm para Gemini")
                    response = await asyncio.to_thread(
                        agent.run,
                        base_prompt,
                        audio=[Audio(content=audio_bytes, format="webm")],
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

                print(f"[WEB WS] Resposta do agente ({len(response.content)} chars): {response.content[:80]}...")
                await websocket.send_json({"type": "status", "text": "Gerando áudio..."})

                audio_b64 = ""
                mime_type = "audio/wav"
                try:
                    audio_out, mime_type = await tts.synthesize(response.content)
                    print(f"[WEB WS] TTS gerado: {len(audio_out)} bytes | mime={mime_type}")
                    audio_b64 = base64.b64encode(audio_out).decode() if audio_out else ""
                except Exception as e:
                    print(f"[WEB WS] TTS falhou, enviando só texto: {e}")
                    mime_type = "browser"

                follow_up = _needs_follow_up(response.content)
                print(f"[WEB WS] needs_follow_up={follow_up}")

                await websocket.send_json({
                    "type": "response",
                    "text": response.content,
                    "audio_b64": audio_b64,
                    "mime_type": mime_type,
                    "needs_follow_up": follow_up,
                })
                print(f"[WEB WS] Resposta enviada ao cliente: {phone_number}")

            except Exception as e:
                import traceback
                print(f"[WEB WS] ERRO ao processar mensagem de {phone_number}: {e}")
                print(traceback.format_exc())
                await websocket.send_json({"type": "error", "message": "Erro interno. Tenta de novo!"})

    except WebSocketDisconnect:
        await _cancel_task(current_task)
        print(f"[WEB WS] Cliente desconectado: {phone_number}")

import os
import re
import json
import base64
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

def split_into_sentences(text: str) -> list[str]:
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    return [s.strip() for s in sentences if s.strip()]

from src.agent.factory import create_agent_with_tools
from src.agent.response_utils import extract_final_response
from src.integrations.tts import get_tts
from src.memory.identity import get_user, update_user_name, update_last_seen, is_new_session, is_plan_active
from src.memory.extractor import extract_and_save_facts
from src.tools.memory_manager import add_memory
from src.auth.jwt import decode_token
from src.memory.analytics import log_event, log_agent_tools
from src.models.chat_messages import save_message
from src.agent.prompts import GREETING_INJECTION_WEB as GREETING_INJECTION
import time

router = APIRouter()


_EMOJI_TAIL_RE = re.compile(
    r'[\s\U0001F000-\U0001FAFF\U0001F600-\U0001F64F\U0001F900-\U0001F9FF'
    r'\u2600-\u27BF\uFE00-\uFE0F\u200d\u2764\u2705\u274C\u2728'
    r'\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF*_)!\s]+$'
)


def _needs_follow_up(text: str) -> bool:
    cleaned = _EMOJI_TAIL_RE.sub('', text.strip())
    if cleaned.endswith('?'):
        return True
    tail = cleaned[-300:] if len(cleaned) > 300 else cleaned
    return '?' in tail


class WebSocketNotifier:
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


async def _process_text(websocket, phone_number: str, user_text: str, tts, user: dict, mode: str = "voice", images_b64: list = None):
    if images_b64 is None:
        images_b64 = []
        
    start_time = time.time()
    log_event(user_id=phone_number, channel="web", event_type="message_received", status="success")
    
    # Decodificar imagens
    image_bytes_list = []
    for b64 in images_b64:
        try:
            if b64.startswith("data:"):
                b64 = b64.split(",", 1)[1]
            image_bytes_list.append(base64.b64decode(b64))
        except Exception as e:
            print(f"[WEB WS] Erro ao decodificar imagem base64: {e}")

    agent_images = []
    save_to_chat = (mode == "text")

    if image_bytes_list:
        from agno.media import Image
        from src.tools.image_editor import store_session_images
        store_session_images(phone_number, image_bytes_list)
        for i_bytes in image_bytes_list:
            agent_images.append(Image(content=i_bytes))
            
        async def handle_images_and_save_message():
            from src.integrations.image_storage import upload_user_image, describe_and_store_images
            loop = asyncio.get_event_loop()
            upload_tasks = [loop.run_in_executor(None, upload_user_image, phone_number, img_bytes) for img_bytes in image_bytes_list]
            urls = await asyncio.gather(*upload_tasks, return_exceptions=True)
            
            valid_urls = [u for u in urls if not isinstance(u, Exception) and u]
            
            if save_to_chat:
                display_text = user_text if user_text else ""
                if valid_urls:
                    display_text += "\n" + "\n".join(valid_urls)
                display_text = display_text.strip() or "[Imagens]"
                await asyncio.to_thread(save_message, phone_number, phone_number, "user", display_text)
            
            await describe_and_store_images(phone_number, image_bytes_list, pre_uploaded_urls=urls)

        asyncio.create_task(handle_images_and_save_message())
    elif save_to_chat:
        display_text = user_text if user_text else "[Imagens]"
        asyncio.create_task(asyncio.to_thread(save_message, phone_number, phone_number, "user", display_text))
    
    new_session = is_new_session(user, threshold_hours=4)
    loop = asyncio.get_event_loop()

    await websocket.send_json({"type": "status", "text": "Pensando..."})

    # Atalho só deve ser executado se não for nova sessão E se não tiver imagens (imagens exigem agente)
    if not new_session and not agent_images and user_text.strip():
        try:
            from src.tools.reminder_shortcuts import try_schedule_quick_reminder

            shortcut_msg = try_schedule_quick_reminder(
                user_phone=phone_number,
                text=user_text,
                notification_channel="web_voice" if mode != "text" else "whatsapp_text",
            )
            if shortcut_msg:
                await websocket.send_json(
                    {
                        "type": "response",
                        "text": shortcut_msg,
                        "audio_b64": "",
                        "mime_type": "none",
                        "needs_follow_up": False,
                    }
                )
                update_last_seen(phone_number)
                await websocket.send_json({"type": "reminder_updated"})
                return
        except Exception as e:
            print(f"[WEB WS] Falha no atalho de lembrete rapido: {e}")

    notifier = WebSocketNotifier(websocket, loop)
    agent = create_agent_with_tools(phone_number, notifier, include_explore=True, user_id=phone_number, channel="web")

    prompt = user_text.strip()
    if not prompt and agent_images:
        prompt = "O usuário enviou imagens."
        
    memory_mode = os.getenv("MEMORY_MODE", "agentic").lower()
    if memory_mode == "always-on" and prompt:
        from src.memory.knowledge import get_vector_db
        vector_db = get_vector_db()
        if vector_db:
            try:
                results = vector_db.search(query=prompt, limit=3, filters={"user_id": phone_number})
                if results:
                    memories = "\n".join([f"- {doc.content}" for doc in results])
                    prompt += f"\n\n[Contexto da Memoria para considerar:\n{memories}]"
            except Exception as e:
                print(f"[WEB WS] Falha ao buscar memorias: {e}")

    if new_session:
        prompt = GREETING_INJECTION + "\n\n" + prompt

    kwargs = {"knowledge_filters": {"user_id": phone_number}}
    if agent_images:
        kwargs["images"] = agent_images

    response = await asyncio.to_thread(
        agent.run, prompt, **kwargs
    )
    log_agent_tools(phone_number, "web", agent)
    final_text = extract_final_response(response)

    if not final_text:
        rc = getattr(response, 'reasoning_content', None)
        print(f"[WEB WS] WARN resposta vazia — content={repr(getattr(response, 'content', None))[:200]} reasoning={repr(rc)[:200]}")
        if hasattr(response, "messages") and response.messages:
            for i, m in enumerate(response.messages[-5:]):
                print(f"  msg[{i}] role={getattr(m, 'role', '?')} content={repr(getattr(m, 'content', None))[:120]} tool_calls={bool(getattr(m, 'tool_calls', None))} reasoning={repr(getattr(m, 'reasoning_content', None))[:80]}")

        print("[WEB WS] Retrying agent.run()...")
        response = await asyncio.to_thread(agent.run, prompt, **kwargs)
        final_text = extract_final_response(response)

        if not final_text:
            final_text = "Desculpa, tive um problema ao processar sua mensagem. Pode repetir?"
            print("[WEB WS] Retry tambem vazio, usando mensagem padrao")

    await asyncio.sleep(0)

    asyncio.create_task(asyncio.to_thread(
        extract_and_save_facts, phone_number, user_text, final_text
    ))

    update_last_seen(phone_number)
    print(f"[WEB WS] Resposta ({len(final_text)} chars): {final_text[:80]}...")

    audio_b64 = ""
    mime_type = "none"

    if mode != "text":
        await websocket.send_json({"type": "status", "text": "Gerando áudio..."})
        try:
            audio_out, mime_type = await tts.synthesize(final_text)
            print(f"[WEB WS] TTS: {len(audio_out)} bytes | {mime_type}")
            audio_b64 = base64.b64encode(audio_out).decode() if audio_out else ""
        except Exception as e:
            print(f"[WEB WS] TTS falhou, enviando só texto: {e}")
            mime_type = "browser"

    await asyncio.sleep(0)

    follow_up = _needs_follow_up(final_text)
    print(f"[WEB WS] needs_follow_up={follow_up}")

    await websocket.send_json({
        "type": "response",
        "text": final_text,
        "audio_b64": audio_b64,
        "mime_type": mime_type,
        "needs_follow_up": follow_up,
    })
    if save_to_chat:
        asyncio.create_task(asyncio.to_thread(save_message, phone_number, phone_number, "agent", final_text))
    latency = int((time.time() - start_time) * 1000)
    log_event(user_id=phone_number, channel="web", event_type="message_sent", status="success", latency_ms=latency)
    await websocket.send_json({"type": "reminder_updated"})


async def _cancel_task(task: asyncio.Task | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    print("[WEB WS] Task anterior cancelada")


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    def connect(self, websocket: WebSocket, phone_number: str):
        self.active_connections[phone_number] = websocket

    def disconnect(self, phone_number: str):
        if phone_number in self.active_connections:
            del self.active_connections[phone_number]

    async def send_personal_message(self, phone_number: str, message: dict) -> bool:
        if phone_number in self.active_connections:
            ws = self.active_connections[phone_number]
            try:
                await ws.send_json(message)
                return True
            except Exception as e:
                print(f"[WS MANAGER] Erro ao enviar mensagem para {phone_number}: {e}")
                self.disconnect(phone_number)
        return False

    def is_online(self, phone_number: str) -> bool:
        return phone_number in self.active_connections

ws_manager = ConnectionManager()


@router.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket, token: str = Query(...)):
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
        
    loop = asyncio.get_event_loop()
    tts = get_tts()
    current_task: asyncio.Task | None = None

    ws_manager.connect(websocket, phone_number)
    print(f"[WEB WS] Cliente conectado: {phone_number}")

    async def run_text(user_text: str, mode: str = "voice", images_b64: list = None) -> None:
        try:
            await _process_text(websocket, phone_number, user_text, tts, user, mode, images_b64)
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
                mode = msg.get("mode", "voice")
                print(f"[WEB WS] Mensagem texto | tipo={msg_type} | conteudo={str(msg)[:80]}")

                if msg_type == "cancel":
                    print(f"[WEB WS] Cancel recebido do cliente: {phone_number}")
                    await _cancel_task(current_task)
                    current_task = None
                    await websocket.send_json({"type": "status", "text": ""})
                    continue

                if msg_type == "name":
                    # Backward compatibility, mas agora o nome vem do registro
                    continue

                if msg_type == "user_message":
                    user_text = msg.get("text", "").strip()
                    images_b64 = msg.get("images", [])
                    if not user_text and not images_b64:
                        continue
                    print(f"[WEB WS] Texto do usuario: \"{user_text[:80]}\" | {len(images_b64)} imagens")
                    await _cancel_task(current_task)
                    current_task = asyncio.create_task(run_text(user_text, mode, images_b64))
                    continue

                continue

            if not audio_bytes:
                print(f"[WEB WS] Frame sem bytes e sem texto — ignorando (tipo={frame_type})")
                continue

            print(f"[WEB WS] Audio recebido: {len(audio_bytes)} bytes de {phone_number}")

            try:
                start_time = time.time()
                log_event(user_id=phone_number, channel="web", event_type="message_received", status="success")
                # Refresh user p/ pegar is_new_session fresquinho
                current_user = get_user(phone_number)
                new_session = is_new_session(current_user, threshold_hours=4)

                await websocket.send_json({"type": "status", "text": "Ouvindo..."})

                notifier = WebSocketNotifier(websocket, loop)
                agent = create_agent_with_tools(phone_number, notifier, user_id=phone_number, channel="web")

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
                    log_agent_tools(phone_number, "web", agent)
                    response_content = extract_final_response(response)
                    asyncio.create_task(asyncio.to_thread(
                        extract_and_save_facts, phone_number, "Áudio do usuário", response_content
                    ))
                else:
                    from src.integrations.transcriber import transcriber

                    transcript = await transcriber.transcribe(audio_bytes, filename="audio.webm")
                    await websocket.send_json({"type": "transcript", "text": transcript})
                    await websocket.send_json({"type": "status", "text": "Pensando..."})

                    if not new_session:
                        try:
                            from src.tools.reminder_shortcuts import try_schedule_quick_reminder
                            shortcut_msg = try_schedule_quick_reminder(
                                user_phone=phone_number,
                                text=transcript,
                                notification_channel="web_voice",
                            )
                            if shortcut_msg:
                                await websocket.send_json({
                                    "type": "response",
                                    "text": shortcut_msg,
                                    "audio_b64": "",
                                    "mime_type": "none",
                                    "needs_follow_up": False,
                                })
                                update_last_seen(phone_number)
                                latency = int((time.time() - start_time) * 1000)
                                log_event(user_id=phone_number, channel="web", event_type="message_sent", status="success", latency_ms=latency)
                                await websocket.send_json({"type": "reminder_updated"})
                                continue
                        except Exception as e:
                            print(f"[WEB WS] Falha no atalho de lembrete rapido (audio): {e}")

                    prompt = f"O usuario enviou um audio com a seguinte transcricao:\n\n{transcript}"
                    if new_session:
                        prompt = GREETING_INJECTION + "\n\n" + prompt

                    response = await asyncio.to_thread(
                        agent.run,
                        prompt,
                        knowledge_filters={"user_id": phone_number},
                    )
                    log_agent_tools(phone_number, "web", agent)
                    response_content = extract_final_response(response)
                    asyncio.create_task(asyncio.to_thread(
                        extract_and_save_facts, phone_number, transcript, response_content
                    ))

                update_last_seen(phone_number)

                print(f"[WEB WS] Resposta do agente ({len(response_content)} chars): {response_content[:80]}...")
                await websocket.send_json({"type": "status", "text": "Gerando áudio..."})

                audio_b64 = ""
                mime_type = "audio/wav"
                try:
                    audio_out, mime_type = await tts.synthesize(response_content)
                    print(f"[WEB WS] TTS gerado: {len(audio_out)} bytes | mime={mime_type}")
                    audio_b64 = base64.b64encode(audio_out).decode() if audio_out else ""
                except Exception as e:
                    print(f"[WEB WS] TTS falhou, enviando só texto: {e}")
                    mime_type = "browser"

                follow_up = _needs_follow_up(response_content)
                print(f"[WEB WS] needs_follow_up={follow_up}")

                await websocket.send_json({
                    "type": "response",
                    "text": response_content,
                    "audio_b64": audio_b64,
                    "mime_type": mime_type,
                    "needs_follow_up": follow_up,
                })
                print(f"[WEB WS] Resposta enviada ao cliente: {phone_number}")
                latency = int((time.time() - start_time) * 1000)
                log_event(user_id=phone_number, channel="web", event_type="message_sent", status="success", latency_ms=latency)
                await websocket.send_json({"type": "reminder_updated"})

            except Exception as e:
                import traceback
                print(f"[WEB WS] ERRO ao processar mensagem de {phone_number}: {e}")
                print(traceback.format_exc())
                await websocket.send_json({"type": "error", "message": "Erro interno. Tenta de novo!"})

    except WebSocketDisconnect:
        await _cancel_task(current_task)
        ws_manager.disconnect(phone_number)
        print(f"[WEB WS] Cliente desconectado: {phone_number}")

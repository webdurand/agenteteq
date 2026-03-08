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
from src.config.feature_gates import is_feature_enabled
from src.memory.identity import get_user, update_user_name, update_last_seen, is_new_session, is_plan_active
from src.memory.extractor import extract_and_save_facts
from src.tools.memory_manager import add_memory
from src.auth.jwt import decode_token
from src.memory.analytics import log_event, log_agent_tools
from src.models.chat_messages import save_message
from src.agent.prompts import GREETING_INJECTION_WEB as GREETING_INJECTION
from src.queue.task_queue import get_usage_context, get_usage_status
import time
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

def _detect_audio_ext(audio_bytes: bytes, hint: str = "") -> str:
    """Detect audio format from magic bytes, with optional hint from frontend."""
    if hint:
        return hint
    if audio_bytes[:4] == b'\x1aE\xdf\xa3':
        return "webm"
    if len(audio_bytes) > 7 and audio_bytes[4:8] == b'ftyp':
        return "mp4"
    if audio_bytes[:4] == b'RIFF':
        return "wav"
    if audio_bytes[:4] == b'OggS':
        return "ogg"
    return "webm"

_EMOJI_TAIL_RE = re.compile(
    r'[\s\U0001F000-\U0001FAFF\U0001F600-\U0001F64F\U0001F900-\U0001F9FF'
    r'\u2600-\u27BF\uFE00-\uFE0F\u200d\u2764\u2705\u274C\u2728'
    r'\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF*_)!\s]+$'
)

_IMAGE_INTENT_RE = re.compile(
    r"\b(imagem|imagens|foto|fotos|carrossel|gera|gerar|cria|criar|edita|editar|ilustracao|arte)\b",
    re.IGNORECASE,
)

def _needs_follow_up(text: str) -> bool:
    cleaned = _EMOJI_TAIL_RE.sub('', text.strip())
    if cleaned.endswith('?'):
        return True
    tail = cleaned[-300:] if len(cleaned) > 300 else cleaned
    return '?' in tail

def _looks_like_image_request(user_text: str, has_images: bool) -> bool:
    if has_images:
        return True
    if not user_text:
        return False
    return bool(_IMAGE_INTENT_RE.search(user_text))

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
            logger.error("[WS NOTIFIER] Falha ao enviar status '%s': %s", message[:40], e)

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
            logger.error("[WEB WS] Erro ao decodificar imagem base64: %s", e)

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
                preferred_web_channel="web_voice" if mode != "text" else "web_text",
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
            logger.error("[WEB WS] Falha no atalho de lembrete rapido: %s", e)

    notifier = WebSocketNotifier(websocket, loop)
    agent_channel = "web_voice" if mode != "text" else "web_text"
    agent = create_agent_with_tools(
        phone_number,
        notifier,
        include_explore=True,
        user_id=phone_number,
        channel=agent_channel,
    )

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
                logger.error("[WEB WS] Falha ao buscar memorias: %s", e)

    if new_session:
        prompt = GREETING_INJECTION + "\n\n" + prompt

    usage_status = await asyncio.to_thread(get_usage_status, phone_number)
    usage_ctx = usage_status.get("context") or await asyncio.to_thread(get_usage_context, phone_number)
    prompt = f"{usage_ctx}\n\n{prompt}"

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
        logger.warning("[WEB WS] WARN resposta vazia — content=%s reasoning=%s", repr(getattr(response, 'content', None))[:200], repr(rc)[:200])
        if hasattr(response, "messages") and response.messages:
            for i, m in enumerate(response.messages[-5:]):
                logger.info("  msg[%s] role=%s content=%s tool_calls=%s reasoning=%s", i, getattr(m, 'role', '?'), repr(getattr(m, 'content', None))[:120], bool(getattr(m, 'tool_calls', None)), repr(getattr(m, 'reasoning_content', None))[:80])

        logger.info("[WEB WS] Retrying agent.run()...")
        response = await asyncio.to_thread(agent.run, prompt, **kwargs)
        final_text = extract_final_response(response)

        if not final_text:
            final_text = "Desculpa, tive um problema ao processar sua mensagem. Pode repetir?"
            logger.info("[WEB WS] Retry tambem vazio, usando mensagem padrao")

    await asyncio.sleep(0)

    from src.queue.task_queue import pop_limit_flag
    limit_info = pop_limit_flag(phone_number)
    if (
        not limit_info
        and save_to_chat
        and usage_status.get("effective_plan") == "free"
        and usage_status.get("is_limited")
        and _looks_like_image_request(user_text, bool(agent_images))
    ):
        # Fallback determinístico: se o LLM respondeu por contexto de limite sem
        # chamar tool, ainda assim disparamos o evento limit_reached para renderizar card Premium.
        limit_info = {
            "message": usage_status.get("limit_message") or "Seu limite diário de gerações foi atingido.",
            "plan_type": "free",
        }

    if limit_info:
        logger.info("[WEB WS] Limite atingido para %s, enviando limit_reached determinístico", phone_number)
        await websocket.send_json({
            "type": "limit_reached",
            "message": limit_info["message"],
            "plan_type": limit_info["plan_type"],
        })
        update_last_seen(phone_number)
        if save_to_chat:
            import json as _json
            limit_text = "__LIMIT_REACHED__" + _json.dumps({
                "message": limit_info["message"],
                "plan_type": limit_info["plan_type"],
            })
            asyncio.create_task(asyncio.to_thread(save_message, phone_number, phone_number, "agent", limit_text))
        latency = int((time.time() - start_time) * 1000)
        log_event(user_id=phone_number, channel="web", event_type="message_sent", status="success", latency_ms=latency)
        return

    asyncio.create_task(asyncio.to_thread(
        extract_and_save_facts, phone_number, user_text, final_text
    ))

    update_last_seen(phone_number)
    logger.info("[WEB WS] Resposta (%s chars): %s...", len(final_text), final_text[:80])

    audio_b64 = ""
    mime_type = "none"

    tts_enabled = is_feature_enabled(phone_number, "tts_enabled")
    if mode != "text" and tts_enabled:
        await websocket.send_json({"type": "status", "text": "Gerando áudio..."})
        try:
            audio_out, mime_type = await tts.synthesize(final_text)
            logger.info("[WEB WS] TTS: %s bytes | %s", len(audio_out), mime_type)
            audio_b64 = base64.b64encode(audio_out).decode() if audio_out else ""
        except Exception as e:
            logger.info("[WEB WS] TTS falhou, enviando só texto: %s", e)
            mime_type = "browser"
    elif mode != "text" and not tts_enabled:
        mime_type = "browser"

    await asyncio.sleep(0)

    follow_up = _needs_follow_up(final_text)
    logger.info("[WEB WS] needs_follow_up=%s", follow_up)

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
    logger.info("[WEB WS] Task anterior cancelada")

class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, dict[str, WebSocket]] = {}

    def connect(self, websocket: WebSocket, phone_number: str, channel: str = "web") -> str:
        user_connections = self.active_connections.setdefault(phone_number, {})
        conn_key = f"{channel}:{id(websocket)}"
        user_connections[conn_key] = websocket
        return conn_key

    def disconnect(self, phone_number: str, websocket: WebSocket | None = None):
        user_connections = self.active_connections.get(phone_number)
        if not user_connections:
            return

        if websocket is None:
            del self.active_connections[phone_number]
            return

        keys_to_remove = [k for k, ws in user_connections.items() if ws is websocket]
        for key in keys_to_remove:
            user_connections.pop(key, None)

        if not user_connections:
            self.active_connections.pop(phone_number, None)

    async def send_personal_message(self, phone_number: str, message: dict) -> bool:
        user_connections = self.active_connections.get(phone_number, {})
        if not user_connections:
            return False

        delivered = False
        dead_keys: list[str] = []
        for key, ws in list(user_connections.items()):
            try:
                await ws.send_json(message)
                delivered = True
            except Exception as e:
                logger.error("[WS MANAGER] Erro ao enviar mensagem para %s (%s): %s", phone_number, key, e)
                dead_keys.append(key)

        for key in dead_keys:
            user_connections.pop(key, None)
        if not user_connections:
            self.active_connections.pop(phone_number, None)

        return delivered

    def is_online(self, phone_number: str) -> bool:
        return bool(self.active_connections.get(phone_number))

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
    audio_ext_hint: str = ""

    ws_manager.connect(websocket, phone_number, channel="web")
    logger.info("[WEB WS] Cliente conectado: %s", phone_number)

    async def run_text(user_text: str, mode: str = "voice", images_b64: list = None) -> None:
        try:
            await _process_text(websocket, phone_number, user_text, tts, user, mode, images_b64)
        except asyncio.CancelledError:
            logger.info("[WEB WS] Processamento cancelado: \"%s\"", user_text[:60])
        except Exception as e:
            import traceback
            logger.error("[WEB WS] ERRO ao processar texto: %s\n%s", e, traceback.format_exc())
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

            logger.info("[WEB WS] Frame recebido | tipo=%s | bytes=%s | text=%s", frame_type, len(audio_bytes) if audio_bytes else 0, text_frame[:60] if text_frame else None)

            if frame_type == "websocket.disconnect":
                logger.info("[WEB WS] Disconnect recebido: %s", phone_number)
                await _cancel_task(current_task)
                break

            if text_frame:
                msg = json.loads(text_frame)
                msg_type = msg.get("type")
                mode = msg.get("mode", "voice")
                logger.info("[WEB WS] Mensagem texto | tipo=%s | conteudo=%s", msg_type, str(msg)[:80])

                if msg_type == "cancel":
                    logger.info("[WEB WS] Cancel recebido do cliente: %s", phone_number)
                    await _cancel_task(current_task)
                    current_task = None
                    await websocket.send_json({"type": "status", "text": ""})
                    continue

                if msg_type == "name":
                    # Backward compatibility, mas agora o nome vem do registro
                    continue

                if msg_type == "audio_meta":
                    audio_ext_hint = msg.get("ext", "")
                    continue

                if msg_type == "user_message":
                    user_text = msg.get("text", "").strip()
                    images_b64 = msg.get("images", [])
                    if not user_text and not images_b64:
                        continue
                    logger.info("[WEB WS] Texto do usuario: \"%s\" | %s imagens", user_text[:80], len(images_b64))
                    await _cancel_task(current_task)
                    current_task = asyncio.create_task(run_text(user_text, mode, images_b64))
                    continue

                continue

            if not audio_bytes:
                logger.info("[WEB WS] Frame sem bytes e sem texto — ignorando (tipo=%s)", frame_type)
                continue

            logger.info("[WEB WS] Audio recebido: %s bytes de %s", len(audio_bytes), phone_number)

            try:
                start_time = time.time()
                log_event(user_id=phone_number, channel="web", event_type="message_received", status="success")
                # Refresh user p/ pegar is_new_session fresquinho
                current_user = get_user(phone_number)
                new_session = is_new_session(current_user, threshold_hours=4)

                await websocket.send_json({"type": "status", "text": "Ouvindo..."})

                notifier = WebSocketNotifier(websocket, loop)
                agent = create_agent_with_tools(
                    phone_number,
                    notifier,
                    user_id=phone_number,
                    channel="web_voice",
                )

                audio_fmt = _detect_audio_ext(audio_bytes, audio_ext_hint)
                audio_ext_hint = ""
                llm_provider = os.getenv("LLM_PROVIDER", "openai").lower()
                logger.info("[WEB WS] Processando audio | provider=%s | formato=%s | new_session=%s", llm_provider, audio_fmt, new_session)

                if llm_provider == "gemini":
                    from agno.media import Audio

                    await websocket.send_json({"type": "transcript", "text": "..."})
                    await websocket.send_json({"type": "status", "text": "Pensando..."})

                    base_prompt = f"O usuario enviou este audio via interface web (formato {audio_fmt}). Responda naturalmente ao que foi dito."
                    if new_session:
                        base_prompt = GREETING_INJECTION + " " + base_prompt
                    usage_ctx = await asyncio.to_thread(get_usage_context, phone_number)
                    base_prompt = f"{usage_ctx}\n\n{base_prompt}"

                    logger.info("[WEB WS] Enviando %s bytes de audio %s para Gemini", len(audio_bytes), audio_fmt)
                    response = await asyncio.to_thread(
                        agent.run,
                        base_prompt,
                        audio=[Audio(content=audio_bytes, format=audio_fmt)],
                        knowledge_filters={"user_id": phone_number},
                    )
                    log_agent_tools(phone_number, "web", agent)
                    response_content = extract_final_response(response)
                    asyncio.create_task(asyncio.to_thread(
                        extract_and_save_facts, phone_number, "Áudio do usuário", response_content
                    ))
                else:
                    from src.integrations.transcriber import transcriber

                    transcript = await transcriber.transcribe(audio_bytes, filename=f"audio.{audio_fmt}")
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
                            logger.error("[WEB WS] Falha no atalho de lembrete rapido (audio): %s", e)

                    prompt = f"O usuario enviou um audio com a seguinte transcricao:\n\n{transcript}"
                    if new_session:
                        prompt = GREETING_INJECTION + "\n\n" + prompt
                    usage_ctx = await asyncio.to_thread(get_usage_context, phone_number)
                    prompt = f"{usage_ctx}\n\n{prompt}"

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

                logger.info("[WEB WS] Resposta do agente (%s chars): %s...", len(response_content), response_content[:80])

                audio_b64 = ""
                mime_type = "audio/wav"
                tts_ok = is_feature_enabled(phone_number, "tts_enabled")
                if tts_ok:
                    await websocket.send_json({"type": "status", "text": "Gerando áudio..."})
                    try:
                        audio_out, mime_type = await tts.synthesize(response_content)
                        logger.info("[WEB WS] TTS gerado: %s bytes | mime=%s", len(audio_out), mime_type)
                        audio_b64 = base64.b64encode(audio_out).decode() if audio_out else ""
                    except Exception as e:
                        logger.info("[WEB WS] TTS falhou, enviando só texto: %s", e)
                        mime_type = "browser"
                else:
                    mime_type = "browser"

                follow_up = _needs_follow_up(response_content)
                logger.info("[WEB WS] needs_follow_up=%s", follow_up)

                await websocket.send_json({
                    "type": "response",
                    "text": response_content,
                    "audio_b64": audio_b64,
                    "mime_type": mime_type,
                    "needs_follow_up": follow_up,
                })
                logger.info("[WEB WS] Resposta enviada ao cliente: %s", phone_number)
                latency = int((time.time() - start_time) * 1000)
                log_event(user_id=phone_number, channel="web", event_type="message_sent", status="success", latency_ms=latency)
                await websocket.send_json({"type": "reminder_updated"})

            except Exception as e:
                import traceback

                logger.error("[WEB WS] ERRO ao processar mensagem de %s: %s", phone_number, e)
                print(traceback.format_exc())
                await websocket.send_json({"type": "error", "message": "Erro interno. Tenta de novo!"})

    except WebSocketDisconnect:
        await _cancel_task(current_task)
        ws_manager.disconnect(phone_number, websocket=websocket)
        logger.info("[WEB WS] Cliente desconectado: %s", phone_number)

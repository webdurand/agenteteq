import logging
import os
import time
import hashlib
import hmac
import json
import asyncio
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request, HTTPException, Query

logger = logging.getLogger(__name__)
from sqlalchemy.exc import IntegrityError

from src.integrations.whatsapp import whatsapp_client
from src.integrations.transcriber import transcriber
from src.integrations.status_notifier import StatusNotifier
from src.agent.factory import create_agent_with_tools
from src.agent.response_utils import extract_final_response, split_whatsapp_messages
from src.agent.prompts import GREETING_INJECTION
from src.memory.identity import get_user, create_user, update_user_name, update_last_seen, is_new_session, is_plan_active
from src.memory.knowledge import get_vector_db
from src.memory.extractor import extract_and_save_facts
from src.tools.memory_manager import add_memory
from src.memory.analytics import log_event, log_agent_tools, log_run_metrics
from src.db.session import get_db
from src.db.models import ProcessedMessage, MessageBuffer

router = APIRouter()

VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")

_TEST_NUMBERS = set(os.getenv("META_TEST_NUMBERS", "16315551181,16505551111").split(","))


def _is_test_number(phone: str) -> bool:
    return phone in _TEST_NUMBERS


def deduplicate(dedup_key: str) -> bool:
    with get_db() as db:
        existing = db.get(ProcessedMessage, dedup_key)
        if existing:
            return True

        # Garante idempotencia mesmo sob concorrencia (duas requests em paralelo).
        try:
            db.add(ProcessedMessage(message_id=dedup_key))
            db.flush()
            return False
        except IntegrityError:
            return True


BUFFER_TIMEOUT = 3.0


def flush_ready_buffers():
    now = datetime.now(timezone.utc).isoformat()
    ready_rows = []

    with get_db() as db:
        buffers = (
            db.query(MessageBuffer)
            .filter(MessageBuffer.flush_at <= now)
            .with_for_update(skip_locked=True)
            .all()
        )
        for buf in buffers:
            ready_rows.append((buf.user_id, buf.events))
            db.delete(buf)

    for user_id, events_json in ready_rows:
        try:
            events = json.loads(events_json) if isinstance(events_json, str) else events_json
            if events:
                aggregated = aggregate_events(events)
                from src.events import _main_loop
                if _main_loop and _main_loop.is_running():
                    asyncio.run_coroutine_threadsafe(orchestrate_message(aggregated), _main_loop)
                else:
                    asyncio.create_task(orchestrate_message(aggregated))
        except Exception as e:
            logger.error("Erro ao processar buffer do usuário %s: %s", user_id, e)


async def buffer_message(from_number: str, event: dict):
    flush_at = (datetime.now(timezone.utc) + timedelta(seconds=3)).isoformat()

    with get_db() as db:
        buf = db.get(MessageBuffer, from_number)
        if buf:
            existing = json.loads(buf.events) if isinstance(buf.events, str) else buf.events
            existing.append(event)
            buf.events = json.dumps(existing)
            buf.flush_at = flush_at
        else:
            db.add(MessageBuffer(
                user_id=from_number,
                events=json.dumps([event]),
                flush_at=flush_at,
            ))


async def _verify_meta_signature(request: Request) -> bytes:
    body = await request.body()
    if not WHATSAPP_APP_SECRET:
        return body
    sig = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(WHATSAPP_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(403, "Invalid webhook signature")
    return body


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

    base["id"] = message_ids[0]
    base["all_ids"] = message_ids
    base["aggregated_text"] = "\n\n".join(texts)
    base["raw_message"] = {
        "images": images,
        "all_audios": audios,
        "text": {"body": base["aggregated_text"]}
    }
    return base


@router.get("/webhook/whatsapp")
def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logger.info("Webhook verificado com sucesso!")
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Token de verificação inválido")


@router.post("/webhook/whatsapp")
async def receive_webhook(request: Request):
    try:
        body = await _verify_meta_signature(request)
        data = json.loads(body)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Falha ao ler JSON do request: %s", e)
        return {"status": "error"}

    try:
        events = parse_webhook_payload(data)
        for event in events:
            if not event.get("should_process", False):
                reason = event.get("skip_reason", "Desconhecido")
                if reason not in ["Status message", "Enviado por mim", "Protocol message"] and not reason.startswith("Evento não processável"):
                    logger.info("Evento ignorado. Motivo: %s | ID: %s", reason, event.get('id', 'N/A'))
                continue

            dedup_key = event["dedup_key"]
            if deduplicate(dedup_key):
                logger.info("Mensagem duplicada ignorada no Dedup. Chave: %s", dedup_key)
                continue

            from_number = event["from_number"]
            logger.info("Nova mensagem enfileirada no buffer. De: %s | Tipo: %s | ID: %s", from_number, event['type'], event['id'])

            try:
                asyncio.create_task(whatsapp_client.mark_message_as_read_and_typing(event["id"], from_number, is_audio=(event["type"] == "audio")))
            except Exception:
                pass

            await buffer_message(from_number, event)

    except Exception as e:
        logger.error("Falha ao processar webhook payload: %s", e)

    return {"status": "success"}


def parse_webhook_payload(data: dict) -> list:
    events = []

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

                        if msg_id:
                            # Usar somente o id da mensagem evita duplicidade por variacoes de payload.
                            dedup_key = f"meta_{msg_id}"
                        else:
                            fallback = content_str or json.dumps(msg, sort_keys=True, ensure_ascii=False)
                            dedup_key = f"meta_fallback_{hashlib.md5(fallback.encode()).hexdigest()}"

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

                if msg_id:
                    # msg_id e estavel; evita chaves diferentes em retries com payload incompleto.
                    dedup_key = f"evo_{msg_id}"
                else:
                    fallback = json.dumps(msg_content, sort_keys=True, ensure_ascii=False)
                    dedup_key = f"evo_fallback_{hashlib.md5(fallback.encode()).hexdigest()}"

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
                elif msg_type == "imageMessage":
                    normalized_type = "image"
                    base64_data = evo_data.get("base64", "") or msg_content.get("base64", "")
                    if not base64_data:
                        base64_data = json.dumps({"message": {"key": key, "message": msg_content}})
                    caption = msg_content.get("imageMessage", {}).get("caption", "")
                    normalized_msg = {"image": {"id": base64_data, "caption": caption}}

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

    orchestration_key = f"orch_{message_id}"
    if deduplicate(orchestration_key):
        logger.info("Orquestracao duplicada ignorada para %s de %s", message_id, from_number)
        return

    try:
        user = get_user(from_number)
        logger.info("Iniciando orquestracao do evento agrupado %s de %s", message_id, from_number)

        if not user or not user.get("whatsapp_verified"):
            logger.info("Usuario nao verificado ou inexistente: %s. Solicitando cadastro.", from_number)
            frontend_url = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
            await whatsapp_client.send_text_message(
                from_number,
                f"Para falar comigo, é necessário fazer cadastro em {frontend_url}",
                reply_to_message_id=message_id
            )
            return

        if not is_plan_active(user):
            logger.info("Plano expirado para %s. Bloqueando.", from_number)
            frontend_url = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
            await whatsapp_client.send_text_message(
                from_number,
                f"Seu plano expirou! Renove em {frontend_url} para continuar usando o Teq.",
                reply_to_message_id=message_id
            )
            return

        if is_new_session(user, threshold_hours=4):
            logger.info("Nova sessao detectada para %s. Aplicando greeting injection.", from_number)
            notifier = StatusNotifier(to_number=from_number, reply_to_message_id=message_id)
            agent = create_agent_with_tools(from_number, notifier, user_id=from_number)
            await process_aggregated_message(from_number, message_id, event, agent, injection=GREETING_INJECTION)
            update_last_seen(from_number)
            return

        notifier = StatusNotifier(to_number=from_number, reply_to_message_id=message_id)
        agent = create_agent_with_tools(from_number, notifier, include_explore=True, user_id=from_number)

        await process_aggregated_message(from_number, message_id, event, agent)
        update_last_seen(from_number)

    except Exception as e:
        logger.error("Falha na orquestracao para %s: %s", from_number, e)
        import traceback
        print(traceback.format_exc())
        try:
            if not _is_test_number(from_number):
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
            if not _is_test_number(from_number):
                await whatsapp_client.send_text_message(from_number, "Só consigo processar até 10 imagens por vez! 😅 Vou analisar apenas as 10 primeiras, tá?", reply_to_message_id=message_id)
        except Exception:
            pass
        images = images[:10]

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

    logger.info("Texto agregado (%s imgs, %s audios): %s...", len(agent_images), len(agent_audios), text_body[:50])

    from src.queue.task_queue import get_usage_context
    usage_ctx = get_usage_context(from_number)
    if usage_ctx:
        text_body = f"{usage_ctx}\n\n{text_body}"

    if not injection and text_body:
        try:
            from src.tools.reminder_shortcuts import try_schedule_quick_reminder
            shortcut_msg = try_schedule_quick_reminder(
                user_phone=from_number,
                text=text_body,
            )
            if shortcut_msg:
                if not _is_test_number(from_number):
                    await whatsapp_client.send_text_message(from_number, shortcut_msg, reply_to_message_id=message_id)
                    latency = int((time.time() - start_time) * 1000)
                    log_event(user_id=from_number, channel="whatsapp", event_type="message_sent", status="success", latency_ms=latency)
                return
        except Exception as e:
            logger.error("Falha no atalho de lembrete rapido: %s", e)

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
                logger.error("Falha ao buscar memorias always-on: %s", e)

    if injection:
        text_body = injection + "\n\n" + text_body

    if not text_body.strip():
        text_body = "O usuário enviou uma ou mais mídias (áudio/imagem). Responda de acordo."

    kwargs = {"knowledge_filters": {"user_id": from_number}}
    if agent_images:
        kwargs["images"] = agent_images
    if agent_audios:
        kwargs["audio"] = agent_audios

    response = await asyncio.to_thread(agent.run, text_body, **kwargs)
    log_agent_tools(from_number, "whatsapp", response)
    asyncio.create_task(asyncio.to_thread(log_run_metrics, from_number, "whatsapp", response))
    final_text = extract_final_response(response)
    asyncio.create_task(asyncio.to_thread(extract_and_save_facts, from_number, text_body, final_text))

    if not _is_test_number(from_number):
        logger.info("Enviando resposta para %s", from_number)
        parts = split_whatsapp_messages(final_text)
        for i, part in enumerate(parts):
            reply_id = message_id if i == 0 else None
            await whatsapp_client.send_text_message(from_number, part, reply_to_message_id=reply_id)
        latency = int((time.time() - start_time) * 1000)
        log_event(user_id=from_number, channel="whatsapp", event_type="message_sent", status="success", latency_ms=latency)

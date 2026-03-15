"""
Unified image generation tool — replaces carousel_generator + image_editor.

Handles single images, carousels (multi-slide), and image editing via a single
`generate_image` tool with a `reference_source` parameter.
"""

import asyncio
import io
import os
from typing import List, Dict, Any, Optional
from datetime import datetime

import cloudinary.uploader

from src.tools.image_generation import get_image_provider
from src.tools.image_session import (
    get_session_images,
    store_generated_image,
    _try_recover_last_image,
)
from src.models.carousel import create_carousel, update_carousel_status
from src.events import emit_event_sync
from src.integrations.image_storage import _ensure_cloudinary_config, convert_to_webp
import logging

logger = logging.getLogger(__name__)

_ensure_cloudinary_config()


# ---------------------------------------------------------------------------
# Slide expansion (LLM) — moved from carousel_generator
# ---------------------------------------------------------------------------

def expand_slides_from_description(
    description: str,
    num_slides: int = 5,
    style: str = "Fotorrealista",
    sequential: bool = True,
) -> List[Dict[str, str]]:
    """
    Expande uma descrição simples (vinda do Voice Live) em N prompts detalhados
    usando Gemini Flash. Retorna lista no formato esperado por generate_image_tool.
    Quando sequential=True, gera também um style_anchor compartilhado para coerência visual.
    """
    import json as _json
    from google import genai

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return [
            {"slide_number": i + 1, "prompt": description, "style": style}
            for i in range(num_slides)
        ]

    client = genai.Client(api_key=api_key)

    if sequential:
        system_prompt = (
            "Voce e um diretor criativo de carrosseis PREMIUM para Instagram. "
            "Seu trabalho e transformar um tema em um CARROSSEL profissional que conta uma HISTORIA com arco narrativo claro. "
            "Os textos serao sobrepostos por tipografia profissional — os prompts de imagem devem gerar FUNDOS LIMPOS sem texto."
            "\n\nESTRUTURA OBRIGATORIA DO CARROSSEL:"
            "\n- SLIDE 1 (role='capa'): CAPA IMPACTANTE. Titulo bold e chamativo que gera curiosidade. "
            "Imagem de fundo visualmente forte. O objetivo e fazer a pessoa parar de rolar e abrir o carrossel. "
            "Exemplos de gancho: 'X coisas que...', 'O segredo de...', 'Pare de fazer isso...', 'Voce nao vai acreditar...'"
            "\n- SLIDES 2 a N-1 (role='conteudo'): DESENVOLVIMENTO. Cada slide entrega UM ponto de valor. "
            "Cada slide deve ter um titulo claro e uma mensagem objetiva no body. "
            "Progridem logicamente: o slide 2 complementa o 1, o 3 complementa o 2, etc."
            "\n- SLIDE N (role='fechamento'): FECHAMENTO FORTE. CTA (call to action) que gera engajamento. "
            "Exemplos de CTA: 'Salva pra consultar depois', 'Comenta qual foi sua favorita', 'Manda pra alguem que precisa ver isso'."
            "\n\nCAMPOS OBRIGATORIOS POR SLIDE:"
            "\n- 'prompt': descricao da IMAGEM DE FUNDO apenas. NAO inclua texto/tipografia/letras na imagem. "
            "Inclua SEMPRE no prompt: 'sem texto, sem tipografia, sem letras, imagem de fundo limpa'. "
            "Para capa: 'composicao com espaco livre no terco inferior para sobreposicao de texto'. "
            "Para conteudo: 'composicao com espaco livre no topo e centro para sobreposicao de texto'. "
            "Para fechamento: 'composicao com espaco livre no centro para sobreposicao de texto'."
            "\n- 'title': texto do titulo que sera sobreposto via tipografia (max 50 chars)"
            "\n- 'body': texto complementar/explicativo (max 120 chars, opcional para capa)"
            "\n- 'cta_text': texto do CTA (APENAS no slide de fechamento, max 40 chars)"
            "\n\nREGRAS DE DESIGN:"
            "\n1. Defina 'style_anchor': identidade visual compartilhada DETALHADA (estilo artistico, "
            "iluminacao, textura, composicao base, atmosfera, angulo de camera) para COERENCIA VISUAL."
            "\n2. Defina 'color_palette' com 4 cores hex que funcionem juntas como identidade visual: "
            "primary (fundo de overlays), accent (cor de destaque/CTA), text_primary (cor do texto principal, "
            "geralmente branco ou preto dependendo do fundo), text_secondary (cor do texto secundario)."
            "\n3. Os prompts de imagem devem gerar FUNDOS que funcionem juntos como set visual coeso."
            "\n4. Cada prompt deve descrever a CENA VISUAL especifica, com mesma paleta de cores, iluminacao e estilo."
            "\n\nResponda SOMENTE com um JSON object, sem markdown, sem explicacao. "
            "Formato: {"
            "\"style_anchor\": \"descricao detalhada da identidade visual\", "
            "\"color_palette\": {\"primary\": \"#hex\", \"accent\": \"#hex\", \"text_primary\": \"#hex\", \"text_secondary\": \"#hex\"}, "
            f"\"slides\": [{{\"slide_number\": 1, \"role\": \"capa\", \"prompt\": \"...\", \"title\": \"...\", \"body\": \"...\", \"style\": \"{style}\"}}]"
            "}"
        )
        temperature = 0.5
    else:
        system_prompt = (
            "Voce e um gerador de prompts para imagens. "
            "Dado um tema e quantidade, gere prompts detalhados e VARIADOS para cada imagem. "
            "Cada prompt deve descrever uma cena, composicao e elementos visuais especificos. "
            "Responda SOMENTE com um JSON array, sem markdown, sem explicacao. "
            f"Formato: [{{\"slide_number\": 1, \"prompt\": \"...\", \"style\": \"{style}\"}}]"
        )
        temperature = 0.9

    user_prompt = f"Tema: {description}\nQuantidade: {num_slides}\nEstilo: {style}"

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[{"role": "user", "parts": [{"text": user_prompt}]}],
            config={"system_instruction": system_prompt, "temperature": temperature},
        )
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0]
        parsed = _json.loads(raw)

        if sequential and isinstance(parsed, dict):
            style_anchor = parsed.get("style_anchor", "")
            color_palette = parsed.get("color_palette", {})
            slides = parsed.get("slides", [])
            if isinstance(slides, list) and len(slides) > 0:
                for s in slides:
                    s["style_anchor"] = style_anchor
                    if color_palette:
                        s["color_palette"] = color_palette
                logger.info(
                    "expand_slides_from_description: %s slides (sequential, anchor=%s chars, palette=%s)",
                    len(slides), len(style_anchor), bool(color_palette),
                )
                return slides[:num_slides]
        elif isinstance(parsed, list) and len(parsed) > 0:
            logger.info("expand_slides_from_description: %s slides expandidos via LLM", len(parsed))
            return parsed[:num_slides]
    except Exception as e:
        logger.warning("expand_slides_from_description fallback (erro LLM): %s", e)

    return [
        {"slide_number": i + 1, "prompt": description, "style": style}
        for i in range(num_slides)
    ]


# ---------------------------------------------------------------------------
# Unified background processor
# ---------------------------------------------------------------------------

async def _process_image_background(
    carousel_id: str,
    user_id: str,
    slides: List[Dict[str, Any]],
    channel: str = "web",
    aspect_ratio: str = "4:3",
    reference_image: Optional[bytes] = None,
    task_id: Optional[str] = None,
    sequential_slides: bool = True,
    is_edit: bool = False,
):
    """
    Unified background processor for image generation and editing.

    When ``is_edit`` is True (single-slide edit with reference), uses simpler
    edit-specific WS events and flow.  Otherwise uses full carousel logic with
    sequential/parallel generation.
    """
    from src.endpoints.web import ws_manager
    from src.queue.task_queue import is_task_cancelled

    send_web = channel in ("web", "web_voice", "web_text", "web_whatsapp")
    send_whatsapp = channel in ("whatsapp_text", "whatsapp", "web_whatsapp")

    # --- Edit mode (single slide + reference) ---
    if is_edit and reference_image is not None and len(slides) == 1:
        await _process_edit_flow(
            carousel_id, user_id, slides[0], reference_image, aspect_ratio,
            channel, task_id, send_web, send_whatsapp,
        )
        return

    # --- Generation / carousel mode ---
    try:
        from src.config.system_config import get_config

        provider = get_image_provider()
        max_concurrent = int(get_config("max_concurrent_images", "3"))
        sem = asyncio.Semaphore(max_concurrent)

        def _build_full_prompt(slide: Dict[str, Any], is_continuation: bool = False) -> str:
            prompt = slide.get("prompt", "")
            style = slide.get("style", "")
            style_anchor = slide.get("style_anchor", "")
            role = slide.get("role", "conteudo")
            parts = []
            if style:
                parts.append(f"Style: {style}.")
            if style_anchor:
                parts.append(f"Visual identity: {style_anchor}.")

            if slide.get("title") or slide.get("cta_text"):
                if role == "capa":
                    parts.append("Composition: leave the bottom 40% of the image clean or with soft gradient — text will be overlaid there.")
                elif role == "conteudo":
                    parts.append("Composition: leave the top and center area somewhat clean — text will be overlaid there.")
                elif role == "fechamento":
                    parts.append("Composition: leave the center of the image open and clean — call-to-action text will be overlaid.")
                parts.append("IMPORTANT: Do not render any text, typography, letters, or words in the image. Generate a clean background only.")

            if is_continuation:
                parts.append(
                    "This image is part of a carousel series. "
                    "Use the SAME color palette, lighting style, and visual mood described in the visual identity above, "
                    "but create a COMPLETELY DIFFERENT scene and composition unique to this slide's topic."
                )

            parts.append(prompt)
            return " ".join(parts)

        def _apply_overlay(slide: Dict[str, Any], index: int, image_bytes: bytes) -> bytes:
            if not (slide.get("title") or slide.get("cta_text")):
                return image_bytes
            try:
                from src.tools.image_generation.text_overlay import apply_text_overlay, SlideText, ColorPalette

                slide_text = SlideText(
                    role=slide.get("role", "conteudo"),
                    title=slide.get("title", ""),
                    body=slide.get("body", ""),
                    slide_number=index + 1,
                    total_slides=len(slides),
                    cta_text=slide.get("cta_text", ""),
                )
                palette_data = slide.get("color_palette", {})
                palette = ColorPalette(
                    primary=palette_data.get("primary", "#1A1A2E"),
                    accent=palette_data.get("accent", "#E94560"),
                    text_primary=palette_data.get("text_primary", "#FFFFFF"),
                    text_secondary=palette_data.get("text_secondary", "#D0D0D0"),
                ) if palette_data else ColorPalette()
                return apply_text_overlay(image_bytes, slide_text, palette)
            except Exception as e:
                logger.warning("Text overlay falhou para slide %s, usando imagem sem overlay: %s", index + 1, e)
                return image_bytes

        async def _generate_single(slide: Dict[str, Any], index: int, ref: Optional[bytes] = None) -> tuple[bytes, bytes]:
            is_continuation = index > 0
            full_prompt = _build_full_prompt(slide, is_continuation=is_continuation)
            if ref is not None and index == 0:
                raw_bytes = await provider.edit(full_prompt, ref, aspect_ratio=aspect_ratio)
            else:
                raw_bytes = await provider.generate(full_prompt, aspect_ratio=aspect_ratio)
            overlaid_bytes = _apply_overlay(slide, index, raw_bytes)
            return overlaid_bytes, raw_bytes

        async def _upload_slide(image_bytes: bytes, index: int) -> str:
            loop = asyncio.get_event_loop()
            def _upload():
                webp_bytes = convert_to_webp(image_bytes)
                file_obj = io.BytesIO(webp_bytes)
                return cloudinary.uploader.upload(
                    file_obj,
                    folder="carousels",
                    public_id=f"{carousel_id}_slide_{index}",
                    overwrite=True,
                )
            upload_result = await loop.run_in_executor(None, _upload)
            return upload_result.get("secure_url")

        async def _generate_and_upload(slide: Dict[str, Any], index: int, ref: Optional[bytes] = None):
            async with sem:
                if task_id and is_task_cancelled(task_id):
                    logger.info("Slide %s pulado — task %s cancelada", index + 1, task_id)
                    return None, None, None

                overlaid_bytes, raw_bytes = await _generate_single(slide, index, ref)
                url = await _upload_slide(overlaid_bytes, index)
                slide["image_url"] = url
                logger.info("Slide %s gerado: %s", index + 1, url)

                await ws_manager.send_personal_message(user_id, {
                    "type": "slide_done",
                    "carousel_id": carousel_id,
                    "slide_index": index,
                    "total": len(slides),
                })

                return slide, overlaid_bytes, raw_bytes

        # --- Sequential mode: slide 1 first, then 2..N ---
        if sequential_slides and len(slides) > 1:
            logger.info("Modo sequencial: gerando slide 1 como referência para os demais")
            slide1_result, slide1_overlaid, slide1_raw = await _generate_and_upload(slides[0], 0, ref=reference_image)

            if slide1_result is None or slide1_raw is None:
                logger.warning("Slide 1 falhou/cancelado no modo sequencial, fazendo fallback paralelo")

            remaining_tasks = [
                _generate_and_upload(slide, i, ref=reference_image)
                for i, slide in enumerate(slides) if i > 0
            ]
            remaining_results = await asyncio.gather(*remaining_tasks, return_exceptions=True)
            all_results = [(slide1_result, slide1_overlaid, slide1_raw)] + list(remaining_results)
        else:
            tasks = [
                _generate_and_upload(slide, i, ref=reference_image)
                for i, slide in enumerate(slides)
            ]
            all_results = await asyncio.gather(*tasks, return_exceptions=True)

        updated_slides = []
        cancelled_count = 0
        for i, result in enumerate(all_results):
            if isinstance(result, Exception):
                logger.info("Slide %s falhou: %s", i + 1, result)
                slides[i]["image_url"] = None
                updated_slides.append(slides[i])
            elif isinstance(result, tuple):
                slide_data = result[0]
                if slide_data is None:
                    cancelled_count += 1
                    slides[i]["image_url"] = None
                    updated_slides.append(slides[i])
                else:
                    updated_slides.append(slide_data)
            elif result is None:
                cancelled_count += 1
                slides[i]["image_url"] = None
                updated_slides.append(slides[i])
            else:
                updated_slides.append(result)

        if task_id and is_task_cancelled(task_id):
            logger.info("Geração %s cancelada pelo usuario — ignorando resultado.", carousel_id)
            emit_event_sync(user_id, "carousel_generated")
            return

        if cancelled_count == len(slides):
            update_carousel_status(carousel_id, "failed", list(updated_slides))
            logger.info("Geração %s falhou (todos os slides pulados).", carousel_id)
            emit_event_sync(user_id, "carousel_generated")
            return

        update_carousel_status(carousel_id, "done", list(updated_slides))
        logger.info("Geração %s finalizada com sucesso.", carousel_id)
        emit_event_sync(user_id, "carousel_generated")

        # Store last generated image for edit chaining
        done_slides = [s for s in updated_slides if s.get("image_url")]
        if done_slides:
            store_generated_image(user_id, done_slides[-1]["image_url"])

        from src.events_broadcast import emit_action_log
        title = slides[0].get("style", "Imagem") if slides else "Imagem"
        label = f"{title} ({len(done_slides)} {'imagem' if len(done_slides) == 1 else 'imagens'})"
        await emit_action_log(user_id, "Imagem gerada", label, channel)

        await _notify_user(user_id, channel, carousel_id, list(updated_slides))

    except Exception as e:
        import traceback
        logger.error("Erro na geração em background: %s\n%s", e, traceback.format_exc())
        update_carousel_status(carousel_id, "failed", slides)
        emit_event_sync(user_id, "carousel_generated")

        if send_web:
            try:
                await ws_manager.send_personal_message(user_id, {
                    "type": "carousel_failed",
                    "carousel_id": carousel_id,
                })
                from src.models.chat_messages import update_message_by_prefix
                await asyncio.to_thread(
                    update_message_by_prefix, user_id,
                    "__CAROUSEL_GENERATING__",
                    f"__CAROUSEL_FAILED__{carousel_id}",
                )
            except Exception:
                pass


async def _process_edit_flow(
    carousel_id: str,
    user_id: str,
    slide: Dict[str, Any],
    reference_bytes: bytes,
    aspect_ratio: str,
    channel: str,
    task_id: Optional[str],
    send_web: bool,
    send_whatsapp: bool,
):
    """Simplified flow for single-image editing with a reference."""
    from src.endpoints.web import ws_manager

    edit_prompt = slide.get("prompt", "")

    try:
        provider = get_image_provider()

        if send_web:
            await ws_manager.send_personal_message(user_id, {
                "type": "image_editing",
                "prompt": edit_prompt[:100],
            })
            try:
                import json as _json
                from src.models.chat_messages import save_message
                placeholder = "__IMAGE_EDITING__" + _json.dumps({"prompt": edit_prompt[:100]})
                await asyncio.to_thread(save_message, user_id, user_id, "agent", placeholder)
            except Exception as e:
                logger.error("Erro ao persistir placeholder de edição: %s", e)

        if task_id:
            from src.queue.task_queue import is_task_cancelled
            if is_task_cancelled(task_id):
                logger.info("Edição cancelada antes de iniciar — task %s", task_id)
                raise Exception("cancelled by user")

        result_bytes = await provider.edit(edit_prompt, reference_bytes, aspect_ratio=aspect_ratio)

        loop = asyncio.get_event_loop()

        def _upload():
            webp_bytes = convert_to_webp(result_bytes)
            file_obj = io.BytesIO(webp_bytes)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            return cloudinary.uploader.upload(
                file_obj,
                folder=f"edited_images/{user_id}",
                public_id=f"edit_{ts}",
                overwrite=True,
            )

        upload_result = await loop.run_in_executor(None, _upload)
        image_url = upload_result.get("secure_url")
        store_generated_image(user_id, image_url)
        logger.info("Imagem editada: %s", image_url)

        from src.events_broadcast import emit_action_log
        await emit_action_log(user_id, "Imagem editada", edit_prompt[:100], channel)

        if send_web:
            await ws_manager.send_personal_message(user_id, {
                "type": "image_edit_ready",
                "image_url": image_url,
                "prompt": edit_prompt[:100],
            })
            try:
                from src.models.chat_messages import update_message_by_prefix
                formatted = f"Pronto! Aqui está a imagem editada:\n{image_url}"
                updated = await asyncio.to_thread(
                    update_message_by_prefix, user_id,
                    "__IMAGE_EDITING__",
                    formatted,
                )
                if not updated:
                    from src.models.chat_messages import save_message
                    await asyncio.to_thread(save_message, user_id, user_id, "agent", formatted)
            except Exception as e:
                logger.error("Erro ao persistir mensagem: %s", e)

        if send_whatsapp:
            try:
                from src.integrations.whatsapp import whatsapp_client
                await whatsapp_client.send_image(user_id, image_url, caption="Aqui está a imagem editada!")
            except Exception as e:
                logger.error("Erro ao enviar imagem via WhatsApp: %s", e)

        # Save to gallery
        try:
            slide_data = [{"prompt": edit_prompt[:200], "style": "Edição de imagem", "image_url": image_url}]
            update_carousel_status(carousel_id, "done", slide_data)
            emit_event_sync(user_id, "carousel_generated")
        except Exception as e:
            logger.error("Erro ao salvar na galeria: %s", e)

        from src.integrations.image_storage import index_user_image
        await asyncio.to_thread(index_user_image, user_id, image_url, f"Imagem editada: {edit_prompt[:200]}")

    except Exception as e:
        import traceback
        logger.error("Erro na edição: %s\n%s", e, traceback.format_exc())

        if send_web:
            try:
                await ws_manager.send_personal_message(user_id, {
                    "type": "image_edit_ready",
                    "image_url": None,
                    "error": str(e),
                    "prompt": edit_prompt[:100],
                })
                from src.models.chat_messages import update_message_by_prefix
                await asyncio.to_thread(
                    update_message_by_prefix, user_id,
                    "__IMAGE_EDITING__",
                    "❌ Erro ao editar a imagem. Tente novamente.",
                )
            except Exception:
                pass
        if send_whatsapp:
            try:
                from src.integrations.whatsapp import whatsapp_client
                await whatsapp_client.send_text_message(
                    user_id, "❌ Eita, deu um erro ao editar a imagem. Tenta de novo!"
                )
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Notification helpers
# ---------------------------------------------------------------------------

async def _notify_user(user_id: str, channel: str, carousel_id: str, slides: List[Dict[str, Any]]):
    """Envia o resultado pelo canal correto após a geração."""
    done_slides = [s for s in slides if s.get("image_url")]
    if not done_slides:
        return

    send_whatsapp = channel in ("whatsapp_text", "whatsapp", "web_whatsapp")
    send_web = channel in ("web", "web_voice", "web_text", "web_whatsapp")

    if send_whatsapp:
        await _notify_whatsapp(user_id, done_slides)

    if send_web:
        from src.endpoints.web import ws_manager
        delivered = await ws_manager.send_personal_message(user_id, {
            "type": "carousel_ready",
            "carousel_id": carousel_id,
            "slides": done_slides,
        })
        logger.info("[NOTIFY] carousel_ready enviado via WS para %s: delivered=%s", user_id[:8], delivered)

        try:
            import json as _json
            from src.models.chat_messages import update_message_by_prefix
            ready_payload = _json.dumps({
                "carousel_id": carousel_id,
                "slides": [
                    {
                        "slide_number": s.get("slide_number") or (i + 1),
                        "style": s.get("style", ""),
                        "image_url": s.get("image_url", ""),
                    }
                    for i, s in enumerate(done_slides)
                ],
            })
            updated = await asyncio.to_thread(
                update_message_by_prefix, user_id,
                "__CAROUSEL_GENERATING__",
                f"__CAROUSEL_READY__{ready_payload}",
            )
            if not updated:
                from src.models.chat_messages import save_message
                await asyncio.to_thread(save_message, user_id, user_id, "agent", f"__CAROUSEL_READY__{ready_payload}")
        except Exception as e:
            logger.error("Erro ao persistir mensagem de carrossel: %s", e)


async def _notify_whatsapp(user_id: str, slides: List[Dict[str, Any]]):
    """Envia as imagens geradas como mídia no WhatsApp do usuário."""
    try:
        from src.integrations.whatsapp import whatsapp_client

        total = len(slides)

        async def _send_with_retry(coro_fn, retries=2, delay=3):
            for attempt in range(retries + 1):
                try:
                    return await coro_fn()
                except Exception as e:
                    if attempt < retries:
                        logger.warning("WhatsApp send retry %s/%s apos erro: %s", attempt + 1, retries, e)
                        await asyncio.sleep(delay)
                    else:
                        raise

        if total > 1:
            await _send_with_retry(
                lambda: whatsapp_client.send_text_message(
                    user_id, f"✅ Suas imagens ficaram prontas! Enviando {total} slides..."
                )
            )

        for i, slide in enumerate(slides):
            url = slide.get("image_url")
            if not url:
                continue
            num = slide.get("slide_number") or (i + 1)
            style = slide.get("style", "")
            if total > 1:
                caption = f"Slide {num}/{total}"
                if style:
                    caption += f" — {style}"
            else:
                caption = "Aqui está sua imagem!"
            try:
                await _send_with_retry(
                    lambda u=url, c=caption: whatsapp_client.send_image(user_id, u, caption=c)
                )
            except Exception as img_err:
                logger.error("Erro ao enviar slide %s via WhatsApp: %s", num, img_err, exc_info=True)
                try:
                    await whatsapp_client.send_text_message(
                        user_id,
                        f"Tive um problema ao enviar a imagem {num}. Ela está salva no seu painel web!",
                    )
                except Exception:
                    pass

        logger.info("%s slides enviados via WhatsApp para %s", total, user_id)
    except Exception as e:
        logger.error("Erro ao enviar resultado via WhatsApp: %s", e, exc_info=True)


def _send_destination_feedback(user_id: str, origin_channel: str, effective_channel: str, num_slides: int):
    """Envia feedback imediato no canal de DESTINO quando é cross-channel."""
    import asyncio as _aio

    send_to_whatsapp = "whatsapp" in effective_channel and "whatsapp" not in origin_channel
    send_to_web = effective_channel in ("web_text", "web_whatsapp") and origin_channel not in ("web", "web_voice", "web_text")

    label = "imagem" if num_slides == 1 else f"{num_slides} imagens"
    msg = f"📩 Recebi seu pedido! Gerando {label}, já te mando aqui."

    if send_to_whatsapp:
        try:
            from src.integrations.whatsapp import whatsapp_client
            _loop = _aio.get_event_loop()
            _loop.create_task(whatsapp_client.send_text_message(user_id, msg))
        except Exception as e:
            logger.error("Erro ao enviar feedback WhatsApp destino: %s", e)

    if send_to_web:
        try:
            from src.endpoints.web import ws_manager
            _loop = _aio.get_event_loop()
            _loop.create_task(ws_manager.send_personal_message(user_id, {
                "type": "response",
                "text": msg,
                "audio_b64": "",
                "mime_type": "none",
                "needs_follow_up": False,
            }))
        except Exception as e:
            logger.error("Erro ao enviar feedback web destino: %s", e)


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------

def create_image_tools(user_id: str, channel: str = "web"):
    """
    Factory que cria as tools de geração de imagem com user_id e canal pré-injetados.
    Returns (generate_image_tool, list_gallery_tool).
    """

    def generate_image_tool(
        title: str,
        slides: List[Dict[str, str]],
        format: str = "1350x1080",
        reference_source: str = "",
        sequential_slides: bool = True,
        delivery_channel: str = "",
        preset_name: str = "",
    ) -> str:
        """
        Gera ou edita imagens com IA. Pode ser uma imagem única (1 slide), carrossel (N slides)
        ou edição de foto existente (com reference_source).
        As imagens são geradas em background e o usuário é notificado ao terminar.

        Args:
            title: Título descritivo da geração.
            slides: Lista de dicionários com as chaves:
                    - 'slide_number': (opcional) número do slide
                    - 'role': papel narrativo do slide ('capa', 'conteudo' ou 'fechamento')
                    - 'prompt': descrição detalhada da imagem / instrução de edição
                    - 'style': estilo visual (ex: Clean/Mockup, Cinemático, Fotorrealista)
                    - 'style_anchor': (opcional) bloco de identidade visual compartilhado
                    - 'title': (opcional) texto sobreposto na imagem via tipografia profissional
                    - 'body': (opcional) texto complementar sobreposto
                    - 'cta_text': (opcional, só no fechamento) texto de CTA sobreposto
                    Para EDIÇÃO de foto, basta 1 slide com 'prompt' descrevendo a modificação.
            format: Formato/dimensão das imagens. Exemplos:
                    - "1350x1080" → carrossel Instagram landscape (padrão)
                    - "1080x1080" → quadrado
                    - "1080x1350" → portrait / feed vertical
                    - "1080x1920" → stories / reels
                    - "16:9" → widescreen
                    - "1:1" → quadrado
            reference_source: Qual imagem usar como referência para EDIÇÃO.
                    - "" (vazio, padrão): Gera do zero, sem referência. Use para imagens/carrosseis NOVOS.
                    - "original": Usa a foto ORIGINAL enviada pelo usuário.
                      PREFIRA quando o pedido envolve mudança de estilo radical
                      (ex: "faz mais realista", "muda o estilo", "transforma em cartoon").
                    - "last_generated": Usa a ÚLTIMA imagem gerada/editada.
                      Use para ajustes incrementais (ex: "muda o fundo", "adiciona um chapéu").
                    - "auto": Escolhe automaticamente entre original e última gerada.
                    SOMENTE preencha quando o usuário EXPLICITAMENTE pedir para editar/modificar uma imagem.
            sequential_slides: Se True (padrão), gera o slide 1 primeiro e mantém
                               coerência visual nos demais. Use False para coleções independentes.
            delivery_channel: DEIXE VAZIO ("") na maioria dos casos — o sistema ja sabe o canal correto.
                              Preencha APENAS se o USUARIO disser EXPLICITAMENTE 'manda na web',
                              'envia no zap', 'manda nos dois'. Se nao mencionou, DEIXE VAZIO.
                              Valores quando necessario: 'whatsapp', 'web', 'ambos'.
            preset_name: Nome de um preset/template de estilo salvo pelo usuario.

        Returns:
            Mensagem de confirmação imediata.
        """
        from src.tools.image_generation.base import resolve_aspect_ratio
        from src.queue.task_queue import check_daily_limit

        limit_msg = check_daily_limit(user_id)
        if limit_msg:
            return limit_msg

        # Apply preset or brand profile
        preset_applied = False
        if preset_name:
            try:
                from src.models.carousel_presets import get_preset_by_name
                preset = get_preset_by_name(user_id, preset_name)
                if preset:
                    preset_palette = preset.get("color_palette", {})
                    preset_style = preset.get("style_anchor", "")
                    if preset.get("default_format") and not format:
                        format = preset["default_format"]
                    for slide in slides:
                        if preset_palette and not slide.get("color_palette"):
                            slide["color_palette"] = preset_palette
                        if preset_style and not slide.get("style_anchor"):
                            slide["style_anchor"] = preset_style
                    preset_applied = True
                    logger.info("Preset '%s' aplicado", preset["name"])
            except Exception as e:
                logger.warning("Erro ao aplicar preset: %s", e)

        if not preset_applied:
            try:
                from src.models.branding import get_default_brand_profile
                brand = get_default_brand_profile(user_id)
                if brand:
                    brand_palette = {
                        "primary": brand["bg_color"] or brand["primary_color"],
                        "accent": brand["accent_color"],
                        "text_primary": brand["text_primary_color"],
                        "text_secondary": brand["text_secondary_color"],
                    }
                    brand_style = brand.get("style_description", "")
                    for slide in slides:
                        if not slide.get("color_palette"):
                            slide["color_palette"] = brand_palette
                        if brand_style and not slide.get("style_anchor"):
                            slide["style_anchor"] = brand_style
                    logger.info("Branding '%s' aplicado", brand["name"])
            except Exception as e:
                logger.warning("Erro ao aplicar branding: %s", e)

        # Resolve delivery_channel override
        effective_channel = channel
        if delivery_channel:
            from src.integrations.channel_router import resolve_channel
            resolved = resolve_channel(delivery_channel)
            if resolved and resolved != channel:
                logger.warning(
                    "generate_image_tool | CROSS-CHANNEL OVERRIDE | user=%s | origin=%s | requested=%s | resolved=%s",
                    user_id, channel, delivery_channel, resolved,
                )
                from src.memory.analytics import log_event
                log_event(
                    user_id=user_id, channel=channel,
                    event_type="cross_channel_image_delivery", status="warning",
                    extra_data={"origin": channel, "effective": resolved, "delivery_param": delivery_channel, "title": title},
                )
            if resolved:
                effective_channel = resolved

        aspect_ratio = resolve_aspect_ratio(format)
        format_label = format.strip() or "1350x1080"

        # Resolve reference image (for edit mode)
        ref_url = None
        is_edit = False
        if reference_source:
            session = get_session_images(user_id)
            originals = session.get("originals", [])
            last_gen = session.get("last_generated")

            if not originals and not last_gen:
                recovered = _try_recover_last_image(user_id)
                if recovered:
                    store_generated_image(user_id, recovered)
                    last_gen = recovered

            if not originals and not last_gen:
                return (
                    "Não encontrei nenhuma imagem na conversa atual. "
                    "O usuário precisa enviar uma imagem junto com o pedido de edição."
                )

            if reference_source == "original":
                ref_url = originals[0] if originals else last_gen
            elif reference_source == "last_generated":
                ref_url = last_gen if last_gen else (originals[0] if originals else None)
            else:  # "auto"
                ref_url = originals[0] if originals else last_gen

            if not ref_url:
                return "Não encontrei a imagem de referência solicitada."

            is_edit = len(slides) == 1

        ref_label = (
            "edição" if is_edit
            else "com referência" if ref_url
            else "sem referência"
        )
        logger.info(
            "generate_image_tool | user=%s | channel=%s | effective=%s | format=%s (%s) | %s | title='%s' | slides=%s",
            user_id, channel, effective_channel, format_label, aspect_ratio,
            ref_label, title, len(slides),
        )

        try:
            carousel_id = create_carousel(user_id, title, slides)
            logger.info("Registro criado no banco: %s", carousel_id)

            from src.queue.task_queue import enqueue_task
            result = enqueue_task(user_id, "image", effective_channel, {
                "carousel_id": carousel_id,
                "slides": list(slides),
                "aspect_ratio": aspect_ratio,
                "reference_image_url": ref_url,
                "sequential_slides": sequential_slides,
                "is_edit": is_edit,
            })

            canal_label = "WhatsApp" if "whatsapp" in effective_channel else "painel de Imagens na web"
            ref_msg = " usando a imagem enviada como referência" if ref_url else ""

            if result["status"] == "queued":
                if is_edit:
                    # Edit mode — WS events handled by _process_edit_flow
                    _send_destination_feedback(user_id, channel, effective_channel, 1)
                    if effective_channel in ("web", "web_voice", "web_text", "web_whatsapp"):
                        return (
                            "[IMAGEM EM EDICAO — NAO ESCREVA NADA SOBRE ISSO. "
                            "O usuario ja esta vendo o progresso visualmente na interface. "
                            "Responda APENAS se o usuario tiver feito uma pergunta adicional, caso contrario fique em silencio.]"
                        )
                    source_label = "original" if ref_url == (get_session_images(user_id).get("originals", [None])[0] if get_session_images(user_id).get("originals") else None) else "última gerada"
                    return (
                        f"Edição de imagem na fila (ref: {source_label})! "
                        f"Posição {result['position']}. Estimativa: ~{result['estimated_wait']}."
                    )

                # Generation mode (carousel or single image)
                if effective_channel in ("web", "web_voice", "web_text", "web_whatsapp"):
                    emit_event_sync(user_id, "carousel_generating", {
                        "carousel_id": carousel_id,
                        "num_slides": len(slides),
                        "title": title,
                    })
                    try:
                        import json as _json
                        from src.models.chat_messages import save_message
                        placeholder = "__CAROUSEL_GENERATING__" + _json.dumps({
                            "carousel_id": carousel_id,
                            "num_slides": len(slides),
                            "slides_done": 0,
                        })
                        save_message(user_id, user_id, "agent", placeholder)
                    except Exception as e:
                        logger.error("Erro ao persistir placeholder de carrossel: %s", e)

                _send_destination_feedback(user_id, channel, effective_channel, len(slides))

                if effective_channel in ("web", "web_voice", "web_text", "web_whatsapp"):
                    return (
                        "[IMAGENS EM GERACAO — NAO ESCREVA NADA SOBRE ISSO. "
                        "O usuario ja esta vendo o progresso visualmente na interface. "
                        "Responda APENAS se o usuario tiver feito uma pergunta adicional, caso contrario fique em silencio.]"
                    )
                return (
                    f"'{title}' com {len(slides)} {'imagem' if len(slides) == 1 else 'imagens'} na fila ({format_label}{ref_msg}). "
                    f"Posição {result['position']}. Estimativa: ~{result['estimated_wait']}. "
                    f"Avisarei pelo {canal_label} quando estiver pronto!"
                )
            elif result["status"] == "limit_reached":
                return f"Você já tem {result['pending_count']} pedidos na fila. Aguarde os anteriores terminarem!"
            elif result["status"] == "daily_limit":
                return f"Limite de {result['daily_limit']} gerações por dia atingido. Tente amanhã!"

            return "Erro desconhecido ao colocar na fila."
        except Exception as e:
            import traceback
            logger.error("Erro ao iniciar geração: %s\n%s", e, traceback.format_exc())
            return f"Erro ao iniciar a geração: {e}"

    def list_gallery_tool() -> str:
        """
        Lista as imagens e carrosséis já gerados pelo usuário.

        Returns:
            Resumo das gerações com seus status.
        """
        from src.models.carousel import list_user_carousels

        carousels = list_user_carousels(user_id).get("carousels", [])
        if not carousels:
            return "Nenhuma imagem gerada ainda."

        result = []
        for c in carousels:
            status = c.get("status")
            title = c.get("title", "Sem título")
            result.append(f"- {title} ({status})")
        return "\n".join(result)

    return generate_image_tool, list_gallery_tool

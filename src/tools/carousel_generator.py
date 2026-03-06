import asyncio
import io
import os
from typing import List, Dict, Any, Optional
from src.tools.image_editor import get_session_images
from src.models.carousel import create_carousel, update_carousel_status
from src.tools.image_generation import get_image_provider
from src.events import emit_event_sync
import cloudinary
import cloudinary.uploader

if os.getenv("CLOUDINARY_CLOUD_NAME"):
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
        secure=True
    )


async def _process_carousel_background(
    carousel_id: str,
    user_id: str,
    slides: List[Dict[str, Any]],
    channel: str = "web",
    aspect_ratio: str = "4:3",
    reference_image: Optional[bytes] = None,
):
    """
    Gera imagens em paralelo, faz upload no Cloudinary e notifica o usuário
    pelo canal de origem (web via WS ou whatsapp via API).
    Se reference_image for fornecido, usa como contexto visual para cada slide.
    """
    try:
        from src.endpoints.web import ws_manager
        from src.config.system_config import get_config
        
        await ws_manager.send_personal_message(user_id, {
            "type": "carousel_generating",
            "carousel_id": carousel_id,
            "title": slides[0].get("style", "Carrossel") if slides else "Carrossel",
            "num_slides": len(slides),
        })

        provider = get_image_provider()
        
        max_concurrent = int(get_config("max_concurrent_images", "3"))
        sem = asyncio.Semaphore(max_concurrent)

        async def _generate_and_upload(slide: Dict[str, Any], index: int):
            async with sem:
                prompt = slide.get("prompt", "")
                style = slide.get("style", "")
                full_prompt = f"Style: {style}. {prompt}"
    
                if reference_image:
                    image_bytes = await provider.edit(full_prompt, reference_image, aspect_ratio=aspect_ratio)
                else:
                    image_bytes = await provider.generate(full_prompt, aspect_ratio=aspect_ratio)
    
                loop = asyncio.get_event_loop()
    
                def _upload():
                    file_obj = io.BytesIO(image_bytes)
                    return cloudinary.uploader.upload(
                        file_obj,
                        folder="carousels",
                        public_id=f"{carousel_id}_slide_{index}",
                        overwrite=True,
                    )
    
                upload_result = await loop.run_in_executor(None, _upload)
                slide["image_url"] = upload_result.get("secure_url")
                print(f"[CAROUSEL] Slide {index + 1} gerado: {slide['image_url']}")

                await ws_manager.send_personal_message(user_id, {
                    "type": "slide_done",
                    "carousel_id": carousel_id,
                    "slide_index": index,
                    "total": len(slides),
                })

                return slide

        tasks = [_generate_and_upload(slide, i) for i, slide in enumerate(slides)]
        # return_exceptions=True garante que uma falha individual não cancela os outros slides
        results = await asyncio.gather(*tasks, return_exceptions=True)

        updated_slides = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"[CAROUSEL] Slide {i + 1} falhou: {result}")
                slides[i]["image_url"] = None
                updated_slides.append(slides[i])
            else:
                updated_slides.append(result)

        update_carousel_status(carousel_id, "done", list(updated_slides))
        print(f"[CAROUSEL] Carrossel {carousel_id} finalizado com sucesso.")

        emit_event_sync(user_id, "carousel_generated")

        from src.events_broadcast import emit_action_log
        title = slides[0].get("style", "Carrossel") if slides else "Carrossel"
        await emit_action_log(user_id, "Carrossel gerado", f"{title} ({len(updated_slides)} slides)", channel)

        # Envia de volta pelo canal de origem
        await _notify_user(user_id, channel, list(updated_slides))
    except Exception as e:
        import traceback
        print(f"[CAROUSEL] Erro na geração em background: {e}\n{traceback.format_exc()}")
        update_carousel_status(carousel_id, "failed", slides)
        emit_event_sync(user_id, "carousel_generated")


async def _notify_user(user_id: str, channel: str, slides: List[Dict[str, Any]]):
    """Envia o resultado pelo canal correto após a geração."""

    done_slides = [s for s in slides if s.get("image_url")]
    if not done_slides:
        return

    if channel in ("whatsapp_text", "whatsapp"):
        await _notify_whatsapp(user_id, done_slides)

    elif channel in ("web", "web_voice", "web_text"):
        from src.endpoints.web import ws_manager
        await ws_manager.send_personal_message(user_id, {
            "type": "carousel_ready",
            "slides": done_slides,
        })

        # Persiste o carrossel no histórico de chat igual ao que o frontend renderiza
        try:
            from src.models.chat_messages import save_message
            lines = []
            for i, s in enumerate(done_slides):
                num = s.get("slide_number") or (i + 1)
                style = f" — {s['style']}" if s.get("style") else ""
                url = s.get("image_url", "")
                lines.append(f"**Slide {num}{style}**\n{url}")
            formatted = f"🎨 Carrossel pronto! Confira os {len(done_slides)} slides:\n\n" + "\n\n".join(lines)
            await asyncio.to_thread(save_message, user_id, user_id, "agent", formatted)
        except Exception as e:
            print(f"[CAROUSEL] Erro ao persistir mensagem de carrossel: {e}")


async def _notify_whatsapp(user_id: str, slides: List[Dict[str, Any]]):
    """Envia as imagens geradas como mídia no WhatsApp do usuário."""
    try:
        from src.integrations.whatsapp import whatsapp_client

        total = len(slides)
        await whatsapp_client.send_text_message(
            user_id,
            f"✅ Seu carrossel ficou pronto! Enviando {total} slides..."
        )

        for i, slide in enumerate(slides):
            url = slide.get("image_url")
            if not url:
                continue
            num = slide.get("slide_number") or (i + 1)
            style = slide.get("style", "")
            caption = f"Slide {num}/{total}"
            if style:
                caption += f" — {style}"
            try:
                await whatsapp_client.send_image(user_id, url, caption=caption)
            except Exception as img_err:
                print(f"[CAROUSEL] Erro ao enviar slide {num} via WhatsApp: {img_err}")
                await whatsapp_client.send_text_message(user_id, f"Slide {num}: {url}")

        print(f"[CAROUSEL] {total} slides enviados via WhatsApp para {user_id}")
    except Exception as e:
        print(f"[CAROUSEL] Erro ao enviar resultado via WhatsApp: {e}")


def create_carousel_tools(user_id: str, channel: str = "web"):
    """
    Factory que cria as tools de carrossel com user_id e canal de origem pre-injetados.
    """

    def generate_carousel_tool(
        title: str,
        slides: List[Dict[str, str]],
        format: str = "1350x1080",
        use_reference_image: bool = False,
    ) -> str:
        """
        Inicia a geração de um carrossel para o Instagram.
        Gera as imagens em background e notifica o usuário ao terminar,
        pelo mesmo canal em que o pedido foi feito (web ou whatsapp).

        Args:
            title: Título do carrossel.
            slides: Lista de dicionários com as chaves:
                    - 'slide_number': (opcional) número do slide
                    - 'prompt': descrição detalhada da imagem
                    - 'style': estilo visual (ex: Clean/Mockup, Cinemático)
            format: Formato/dimensão das imagens. Exemplos:
                    - "1350x1080" → carrossel Instagram landscape (padrão)
                    - "1080x1080" → quadrado
                    - "1080x1350" → portrait / feed vertical
                    - "1080x1920" → stories / reels
                    - "16:9" → widescreen
                    O agente DEVE confirmar o formato com o usuário antes de chamar esta tool.
            use_reference_image: Se True, usa a imagem enviada pelo usuário como
                                 contexto/referência visual para TODOS os slides.
                                 Ideal quando o usuário envia uma foto e pede um
                                 carrossel baseado nela (ex: "gera um carrossel
                                 baseado nessa imagem", "usa essa foto como referência").
                                 SEMPRE ative quando o usuário enviar uma imagem junto
                                 com o pedido de carrossel.

        Returns:
            Mensagem de confirmação imediata com o formato que será usado.
        """
        from src.tools.image_generation.base import resolve_aspect_ratio

        aspect_ratio = resolve_aspect_ratio(format)
        format_label = format.strip() or "1350x1080"

        ref_url = None
        if use_reference_image:
            session = get_session_images(user_id)
            originals = session.get("originals", [])
            last_gen = session.get("last_generated")
            if originals:
                ref_url = originals[0]
            elif last_gen:
                ref_url = last_gen
            else:
                from src.tools.image_editor import _try_recover_last_image, store_generated_image
                recovered = _try_recover_last_image(user_id)
                if recovered:
                    store_generated_image(user_id, recovered)
                    ref_url = recovered

        ref_label = "com referência" if ref_url else "sem referência"
        print(f"[CAROUSEL] generate_carousel_tool | user={user_id} | channel={channel} | format={format_label} ({aspect_ratio}) | {ref_label} | title='{title}' | slides={len(slides)}")

        try:
            carousel_id = create_carousel(user_id, title, slides)
            print(f"[CAROUSEL] Registro criado no banco: {carousel_id}")

            from src.queue.task_queue import enqueue_task
            result = enqueue_task(user_id, "carousel", channel, {
                "carousel_id": carousel_id,
                "slides": list(slides),
                "aspect_ratio": aspect_ratio,
                "reference_image_url": ref_url,
            })
            
            canal_label = "WhatsApp" if "whatsapp" in channel else "painel de Imagens na web"
            ref_msg = " usando a imagem enviada como referência" if ref_url else ""
            
            if result["status"] == "queued":
                return (
                    f"Carrossel '{title}' com {len(slides)} slides na fila ({format_label}{ref_msg}). "
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
            print(f"[CAROUSEL] Erro ao iniciar geração: {e}\n{traceback.format_exc()}")
            return f"Erro ao iniciar a geração do carrossel: {e}"

    def list_carousels_tool() -> str:
        """
        Lista os carrosséis já gerados pelo usuário.

        Returns:
            Resumo dos carrosséis com seus status.
        """
        from src.models.carousel import list_user_carousels
        carousels = list_user_carousels(user_id).get("carousels", [])
        if not carousels:
            return "Nenhum carrossel encontrado."

        result = []
        for c in carousels:
            status = c.get("status")
            title = c.get("title", "Sem título")
            result.append(f"- {title} ({status})")
        return "\n".join(result)

    return generate_carousel_tool, list_carousels_tool

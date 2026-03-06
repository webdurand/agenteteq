import asyncio
import io
import os
from typing import List, Dict, Any, Optional
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
    aspect_ratio: str = "4:3"
):
    """
    Gera imagens em paralelo, faz upload no Cloudinary e notifica o usuário
    pelo canal de origem (web via WS ou whatsapp via API).
    """
    try:
        provider = get_image_provider()

        async def _generate_and_upload(slide: Dict[str, Any], index: int):
            prompt = slide.get("prompt", "")
            style = slide.get("style", "")
            full_prompt = f"Style: {style}. {prompt}"

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

        # Notifica o frontend via WS (sempre, para atualizar o painel)
        emit_event_sync(user_id, "carousel_generated")

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
        reference_images: List[str] = []
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
            reference_images: (opcional) URLs de imagens de referência do usuário.

        Returns:
            Mensagem de confirmação imediata com o formato que será usado.
        """
        from src.tools.image_generation.base import resolve_aspect_ratio

        aspect_ratio = resolve_aspect_ratio(format)
        format_label = format.strip() or "1350x1080"

        print(f"[CAROUSEL] generate_carousel_tool | user={user_id} | channel={channel} | format={format_label} ({aspect_ratio}) | title='{title}' | slides={len(slides)}")

        try:
            carousel_id = create_carousel(user_id, title, slides, reference_images)
            print(f"[CAROUSEL] Registro criado no banco: {carousel_id}")

            from src.events import _main_loop
            if _main_loop and _main_loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    _process_carousel_background(carousel_id, user_id, list(slides), channel, aspect_ratio),
                    _main_loop
                )
            else:
                print("[CAROUSEL] AVISO: loop principal não disponível.")

            canal_label = "WhatsApp" if "whatsapp" in channel else "painel de Imagens na web"
            return (
                f"Carrossel '{title}' com {len(slides)} slides iniciado no formato {format_label} ({aspect_ratio})! "
                f"As imagens estão sendo geradas agora em paralelo e serão enviadas para você no {canal_label} assim que ficarem prontas — "
                "geralmente leva entre 1 a 3 minutos."
            )
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

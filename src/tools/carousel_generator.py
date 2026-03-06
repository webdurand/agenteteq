import asyncio
import io
import os
from typing import List, Dict, Any
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


async def _process_carousel_background(carousel_id: str, user_id: str, slides: List[Dict[str, Any]]):
    """
    Função assíncrona rodada no event loop principal para gerar imagens em paralelo,
    fazer upload no Cloudinary e notificar via WebSocket.
    """
    try:
        provider = get_image_provider()

        async def _generate_and_upload(slide: Dict[str, Any], index: int):
            prompt = slide.get("prompt", "")
            style = slide.get("style", "")
            full_prompt = f"Style: {style}. {prompt}"

            image_bytes = await provider.generate(full_prompt, aspect_ratio="4:5")

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
        updated_slides = await asyncio.gather(*tasks)

        update_carousel_status(carousel_id, "done", list(updated_slides))
        print(f"[CAROUSEL] Carrossel {carousel_id} finalizado com sucesso.")
        emit_event_sync(user_id, "carousel_generated")

    except Exception as e:
        import traceback
        print(f"[CAROUSEL] Erro na geração em background: {e}\n{traceback.format_exc()}")
        update_carousel_status(carousel_id, "failed", slides)
        emit_event_sync(user_id, "carousel_generated")


def create_carousel_tools(user_id: str):
    """
    Factory que cria as tools de carrossel com o user_id pre-injetado.
    """

    def generate_carousel_tool(
        title: str,
        slides: List[Dict[str, str]],
        reference_images: List[str] = []
    ) -> str:
        """
        Inicia a geração de um carrossel para o Instagram.
        Gera as imagens em background e notifica o usuário ao terminar.

        Args:
            title: Título do carrossel.
            slides: Lista de dicionários, cada um contendo as chaves:
                    - 'slide_number': (opcional) número do slide
                    - 'prompt': descrição detalhada da imagem a ser gerada
                    - 'style': estilo visual (ex: Clean/Mockup, Cinemático)
            reference_images: (opcional) Lista de URLs de imagens de referência enviadas pelo usuário.

        Returns:
            Mensagem de confirmação imediata.
        """
        print(f"[CAROUSEL] generate_carousel_tool | user={user_id} | title='{title}' | slides={len(slides)}")

        try:
            carousel_id = create_carousel(user_id, title, slides, reference_images)
            print(f"[CAROUSEL] Registro criado no banco: {carousel_id}")

            # Como a tool roda em thread (asyncio.to_thread), precisamos usar o loop
            # principal do servidor para agendar a corrotina de background.
            from src.events import _main_loop
            if _main_loop and _main_loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    _process_carousel_background(carousel_id, user_id, list(slides)),
                    _main_loop
                )
            else:
                print("[CAROUSEL] AVISO: loop principal não disponível, tentando fallback.")
                loop = asyncio.new_event_loop()
                loop.run_until_complete(
                    _process_carousel_background(carousel_id, user_id, list(slides))
                )

            return (
                f"Carrossel '{title}' com {len(slides)} slides iniciado! "
                "As imagens estão sendo geradas agora em paralelo. "
                "Avise o usuário que o painel de Imagens na web será atualizado automaticamente assim que ficarem prontas — "
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
        carousels = list_user_carousels(user_id)
        if not carousels:
            return "Nenhum carrossel encontrado."

        result = []
        for c in carousels:
            status = c.get("status")
            title = c.get("title", "Sem título")
            result.append(f"- {title} ({status})")
        return "\n".join(result)

    return generate_carousel_tool, list_carousels_tool

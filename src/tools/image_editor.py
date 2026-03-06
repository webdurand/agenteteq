import asyncio
import io
import os
from typing import List, Optional
from datetime import datetime
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

# Cache de imagens recentes por sessão para que a tool consiga acessar
# os bytes das imagens enviadas pelo usuário na mesma requisição.
_session_images: dict[str, list[bytes]] = {}


def store_session_images(session_id: str, images: list[bytes]):
    _session_images[session_id] = images


def get_session_images(session_id: str) -> list[bytes]:
    return _session_images.get(session_id, [])


def clear_session_images(session_id: str):
    _session_images.pop(session_id, None)


async def _process_edit_background(
    user_id: str,
    edit_prompt: str,
    reference_bytes: bytes,
    aspect_ratio: str = "1:1",
    channel: str = "web",
):
    try:
        provider = get_image_provider()

        from src.endpoints.web import ws_manager
        await ws_manager.send_personal_message(user_id, {
            "type": "image_editing",
            "prompt": edit_prompt[:100],
        })

        result_bytes = await provider.edit(edit_prompt, reference_bytes, aspect_ratio=aspect_ratio)

        loop = asyncio.get_event_loop()

        def _upload():
            file_obj = io.BytesIO(result_bytes)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            return cloudinary.uploader.upload(
                file_obj,
                folder=f"edited_images/{user_id}",
                public_id=f"edit_{ts}",
                overwrite=True,
            )

        upload_result = await loop.run_in_executor(None, _upload)
        image_url = upload_result.get("secure_url")
        print(f"[IMAGE_EDITOR] Imagem editada: {image_url}")

        if channel in ("web", "web_voice", "web_text"):
            await ws_manager.send_personal_message(user_id, {
                "type": "image_edit_ready",
                "image_url": image_url,
                "prompt": edit_prompt[:100],
            })

            try:
                from src.models.chat_messages import save_message
                formatted = f"Pronto! Aqui está a imagem editada:\n{image_url}"
                await asyncio.to_thread(save_message, user_id, user_id, "agent", formatted)
            except Exception as e:
                print(f"[IMAGE_EDITOR] Erro ao persistir mensagem: {e}")

        elif channel in ("whatsapp_text", "whatsapp"):
            try:
                from src.integrations.whatsapp import whatsapp_client
                await whatsapp_client.send_image(user_id, image_url, caption="Aqui está a imagem editada!")
            except Exception as e:
                print(f"[IMAGE_EDITOR] Erro ao enviar imagem via WhatsApp: {e}")

        try:
            from src.models.carousel import create_carousel, update_carousel_status
            from src.events import emit_event_sync
            slide = [{"prompt": edit_prompt[:200], "style": "Edição de imagem", "image_url": image_url}]
            cid = create_carousel(user_id, f"Edição: {edit_prompt[:60]}", slide)
            update_carousel_status(cid, "done", slide)
            emit_event_sync(user_id, "carousel_generated")
        except Exception as e:
            print(f"[IMAGE_EDITOR] Erro ao salvar na galeria: {e}")

        from src.integrations.image_storage import describe_and_store_images
        await describe_and_store_images(user_id, [result_bytes], pre_uploaded_urls=[image_url])

    except Exception as e:
        import traceback
        print(f"[IMAGE_EDITOR] Erro na edição: {e}\n{traceback.format_exc()}")

        try:
            from src.endpoints.web import ws_manager
            await ws_manager.send_personal_message(user_id, {
                "type": "image_edit_ready",
                "image_url": None,
                "error": str(e),
                "prompt": edit_prompt[:100],
            })
        except Exception:
            pass


def create_image_editor_tools(user_id: str, channel: str = "web"):
    from src.tools.image_generation.base import resolve_aspect_ratio

    def edit_image_tool(
        edit_instructions: str,
        image_index: int = 0,
        format: str = "1:1",
    ) -> str:
        """
        Edita ou transforma uma imagem que o usuário acabou de enviar.
        A imagem é processada em background e o resultado é enviado ao usuário.

        Use esta tool quando o usuário enviar uma imagem e pedir para:
        - Modificar algo na imagem (adicionar/remover objetos, mudar fundo, etc.)
        - Transformar o estilo (cartoon, pintura, minimalista, etc.)
        - Gerar uma nova versão baseada na imagem original
        - Ajustar cores, iluminação ou composição

        Args:
            edit_instructions: Instrução detalhada do que fazer com a imagem.
                               Ex: "Adicione um dragão voando ao fundo desta cena"
                               Ex: "Transforme esta foto em estilo aquarela"
                               Ex: "Remova o fundo e substitua por uma praia tropical"
            image_index: Índice da imagem a editar (0 = primeira imagem enviada).
                         Use 0 se o usuário enviou apenas uma imagem.
            format: Formato/dimensão da imagem de saída.
                    Exemplos: "1:1" (quadrado), "4:3" (landscape), "9:16" (stories), "16:9" (widescreen).
                    Se o usuário não especificar, mantenha o padrão "1:1".

        Returns:
            Mensagem de confirmação. A imagem editada será enviada automaticamente.
        """
        images = get_session_images(user_id)
        if not images:
            return (
                "Não encontrei nenhuma imagem na conversa atual. "
                "O usuário precisa enviar uma imagem junto com o pedido de edição."
            )

        if image_index < 0 or image_index >= len(images):
            return f"Índice de imagem inválido. O usuário enviou {len(images)} imagem(ns) (índices 0 a {len(images) - 1})."

        reference_bytes = images[image_index]
        aspect_ratio = resolve_aspect_ratio(format)

        print(f"[IMAGE_EDITOR] edit_image_tool | user={user_id} | channel={channel} | format={format} ({aspect_ratio}) | prompt='{edit_instructions[:60]}'")

        from src.events import _main_loop
        if _main_loop and _main_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                _process_edit_background(user_id, edit_instructions, reference_bytes, aspect_ratio, channel),
                _main_loop
            )
        else:
            print("[IMAGE_EDITOR] AVISO: loop principal não disponível.")
            return "Erro interno: não foi possível iniciar a edição da imagem."

        return (
            f"Edição de imagem iniciada! Estou processando sua solicitação: \"{edit_instructions[:80]}\". "
            "A imagem editada será enviada automaticamente quando ficar pronta — geralmente leva menos de 1 minuto."
        )

    return edit_image_tool

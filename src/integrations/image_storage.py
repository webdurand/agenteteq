import os
import io
import asyncio
from typing import List, Tuple
from datetime import datetime

# Garante que a config do Cloudinary exista
_cloudinary_configured = False

def _ensure_cloudinary_config():
    global _cloudinary_configured
    if not _cloudinary_configured and os.getenv("CLOUDINARY_CLOUD_NAME"):
        import cloudinary
        cloudinary.config(
            cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
            api_key=os.getenv("CLOUDINARY_API_KEY"),
            api_secret=os.getenv("CLOUDINARY_API_SECRET"),
            secure=True
        )
        _cloudinary_configured = True

def upload_user_image(user_id: str, image_bytes: bytes, extension: str = "png") -> str:
    """
    Faz upload de uma imagem do usuário para o Cloudinary.
    Retorna a URL segura da imagem.
    """
    _ensure_cloudinary_config()
    import cloudinary.uploader
    
    file_obj = io.BytesIO(image_bytes)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    
    # Faz upload para a pasta user_uploads/user_id
    upload_result = cloudinary.uploader.upload(
        file_obj,
        folder=f"user_uploads/{user_id}",
        public_id=f"img_{timestamp}",
        overwrite=True,
    )
    
    return upload_result.get("secure_url")

def index_user_image(user_id: str, cloudinary_url: str, description: str):
    """
    Salva a referência da imagem na knowledge base para que o agente possa buscar depois.
    """
    from src.memory.knowledge import get_vector_db
    from agno.knowledge.document import Document
    
    vector_db = get_vector_db()
    if not vector_db:
        print("[IMAGE_STORAGE] Base de conhecimento indisponível para indexar imagem.")
        return
        
    doc = Document(
        content=f"O usuário enviou uma imagem: {description}",
        meta_data={
            "user_id": user_id,
            "type": "user_image",
            "cloudinary_url": cloudinary_url,
            "timestamp": datetime.now().isoformat()
        }
    )
    
    try:
        vector_db.upsert([doc])
        print(f"[IMAGE_STORAGE] Imagem indexada para {user_id}: {cloudinary_url}")
    except Exception as e:
        print(f"[IMAGE_STORAGE] Erro ao indexar imagem: {e}")

async def describe_and_store_images(user_id: str, image_data: List[bytes], agent=None):
    """
    Processo em background que faz upload, gera descrição e indexa as imagens.
    Recebe as imagens como bytes brutos.
    """
    if not image_data:
        return
        
    try:
        # 1. Upload no Cloudinary (em paralelo)
        loop = asyncio.get_event_loop()
        upload_tasks = []
        
        for img_bytes in image_data:
            # Roda o upload síncrono em thread separada
            upload_tasks.append(
                loop.run_in_executor(None, upload_user_image, user_id, img_bytes)
            )
            
        urls = await asyncio.gather(*upload_tasks, return_exceptions=True)
        
        # 2. Descreve as imagens com Gemini
        from agno.agent import Agent
        from agno.models.google import Gemini
        from agno.media import Image
        
        # Cria um agente local apenas para descrever as imagens caso não tenha sido passado
        # (Idealmente reusa o agente da requisição original se passado)
        describer = agent or Agent(
            model=Gemini(id="gemini-2.5-flash"),
            description="Você é um especialista em descrever imagens de forma clara e objetiva para indexação em banco de dados de busca semântica."
        )
        
        for i, img_bytes in enumerate(image_data):
            url = urls[i]
            if isinstance(url, Exception):
                print(f"[IMAGE_STORAGE] Falha no upload da imagem {i}: {url}")
                continue
                
            try:
                # Pede ao Gemini para descrever a imagem detalhadamente
                prompt = "Descreva esta imagem de forma objetiva e detalhada. Foco no que é visível (pessoas, objetos, textos, ambiente). Se for um documento ou print, resuma o conteúdo e o propósito."
                
                # Executa no thread pool para não bloquear o event loop
                response = await asyncio.to_thread(
                    describer.run, 
                    prompt, 
                    images=[Image(content=img_bytes)]
                )
                
                description = response.content if hasattr(response, 'content') else str(response)
                
                # 3. Indexa na base
                await asyncio.to_thread(index_user_image, user_id, url, description)
                
            except Exception as desc_err:
                print(f"[IMAGE_STORAGE] Erro ao descrever imagem {url}: {desc_err}")
                # Indexa sem descrição avançada como fallback
                await asyncio.to_thread(index_user_image, user_id, url, "Imagem sem descrição detalhada (falha no processamento visual).")
                
    except Exception as e:
        import traceback
        print(f"[IMAGE_STORAGE] Erro no pipeline de armazenamento: {e}\n{traceback.format_exc()}")

import os
import io
import asyncio
from typing import List, Tuple
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Garante que a config do Cloudinary exista
_cloudinary_configured = False

MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB
VALID_IMAGE_HEADERS = {
    b'\xff\xd8\xff': 'jpeg',
    b'\x89PNG': 'png',
    b'GIF8': 'gif',
    b'RIFF': 'webp',
}

def _validate_image(image_bytes: bytes):
    if len(image_bytes) > MAX_IMAGE_SIZE:
        raise ValueError(f"Image too large: {len(image_bytes)} bytes (max {MAX_IMAGE_SIZE})")
    for magic, fmt in VALID_IMAGE_HEADERS.items():
        if image_bytes[:len(magic)] == magic:
            return fmt
    raise ValueError("Invalid image format: not a recognized image type")

def convert_to_webp(image_bytes: bytes, quality: int = 85) -> bytes:
    """
    Converte imagem (PNG/JPEG) para WebP com qualidade configurável.
    Reduz ~60-80% o tamanho sem diferença visual perceptível.
    Retorna os bytes originais se a conversão falhar.
    """
    try:
        from PIL import Image as PILImage
        img = PILImage.open(io.BytesIO(image_bytes))
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=quality)
        webp_bytes = buf.getvalue()
        logger.info("WebP: %s → %s bytes (%s%% menor)", len(image_bytes), len(webp_bytes), 100 - len(webp_bytes) * 100 // len(image_bytes))
        return webp_bytes
    except Exception as e:
        logger.error("Falha na conversão WebP, usando original: %s", e)
        return image_bytes

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
    _validate_image(image_bytes)
    _ensure_cloudinary_config()
    import cloudinary.uploader
    
    image_bytes = convert_to_webp(image_bytes)
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
        logger.info("Base de conhecimento indisponível para indexar imagem.")
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
        content_hash = str(hash(f"{user_id}:{cloudinary_url}"))
        vector_db.upsert(content_hash=content_hash, documents=[doc])
        logger.info("Imagem indexada para %s: %s", user_id, cloudinary_url)
    except Exception as e:
        err = str(e).lower()
        if "unique" in err or "duplicate" in err:
            logger.info("Imagem já indexada (duplicada): %s", cloudinary_url)
        else:
            logger.error("Erro ao indexar imagem: %s", e)

async def describe_and_store_images(user_id: str, image_data: List[bytes], agent=None, pre_uploaded_urls: List[str] = None):
    """
    Processo em background que faz upload (se necessário), gera descrição e indexa as imagens.
    Recebe as imagens como bytes brutos.
    """
    if not image_data:
        return
        
    try:
        loop = asyncio.get_event_loop()
        urls = []
        if pre_uploaded_urls and len(pre_uploaded_urls) == len(image_data):
            urls = pre_uploaded_urls
        else:
            # 1. Upload no Cloudinary (em paralelo)
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
                logger.error("Falha no upload da imagem %s: %s", i, url)
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
                logger.error("Erro ao descrever imagem %s: %s", url, desc_err)
                # Indexa sem descrição avançada como fallback
                await asyncio.to_thread(index_user_image, user_id, url, "Imagem sem descrição detalhada (falha no processamento visual).")
                
    except Exception as e:
        import traceback

        logger.error("Erro no pipeline de armazenamento: %s\n%s", e, traceback.format_exc())

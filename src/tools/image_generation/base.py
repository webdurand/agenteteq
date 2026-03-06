from abc import ABC, abstractmethod

# Formatos suportados mapeados para aspect ratios válidos da API do Gemini.
# Valores aceitos: "1:1", "3:4", "4:3", "9:16", "16:9"
FORMAT_TO_ASPECT_RATIO: dict[str, str] = {
    # Carrossel Instagram landscape (1350x1080)
    "1350x1080": "4:3",
    "1350 x 1080": "4:3",
    # Quadrado (1080x1080)
    "1080x1080": "1:1",
    "1080 x 1080": "1:1",
    "quadrado": "1:1",
    "square": "1:1",
    # Portrait / feed vertical (1080x1350)
    "1080x1350": "3:4",
    "1080 x 1350": "3:4",
    "portrait": "3:4",
    "vertical": "3:4",
    # Stories / Reels (1080x1920)
    "1080x1920": "9:16",
    "1080 x 1920": "9:16",
    "stories": "9:16",
    "reels": "9:16",
    "story": "9:16",
    # Widescreen / YouTube
    "16:9": "16:9",
    "youtube": "16:9",
    "horizontal": "16:9",
    # Aceita aspect ratio direto
    "4:3": "4:3",
    "3:4": "3:4",
    "1:1": "1:1",
    "9:16": "9:16",
}

def resolve_aspect_ratio(fmt: str) -> str:
    """
    Converte um formato legível (ex: '1350x1080', 'stories', '9:16')
    para o aspect ratio aceito pela API do Gemini.
    Retorna '4:3' como padrão para carrosséis Instagram.
    """
    normalized = fmt.strip().lower().replace(" ", "")
    return FORMAT_TO_ASPECT_RATIO.get(normalized, FORMAT_TO_ASPECT_RATIO.get(fmt.strip().lower(), "4:3"))


class ImageProvider(ABC):
    """
    Interface base para provedores de geração de imagem.
    """

    @abstractmethod
    async def generate(self, prompt: str, aspect_ratio: str = "4:3") -> bytes:
        """
        Gera uma imagem a partir de um prompt e retorna os bytes da imagem.
        aspect_ratio deve ser um dos valores suportados: "1:1", "3:4", "4:3", "9:16", "16:9"
        """
        pass

from abc import ABC, abstractmethod
from typing import Optional

class ImageProvider(ABC):
    """
    Interface base para provedores de geração de imagem.
    """

    @abstractmethod
    async def generate(self, prompt: str, aspect_ratio: str = "1:1") -> bytes:
        """
        Gera uma imagem a partir de um prompt e retorna os bytes da imagem.
        """
        pass

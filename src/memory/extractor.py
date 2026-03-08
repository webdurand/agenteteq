from agno.agent import Agent
from agno.models.google import Gemini

from src.memory.knowledge import get_vector_db
from src.tools.memory_manager import add_memory
import logging

logger = logging.getLogger(__name__)

def extract_and_save_facts(user_id: str, message: str, response: str) -> None:
    """
    Analisa a interação recente e extrai fatos ou preferências sobre o usuário.
    Se encontrar algo relevante, salva na base de dados.
    """
    vector_db = get_vector_db()
    if not vector_db:
        return
        
    prompt = f"""
Analise a seguinte interação entre um usuário (o autor da mensagem) e um assistente de IA.
Sua tarefa é extrair fatos permanentes ou preferências explícitas **SOBRE O USUÁRIO**, baseando-se **apenas no que o usuário disse** (a resposta do assistente serve apenas de contexto).
NÃO extraia fatos sobre o que o assistente é capaz de fazer, nem assuma que o usuário é dono de sistemas que o assistente menciona, a menos que o usuário tenha dito isso explicitamente.
Exemplos de fatos a extrair: "O usuário gosta de títulos curtos", "O usuário tem uma empresa chamada XYZ", "O usuário não gosta de emojis".
NÃO extraia fatos temporários ou irrelevantes para o futuro.
Se houver algum fato a ser memorizado, retorne APENAS a string com o fato.
Se houver mais de um fato, retorne-os separados por ponto e vírgula (;).
Se NÃO houver nenhum fato relevante a ser extraído, retorne exatamente a string: VAZIO

Interação:
Usuário: {message}
Assistente: {response}
"""

    extractor_agent = Agent(
        model=Gemini(id="gemini-2.5-flash"),
        description="Você é um extrator de memórias. Siga as instruções estritamente.",
    )
    
    result = extractor_agent.run(prompt)
    if not result or not result.content:
        return
        
    content = result.content.strip()
    
    if content.upper() == "VAZIO" or "VAZIO" in content.upper():
        return
        
    fatos = [f.strip() for f in content.split(";") if f.strip()]
    for fato in fatos:
        logger.info("Fato extraído em background: %s", fato)
        add_memory(fato, user_id)

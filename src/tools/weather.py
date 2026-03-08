import logging

logger = logging.getLogger(__name__)


def get_weather(city: str) -> str:
    """
    Retorna a previsao do tempo atual para a cidade informada usando busca na web.

    Args:
        city: Nome da cidade (ex: 'Rio de Janeiro', 'Sao Paulo', 'Curitiba').

    Returns:
        Resultado da busca com temperatura, condicao do tempo e previsao.
    """
    from src.tools.web_search import web_search_raw

    query = f"previsão do tempo {city} agora temperatura hoje"
    logger.info("Buscando via web: %s", query)
    result = web_search_raw(query)
    logger.info("Resultado obtido (%s chars)", len(result))
    return result

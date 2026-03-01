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
    print(f"[WEATHER] Buscando via web: {query}")
    result = web_search_raw(query)
    print(f"[WEATHER] Resultado obtido ({len(result)} chars)")
    return result

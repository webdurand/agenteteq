import os
from typing import Optional

from src.integrations.status_notifier import StatusNotifier


# ---------------------------------------------------------------------------
# Factories de provider (ponto único de troca via .env)
# Seguem o mesmo padrão de LLM_PROVIDER e WHATSAPP_PROVIDER do projeto.
# ---------------------------------------------------------------------------

def get_search_toolkit():
    """
    Retorna a toolkit de busca web conforme SEARCH_PROVIDER.
    Padrão: duckduckgo (gratuito, sem API key).
    Opções: duckduckgo, tavily, exa, serper, brave
    """
    provider = os.getenv("SEARCH_PROVIDER", "duckduckgo").lower()

    if provider == "tavily":
        from agno.tools.tavily import TavilyTools
        return TavilyTools(api_key=os.getenv("TAVILY_API_KEY"))
    elif provider == "exa":
        from agno.tools.exa import ExaTools
        return ExaTools(api_key=os.getenv("EXA_API_KEY"))
    elif provider == "serper":
        from agno.tools.serpapi import SerpApiTools
        return SerpApiTools(api_key=os.getenv("SERPER_API_KEY"))
    elif provider == "brave":
        from agno.tools.brave_search import BraveSearch
        return BraveSearch(api_key=os.getenv("BRAVE_API_KEY"))
    else:
        from agno.tools.duckduckgo import DuckDuckGoTools
        return DuckDuckGoTools()


def get_scraper_toolkit():
    """
    Retorna a toolkit de scraping conforme SCRAPER_PROVIDER.
    Padrão: newspaper4k (gratuito, sem API key).
    Opções: newspaper4k, crawl4ai
    """
    provider = os.getenv("SCRAPER_PROVIDER", "newspaper4k").lower()

    if provider == "crawl4ai":
        from agno.tools.crawl4ai import Crawl4aiTools
        return Crawl4aiTools(max_length=5000)
    else:
        from agno.tools.newspaper4k import Newspaper4kTools
        return Newspaper4kTools()


# ---------------------------------------------------------------------------
# Camada interna: funções puras, sem notificação.
# Usadas internamente pelo deep_research e sub-agentes do Team.
# ---------------------------------------------------------------------------

def web_search_raw(query: str, max_results: int = 5) -> str:
    """Busca web via provider configurado (sem notificação ao usuário)."""
    try:
        toolkit = get_search_toolkit()
        return toolkit.duckduckgo_search(query=query, max_results=max_results)
    except Exception as e:
        return f"Erro na busca: {e}"


def fetch_page_raw(url: str) -> str:
    """Extrai conteúdo de uma URL via provider configurado (sem notificação ao usuário)."""
    try:
        toolkit = get_scraper_toolkit()
        return toolkit.read_article(url=url)
    except Exception as e:
        return f"Erro ao buscar página: {e}"


# ---------------------------------------------------------------------------
# Camada externa: factories com notificação embutida, para o agente principal.
# O flag _already_notified evita spam quando o agente faz múltiplas buscas.
# ---------------------------------------------------------------------------

def create_web_search_tool(notifier: StatusNotifier):
    """
    Cria a tool web_search com StatusNotifier injetado.
    Notifica o usuário na primeira busca da conversa.
    """
    already_notified = [False]

    def web_search(query: str, max_results: int = 5) -> str:
        """
        Pesquisa rápida na internet sobre um assunto.
        Use para buscar informações atualizadas, notícias, fatos ou
        qualquer coisa que precise de dados recentes da web.
        """
        if not already_notified[0]:
            notifier.notify("Beleza, vou dar uma olhada e já te respondo!")
            already_notified[0] = True
        return web_search_raw(query, max_results)

    return web_search


def create_fetch_page_tool(notifier: StatusNotifier):
    """
    Cria a tool fetch_page com StatusNotifier injetado.
    Notifica o usuário na primeira leitura de página da conversa.
    """
    already_notified = [False]

    def fetch_page(url: str) -> str:
        """
        Lê e extrai o conteúdo completo de uma página web a partir de uma URL.
        Use quando precisar detalhar o conteúdo de um link específico encontrado
        em uma busca ou fornecido pelo usuário.
        """
        if not already_notified[0]:
            notifier.notify("Beleza, vou dar uma olhada e já te respondo!")
            already_notified[0] = True
        return fetch_page_raw(url)

    return fetch_page

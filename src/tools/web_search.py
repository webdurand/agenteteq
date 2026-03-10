import os
from typing import Optional
from urllib.parse import urlparse

from src.integrations.status_notifier import StatusNotifier
from src.config.feature_gates import check_daily_feature_limit, log_feature_usage

_BLOCKED_DOMAINS = ("instagram.com", "facebook.com", "linkedin.com", "tiktok.com", "x.com", "twitter.com")


def _is_blocked_url(url: str) -> bool:
    try:
        domain = urlparse(url).netloc.lower()
        return any(blocked in domain for blocked in _BLOCKED_DOMAINS)
    except Exception:
        return False


class JinaReaderToolkit:
    """Wrapper para a API do Jina Reader (r.jina.ai) para usar a mesma interface do newspaper4k."""
    def read_article(self, url: str) -> str:
        import httpx
        try:
            # timeout aumentado porque paginas pesadas podem demorar a renderizar no backend deles
            response = httpx.get(f"https://r.jina.ai/{url}", timeout=30.0)
            response.raise_for_status()
            return response.text
        except Exception as e:
            return f"Erro ao acessar {url} com Jina Reader: {str(e)}"


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
    Padrão: jina (gratuito, sem API key).
    Opções: jina, newspaper4k, crawl4ai
    """
    provider = os.getenv("SCRAPER_PROVIDER", "jina").lower()

    if provider == "crawl4ai":
        from agno.tools.crawl4ai import Crawl4aiTools
        return Crawl4aiTools(max_length=5000)
    elif provider == "newspaper4k":
        from agno.tools.newspaper4k import Newspaper4kTools
        return Newspaper4kTools()
    else:
        return JinaReaderToolkit()


# ---------------------------------------------------------------------------
# Camada interna: funções puras, sem notificação.
# Usadas internamente pelo deep_research e sub-agentes do Team.
# ---------------------------------------------------------------------------

def _tavily_search(query: str, topic: str = "general", max_results: int = 5, days: int | None = None) -> str:
    """
    Busca via Tavily SDK direto (bypass do wrapper Agno).
    Suporta topic='general' ou 'news', search_depth='advanced', e days para news.
    Retorna resultados formatados com título, snippet, fonte e URL.
    """
    from tavily import TavilyClient

    client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
    kwargs = {
        "query": query,
        "topic": topic,
        "max_results": max_results,
        "search_depth": "advanced",
        "include_answer": True,
    }
    if topic == "news" and days:
        kwargs["days"] = days

    response = client.search(**kwargs)

    parts: list[str] = []
    answer = response.get("answer")
    if answer:
        parts.append(f"Resumo: {answer}\n")

    for r in response.get("results", []):
        title = r.get("title", "Sem título")
        snippet = r.get("content", "")
        url = r.get("url", "")
        published = r.get("published_date", "")
        source = urlparse(url).netloc if url else ""
        date_str = f" ({published})" if published else ""
        parts.append(f"**{title}** — {source}{date_str}\n{snippet}\n{url}")

    return "\n\n".join(parts) if parts else "Nenhum resultado encontrado."


def web_search_raw(query: str, max_results: int = 5, topic: str = "general", days: int = 3) -> str:
    """Busca web via provider configurado (sem notificação ao usuário)."""
    try:
        provider = os.getenv("SEARCH_PROVIDER", "duckduckgo").lower()
        if provider == "tavily":
            return _tavily_search(
                query, topic=topic, max_results=max_results,
                days=days if topic == "news" else None,
            )
        else:
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

def create_web_search_tool(notifier: StatusNotifier, user_id: str = None):
    """
    Cria a tool web_search com StatusNotifier injetado.
    A deduplicação de mensagens é gerenciada pelo próprio StatusNotifier,
    então a mesma mensagem nunca é enviada duas vezes na mesma conversa,
    independente de quantas tools a usem.
    """

    def web_search(query: str, max_results: int = 5, topic: str = "general", days: int = 3) -> str:
        """
        Pesquisa na internet sobre qualquer assunto.
        Use topic='general' (padrao) para buscas gerais.
        Use topic='news' para noticias recentes dos ultimos N dias (ajuste 'days', padrao 3).
        Retorna resultados com titulo, snippet, fonte e link.
        Apos resultados relevantes, salve os achados com add_memory para referencia futura.
        """
        if user_id:
            limit_msg = check_daily_feature_limit(user_id, "max_searches_daily")
            if limit_msg:
                return limit_msg
        if notifier:
            notifier.notify("Beleza, vou dar uma olhada e já te respondo!")
        result = web_search_raw(query, max_results, topic=topic, days=days)
        if user_id:
            log_feature_usage(user_id, "max_searches_daily")
        return result

    return web_search


def create_fetch_page_tool(notifier: StatusNotifier):
    """
    Cria a tool fetch_page com StatusNotifier injetado.
    """

    def fetch_page(url: str) -> str:
        """
        Lê e extrai o conteúdo completo de uma página web a partir de uma URL.
        Use quando precisar detalhar o conteúdo de um link específico encontrado
        em uma busca ou fornecido pelo usuário. Funciona para qualquer site,
        não apenas notícias.
        """
        if _is_blocked_url(url):
            return (
                f"Nao consegui acessar {url} — redes sociais bloqueiam acesso de robos. "
                "Futuramente vou ter integracao direta com essas plataformas."
            )
        if notifier:
            notifier.notify("Beleza, vou dar uma olhada nesse link e já te respondo!")
        return fetch_page_raw(url)

    return fetch_page


def create_explore_site_tool(notifier: StatusNotifier):
    """
    Cria a tool explore_site com StatusNotifier injetado.
    """

    def explore_site(url: str) -> str:
        """
        Explora um site extraindo seus links internos e seções para permitir navegação.
        Use esta ferramenta quando o usuário pedir para você "navegar pelo site",
        "ver o que tem no site", ou "procurar uma sessão específica" dentro de uma URL.
        Ela retornará uma lista dos links encontrados na página para que você possa
        então chamar `fetch_page` nos links que parecem mais relevantes.
        """
        if _is_blocked_url(url):
            return (
                f"Nao consegui acessar {url} — redes sociais bloqueiam acesso de robos. "
                "Futuramente vou ter integracao direta com essas plataformas."
            )
        if notifier:
            notifier.notify("Explorando as seções desse site, só um momento...")
            
        import re
        
        # Pega o conteúdo da página em Markdown
        content = fetch_page_raw(url)
        
        # Expressão regular simples para pegar links em Markdown: [texto](url)
        # O Jina Reader já retorna links organizados assim
        markdown_link_pattern = re.compile(r'\[([^\]]+)\]\((https?://[^\)]+)\)')
        
        links_found = markdown_link_pattern.findall(content)
        
        if not links_found:
            return (
                "Nenhum link claro foi encontrado nesta página. "
                "Pode ser um site que requer JavaScript complexo, uma rede social "
                "com bloqueio, ou apenas não possui links de navegação visíveis.\n\n"
                "Aqui está um trecho do conteúdo que consegui ler:\n"
                f"{content[:1000]}..."
            )
            
        # Deduplicar e formatar os links para o LLM
        unique_links = {}
        for text, link in links_found:
            text = text.strip()
            # Ignora links de imagem ou textos vazios
            if text and not text.startswith("!") and len(text) > 2:
                if link not in unique_links:
                    unique_links[link] = text
                    
        if not unique_links:
            return "Nenhum link de navegação útil foi encontrado."
            
        # Montar um sumário amigável pro LLM
        summary = [f"### Links e seções encontradas em {url}:\n"]
        for link, text in list(unique_links.items())[:50]:  # Limita aos 50 primeiros pra não explodir token
            summary.append(f"- **{text}**: `{link}`")
            
        summary.append("\n**Instrução para você (Teq)**: Escolha os links que parecem "
                       "responder à dúvida do usuário e use a tool `fetch_page` nesses links "
                       "para ler o conteúdo deles e aprofundar a pesquisa.")
        
        return "\n".join(summary)

    return explore_site

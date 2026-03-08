import os
from typing import Callable

from agno.agent import Agent
from agno.team.mode import TeamMode

from src.integrations.status_notifier import StatusNotifier
from src.agent.multi_agent import run_team, get_default_model
from src.tools.web_search import (
    web_search_raw,
    fetch_page_raw,
    get_search_toolkit,
    get_scraper_toolkit,
)
from src.config.feature_gates import check_daily_feature_limit, log_feature_usage


def _get_light_model():
    """Modelo leve e rápido para o agente decisor interno."""
    from agno.models.google import Gemini
    return Gemini(id="gemini-2.5-flash")


def _decide_if_needs_deep_research(topic: str, initial_results: str) -> tuple[bool, list[str]]:
    """
    Agente leve analisa os resultados iniciais e decide se a pesquisa precisa
    de aprofundamento. Se sim, retorna também a lista de sub-tópicos.
    
    Returns:
        (needs_deep, subtopics): True + lista de sub-tópicos, ou False + [].
    """
    from agno.agent import Agent

    decision_agent = Agent(
        model=_get_light_model(),
        description="Você analisa resultados de busca e decide se precisam de aprofundamento.",
    )

    prompt = f"""Analise os resultados de busca abaixo sobre o tema: "{topic}"

RESULTADOS INICIAIS:
{initial_results[:3000]}

Responda em formato estruturado:
1. Linha 1: SUFICIENTE ou APROFUNDAR
2. Se APROFUNDAR: liste os sub-tópicos (máximo 3), um por linha, prefixados com "- "

Exemplo de resposta para aprofundamento:
APROFUNDAR
- Como funciona o mecanismo X
- Comparação entre Y e Z
- Impacto prático no contexto W

Exemplo de resposta suficiente:
SUFICIENTE
"""

    result = decision_agent.run(prompt)
    if not result or not result.content:
        return False, []

    lines = [l.strip() for l in result.content.strip().splitlines() if l.strip()]
    if not lines:
        return False, []

    needs_deep = lines[0].upper().startswith("APROFUNDAR")
    subtopics = [l.lstrip("- ").strip() for l in lines[1:] if l.startswith("-")]

    return needs_deep, subtopics


def _run_deep_team(topic: str, subtopics: list[str]) -> str:
    """
    Cria um Team Agno no modo broadcast com um agente por sub-tópico.
    Cada agente pesquisa seu sub-tópico com as tools configuradas no .env.
    O leader sintetiza os resultados em um relatório coeso.
    """
    search_toolkit = get_search_toolkit()
    scraper_toolkit = get_scraper_toolkit()

    members = []
    for i, subtopic in enumerate(subtopics):
        members.append(
            Agent(
                id=f"researcher_{i}",
                name=f"Pesquisador {i + 1}",
                role=f"Pesquisar em profundidade sobre: {subtopic}",
                model=_get_light_model(),
                tools=[search_toolkit, scraper_toolkit],
                instructions=[
                    f"Pesquise o sub-tópico: {subtopic}",
                    "Faça buscas na web e leia os artigos mais relevantes.",
                    "Retorne um resumo claro e detalhado das informações encontradas.",
                    "Cite as fontes encontradas quando relevante.",
                ],
            )
        )

    if not members:
        return web_search_raw(topic, max_results=8)

    return run_team(
        members=members,
        task=f"Pesquise em profundidade sobre: {topic}. Cada membro deve cobrir seu sub-tópico e o leader deve sintetizar tudo em um relatório completo.",
        mode=TeamMode.coordinate,
        model=get_default_model(),
        instructions=[
            "Sintetize os resultados dos pesquisadores em um relatório coeso e bem estruturado.",
            "Use markdown para organizar (títulos, listas, destaques).",
            "Inclua as fontes mais relevantes ao final.",
        ],
        name="Deep Research Team",
    )


def _save_research_to_memory(topic: str, summary: str, user_id: str) -> None:
    """Salva um trecho da pesquisa na memória do usuário para referência futura."""
    try:
        from src.tools.memory_manager import add_memory

        short_summary = summary[:400].strip()
        if short_summary:
            add_memory(
                fato=f"Pesquisa sobre '{topic}': {short_summary}...",
                user_id=user_id,
            )
    except Exception as e:
        print(f"[DEEP_RESEARCH] Erro ao salvar pesquisa na memória: {e}")


# ---------------------------------------------------------------------------
# Factory pública
# ---------------------------------------------------------------------------

def create_deep_research_tool(notifier: StatusNotifier, user_id: str) -> Callable:
    """
    Cria a tool deep_research com StatusNotifier e user_id injetados.
    
    O deep_research é uma composição dos módulos:
    - StatusNotifier: feedback determinístico ao usuário
    - web_search_raw: busca inicial via provider configurado
    - run_team: orquestração multi-agent via Agno Team
    - add_memory: persistência de trechos na memória do usuário
    """

    def deep_research(topic: str) -> str:
        """
        Pesquisa aprofundada e detalhada sobre um tema na internet.
        
        Use esta tool quando:
        - O usuário pedir uma pesquisa, investigação ou análise aprofundada
        - O tema exigir múltiplas fontes e perspectivas
        - Precisar de informações detalhadas e atualizadas sobre um assunto complexo
        
        Para buscas simples e rápidas, prefira a tool web_search.
        """
        print(f"[DEEP_RESEARCH] Iniciando pesquisa sobre: {topic}")
        limit_msg = check_daily_feature_limit(user_id, "max_deep_research_daily")
        if limit_msg:
            return limit_msg
        if notifier:
            notifier.notify("Beleza, vou dar uma olhada e já te respondo!")

        # Busca inicial para avaliar o escopo
        initial_results = web_search_raw(topic, max_results=5)
        print(f"[DEEP_RESEARCH] Resultados iniciais obtidos. Analisando necessidade de aprofundamento...")

        needs_deep, subtopics = _decide_if_needs_deep_research(topic, initial_results)

        if needs_deep and subtopics:
            print(f"[DEEP_RESEARCH] Aprofundamento necessário. Sub-tópicos: {subtopics}")
            if notifier:
                notifier.notify("Vou detalhar mais pra te dar uma resposta mais precisa!")
            final_content = _run_deep_team(topic, subtopics)
        else:
            print(f"[DEEP_RESEARCH] Resultados iniciais suficientes.")
            final_content = initial_results

        _save_research_to_memory(topic, final_content, user_id)
        log_feature_usage(user_id, "max_deep_research_daily")

        print(f"[DEEP_RESEARCH] Pesquisa concluída.")
        return final_content

    return deep_research

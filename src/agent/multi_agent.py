import os
from typing import Optional

from agno.agent import Agent
from agno.team import Team
from agno.team.mode import TeamMode


def get_default_model():
    """Reutiliza o mesmo padrão de get_model() do assistant.py."""
    provider = os.getenv("LLM_PROVIDER", "openai").lower()

    if provider == "anthropic":
        from agno.models.anthropic import Claude
        return Claude(id=os.getenv("LLM_MODEL", "claude-3-5-sonnet-20241022"))
    elif provider == "gemini":
        from agno.models.google import Gemini
        return Gemini(id=os.getenv("LLM_MODEL", "gemini-2.5-flash"))
    else:
        from agno.models.openai import OpenAIChat
        return OpenAIChat(id=os.getenv("LLM_MODEL", "gpt-4o-mini"))


def run_team(
    members: list[Agent],
    task: str,
    mode: TeamMode = TeamMode.coordinate,
    model=None,
    instructions: Optional[list[str]] = None,
    name: str = "Dynamic Team",
) -> str:
    """
    Executa um Team Agno com os membros e modo fornecidos.
    
    Wrapper genérico e reutilizável — agnóstico ao caso de uso.
    Pode ser usado para pesquisa, análise comparativa, resumo de múltiplos
    documentos, ou qualquer tarefa que se beneficie de execução paralela.

    Modos disponíveis:
    - TeamMode.coordinate (padrão): leader seleciona membros, formula tarefas e sintetiza
    - TeamMode.broadcast: todos os membros recebem a mesma tarefa em paralelo
    - TeamMode.route: leader roteia para um único membro
    - TeamMode.tasks: leader decompõe em lista de tarefas e executa iterativamente
    
    Args:
        members: Lista de Agent Agno, cada um com name, role e tools definidos.
        task: Tarefa ou pergunta a ser executada pelo time.
        mode: Modo de coordenação do Team (padrão: coordinate).
        model: Modelo para o leader do Team (padrão: mesmo do assistente principal).
        instructions: Instruções adicionais para o leader.
        name: Nome identificador do Team (útil para logs).
    
    Returns:
        Conteúdo da resposta final sintetizada pelo leader.
    """
    team = Team(
        name=name,
        model=model or get_default_model(),
        members=members,
        mode=mode,
        instructions=instructions or [],
    )
    result = team.run(task)
    return result.content if result and result.content else ""

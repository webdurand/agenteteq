"""
Templates de formato para videos virais.
Cada template define framework de copywriting e orientacoes de estilo.
A quantidade de cenas e duracao sao flexiveis — definidas pelo conteudo e pedido do usuario.
"""

TEMPLATES = {
    "tutorial": {
        "name": "Tutorial / Dica Pratica",
        "description": "Ensina algo passo a passo. Ideal para conteudo educacional.",
        "framework": "PAS",
        "guidance": (
            "Estrutura sugerida: problema → agitacao → solucao (passos). "
            "Adapte o numero de passos ao conteudo. Pode ter 1 passo ou 5."
        ),
    },
    "storytelling": {
        "name": "Storytelling / Historia",
        "description": "Conta uma historia com arco emocional. Melhor para engajamento profundo.",
        "framework": "BAB",
        "guidance": (
            "Estrutura sugerida: antes → luta/conflito → ponto de virada → depois. "
            "O arco emocional importa mais que o numero de cenas."
        ),
    },
    "listicle": {
        "name": "Listicle / Lista de Dicas",
        "description": "Lista numerada. Formato rapido e compartilhavel.",
        "framework": "AIDA",
        "guidance": (
            "Estrutura sugerida: contexto → itens numerados → melhor item → CTA. "
            "O numero de itens depende da duracao — pode ser 2, 3 ou 7."
        ),
    },
    "transformation": {
        "name": "Transformacao / Antes e Depois",
        "description": "Mostra resultado real. Ideal para prova social e cases.",
        "framework": "BAB",
        "guidance": (
            "Estrutura sugerida: resultado primeiro (hook) → estado antes → metodo → estado depois. "
            "Foque no contraste visual e emocional."
        ),
    },
    "qa": {
        "name": "Pergunta e Resposta",
        "description": "Responde uma duvida comum do publico. Otimo para alcance.",
        "framework": "PAS",
        "guidance": (
            "Estrutura sugerida: pergunta forte → resposta errada comum → resposta certa → prova. "
            "Pode ser bem curto (15-20s) ou mais longo dependendo da complexidade."
        ),
    },
    "behind_the_scenes": {
        "name": "Bastidores / Behind the Scenes",
        "description": "Mostra o processo por tras. Cria conexao e autenticidade.",
        "framework": "STAR",
        "guidance": (
            "Estrutura sugerida: teaser → situacao → tarefa → acao → resultado. "
            "Tom casual e autentico."
        ),
    },
}

# ~2.3 palavras por segundo e uma boa media para fala natural em PT-BR
WORDS_PER_SECOND = 2.3


def estimate_target_words(duration_s: int) -> int:
    """Estima palavras alvo baseado na duracao pedida."""
    return max(15, int(duration_s * WORDS_PER_SECOND))


def get_template(style: str) -> dict:
    """Retorna template pelo nome. Fallback para tutorial."""
    return TEMPLATES.get(style, TEMPLATES["tutorial"])


def list_templates() -> list[dict]:
    """Retorna lista resumida dos templates disponiveis."""
    return [
        {
            "id": key,
            "name": tmpl["name"],
            "description": tmpl["description"],
            "framework": tmpl["framework"],
        }
        for key, tmpl in TEMPLATES.items()
    ]

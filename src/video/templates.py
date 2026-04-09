"""
Templates de formato para videos virais.
Cada template define a estrutura, framework de copywriting, e parametros de edicao.
"""

TEMPLATES = {
    "tutorial": {
        "name": "Tutorial / Dica Pratica",
        "description": "Ensina algo passo a passo. Ideal para conteudo educacional.",
        "framework": "PAS",
        "structure": {
            "hook_type": "bold_statement",
            "hook_duration_s": 3,
            "sections": [
                {"name": "hook", "duration_s": 3, "movement": "zoom_in_face"},
                {"name": "problem", "duration_s": 8, "movement": "ken_burns"},
                {"name": "agitation", "duration_s": 7, "movement": "zoom_pulse"},
                {"name": "step_1", "duration_s": 10, "movement": "zoom_in_face"},
                {"name": "step_2", "duration_s": 10, "movement": "ken_burns"},
                {"name": "step_3", "duration_s": 10, "movement": "zoom_in_face"},
                {"name": "result", "duration_s": 7, "movement": "zoom_out"},
                {"name": "callback", "duration_s": 5, "movement": "zoom_in_face"},
            ],
        },
        "caption_style": "tiktok_bounce_highlight",
        "music_style": "upbeat_corporate",
        "target_duration_s": 60,
        "target_words": 140,
    },
    "storytelling": {
        "name": "Storytelling / Historia",
        "description": "Conta uma historia com arco emocional. Melhor para engajamento profundo.",
        "framework": "BAB",
        "structure": {
            "hook_type": "proof_first",
            "hook_duration_s": 3,
            "sections": [
                {"name": "hook", "duration_s": 3, "movement": "zoom_in_face"},
                {"name": "before", "duration_s": 12, "movement": "ken_burns"},
                {"name": "struggle", "duration_s": 10, "movement": "zoom_pulse"},
                {"name": "turning_point", "duration_s": 8, "movement": "zoom_in_face"},
                {"name": "after", "duration_s": 15, "movement": "ken_burns"},
                {"name": "bridge", "duration_s": 7, "movement": "zoom_in_face"},
                {"name": "callback", "duration_s": 5, "movement": "zoom_out"},
            ],
        },
        "caption_style": "tiktok_bounce_highlight",
        "music_style": "emotional_cinematic",
        "target_duration_s": 60,
        "target_words": 135,
    },
    "listicle": {
        "name": "Listicle / Lista de Dicas",
        "description": "Lista numerada (3-5 itens). Formato rapido e compartilhavel.",
        "framework": "AIDA",
        "structure": {
            "hook_type": "question",
            "hook_duration_s": 3,
            "sections": [
                {"name": "hook", "duration_s": 3, "movement": "zoom_in_face"},
                {"name": "context", "duration_s": 5, "movement": "ken_burns"},
                {"name": "item_1", "duration_s": 10, "movement": "zoom_in_face"},
                {"name": "item_2", "duration_s": 10, "movement": "zoom_pulse"},
                {"name": "item_3", "duration_s": 10, "movement": "zoom_in_face"},
                {"name": "best_item", "duration_s": 12, "movement": "ken_burns"},
                {"name": "cta", "duration_s": 5, "movement": "zoom_in_face"},
                {"name": "callback", "duration_s": 5, "movement": "zoom_out"},
            ],
        },
        "caption_style": "tiktok_bounce_highlight",
        "music_style": "upbeat_energetic",
        "target_duration_s": 60,
        "target_words": 145,
    },
    "transformation": {
        "name": "Transformacao / Antes e Depois",
        "description": "Mostra resultado real. Ideal para prova social e cases.",
        "framework": "BAB",
        "structure": {
            "hook_type": "proof_first",
            "hook_duration_s": 3,
            "sections": [
                {"name": "hook_result", "duration_s": 3, "movement": "zoom_in_face"},
                {"name": "before_state", "duration_s": 12, "movement": "ken_burns"},
                {"name": "the_method", "duration_s": 20, "movement": "zoom_pulse"},
                {"name": "after_state", "duration_s": 12, "movement": "ken_burns"},
                {"name": "bridge", "duration_s": 8, "movement": "zoom_in_face"},
                {"name": "callback", "duration_s": 5, "movement": "zoom_out"},
            ],
        },
        "caption_style": "tiktok_bounce_highlight",
        "music_style": "inspirational",
        "target_duration_s": 60,
        "target_words": 130,
    },
    "qa": {
        "name": "Pergunta e Resposta",
        "description": "Responde uma duvida comum do publico. Otimo para alcance.",
        "framework": "PAS",
        "structure": {
            "hook_type": "question",
            "hook_duration_s": 3,
            "sections": [
                {"name": "hook_question", "duration_s": 3, "movement": "zoom_in_face"},
                {"name": "why_people_ask", "duration_s": 8, "movement": "ken_burns"},
                {"name": "wrong_answer", "duration_s": 10, "movement": "zoom_pulse"},
                {"name": "right_answer", "duration_s": 20, "movement": "zoom_in_face"},
                {"name": "proof", "duration_s": 10, "movement": "ken_burns"},
                {"name": "callback", "duration_s": 5, "movement": "zoom_out"},
            ],
        },
        "caption_style": "tiktok_bounce_highlight",
        "music_style": "curious_playful",
        "target_duration_s": 56,
        "target_words": 130,
    },
    "behind_the_scenes": {
        "name": "Bastidores / Behind the Scenes",
        "description": "Mostra o processo por tras. Cria conexao e autenticidade.",
        "framework": "STAR",
        "structure": {
            "hook_type": "pattern_interrupt",
            "hook_duration_s": 3,
            "sections": [
                {"name": "hook_tease", "duration_s": 3, "movement": "zoom_in_face"},
                {"name": "situation", "duration_s": 10, "movement": "ken_burns"},
                {"name": "task", "duration_s": 8, "movement": "zoom_pulse"},
                {"name": "action", "duration_s": 20, "movement": "zoom_in_face"},
                {"name": "result", "duration_s": 12, "movement": "ken_burns"},
                {"name": "callback", "duration_s": 5, "movement": "zoom_out"},
            ],
        },
        "caption_style": "tiktok_bounce_highlight",
        "music_style": "chill_lo_fi",
        "target_duration_s": 58,
        "target_words": 130,
    },
}


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
            "duration": f"~{tmpl['target_duration_s']}s",
        }
        for key, tmpl in TEMPLATES.items()
    ]

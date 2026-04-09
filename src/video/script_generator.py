"""
Script Generator para videos virais.
Gera roteiros JSON estruturados usando Gemini com formula viral completa.
"""

import json
import logging

logger = logging.getLogger(__name__)


def _get_light_model():
    from agno.models.google import Gemini
    return Gemini(id="gemini-2.5-flash")


def generate_script(
    topic: str,
    style: str = "tutorial",
    duration: int = 60,
    reference_context: str = "",
    brand_voice: str = "",
    source_type: str = "avatar",
    person_description: str = "",
) -> dict:
    """
    Gera roteiro JSON estruturado para video viral.

    Args:
        topic: Tema/ideia do video.
        style: Template (tutorial, storytelling, listicle, transformation, qa, behind_the_scenes).
        duration: Duracao alvo em segundos.
        reference_context: Contexto de referencia (posts analisados, conta monitorada).
        brand_voice: Tom de voz da marca (se disponivel no BrandProfile).

    Returns:
        Dict com roteiro estruturado.
    """
    from src.video.templates import get_template

    template = get_template(style)
    framework = template["framework"]
    target_words = template["target_words"]

    brand_instruction = ""
    if brand_voice:
        brand_instruction = (
            f"\n\nTOM DE VOZ DA MARCA: {brand_voice}. "
            "Adapte a linguagem do roteiro para manter a identidade da marca."
        )

    reference_instruction = ""
    if reference_context:
        reference_instruction = (
            f"\n\nREFERENCIA DE CONTEUDO:\n{reference_context}\n"
            "Use esses padroes como inspiracao para o roteiro (nao copie, adapte)."
        )

    ai_motion_instruction = ""
    if source_type == "ai_motion":
        person_desc = person_description or "a person"
        ai_motion_instruction = f"""

11. IMAGE-TO-VIDEO (MODO AI MOTION - OBRIGATORIO):
    - Cada cena DEVE ter um campo "i2v_prompt": descricao detalhada em ingles da PESSOA
      realizando uma ACAO em um CENARIO especifico.
    - Formato: "[person description] [action] [scenario] [lighting] [camera angle]"
    - Exemplo: "Professional man in navy blazer presenting confidently in a modern glass office, gesturing with right hand, warm natural lighting, medium shot"
    - CONSISTENCIA: TODA cena deve descrever a MESMA roupa/aparencia: "{person_desc}"
    - VARIACAO: mude CENARIOS e ACOES entre cenas, NUNCA mude a aparencia da pessoa.
    - Inclua "camera_hint" por cena: zoom_in, pan_right, pan_left, tilt_up, dolly_forward, static
    - O i2v_prompt deve ser em INGLES (Kling AI funciona melhor em ingles).

12. PERSON_DESCRIPTION:
    - Inclua no topo do JSON: "person_description": "{person_desc}"
    - Esta descricao e usada para manter consistencia visual entre todas as cenas.
"""

    prompt = f"""Voce e um roteirista especialista em videos virais para Instagram Reels, TikTok e YouTube Shorts.
Seu trabalho e criar roteiros que MAXIMIZAM retencao e compartilhamentos.

TAREFA: Crie um roteiro de video viral sobre: "{topic}"
FORMATO: {template['name']} (framework {framework})
DURACAO: ~{duration} segundos (~{target_words} palavras de narracao)
{brand_instruction}{reference_instruction}

=== REGRAS DE ROTEIRIZACAO (OBRIGATORIAS) ===

1. HOOK (0-3 segundos):
   - 71% dos viewers decidem em 3 segundos. O hook PRECISA ser forte.
   - Tipos de hook (escolha o melhor para o tema):
     * bold_statement: Afirmacao forte/contraria ("90% das empresas erram nisso...")
     * question: Pergunta impossivel de ignorar ("Voce sabia que...")
     * pattern_interrupt: Algo inesperado que quebra o scroll
     * proof_first: Comeca pelo resultado ("De R$0 a R$50k em 3 meses...")
     * controversy: Desafia crenca do nicho ("Pare de usar hashtags")
   - ABRA um OPEN LOOP no hook: uma promessa/pergunta que SO fecha no final do video.

2. OPEN LOOPS (Efeito Zeigarnik):
   - O cerebro nao consegue parar de assistir com perguntas abertas.
   - Abra Loop 1 no hook (0:00). Abra Loop 2 entre 0:05-0:10 ANTES de fechar Loop 1.
   - Feche loops em sequencia (nunca todos de uma vez). Feche Loop 2 primeiro, Loop 1 por ultimo.

3. CURIOSITY GAPS:
   - Use frases que criam lacuna de informacao: "mas tem um detalhe que ninguem fala...", "o que eu descobri vai te surpreender..."
   - A promessa DEVE ser cumprida no video. Nao exagere (over-teasing = drop-off).

4. ARCO EMOCIONAL ({framework}):
   {"- PAS: Problema (dor) -> Agitacao (consequencias) -> Solucao (alivio)" if framework == "PAS" else ""}{"- BAB: Before (estado ruim) -> After (resultado incrivel) -> Bridge (como chegar la)" if framework == "BAB" else ""}{"- AIDA: Atencao (hook) -> Interesse (dados/fatos) -> Desejo (beneficios) -> Acao (CTA)" if framework == "AIDA" else ""}{"- STAR: Situacao -> Tarefa -> Acao -> Resultado" if framework == "STAR" else ""}

5. PACING:
   - 170-200 palavras por minuto para Reels.
   - Mais LENTO (120 WPM) em pontos-chave. Mais RAPIDO (200 WPM) em transicoes.
   - Micro-pausas de 200-400ms apos pontos importantes (marque com [pausa]).

6. CORTES E MOVIMENTOS:
   - Corte de cena / mudanca visual a cada 2-4 segundos (regra Hormozi/MrBeast).
   - Pattern interrupt visual a cada 15-25 segundos (troca de cenario, animacao especial).
   - Tipos de movimento: zoom_in_face, zoom_out, ken_burns, zoom_pulse.

7. OVERLAYS E B-ROLL:
   - Cada cena DEVE ter overlay_text (texto na tela) para quem assiste sem som.
   - Cenas de valor DEVEM ter broll_prompt (descricao de video contextual para gerar por IA).

8. LOOP OPTIMIZATION:
   - A ULTIMA frase deve reconectar com a PRIMEIRA (callback).
   - O viewer deve ter impulso de reassistir.
   - Use uma "callback phrase": repita uma palavra especifica do hook na frase final.
   - NAO termine com CTA que "quebre" o loop (nada de "me segue" no final).

9. LEGENDAS (SAFE ZONES):
   - 80% assiste sem som. Legendas sao OBRIGATORIAS.
   - Texto principal posicionado entre Y=200-1400px (longe do topo e fundo do Instagram).
   - Maximo 30 caracteres por linha de overlay_text.

10. HOOKS POLARIZANTES (para compartilhamentos):
    - Shares sao o sinal #1 do algoritmo Instagram 2026.
    - Desafie crencas do nicho (nao pessoas). "Voce nao precisa de X", "X esta morto".
    - Conteudo opinativo gera 3-5x mais shares que conteudo neutro.
{ai_motion_instruction}
=== FORMATO DE SAIDA (JSON ESTRITO) ===

Retorne APENAS o JSON abaixo, sem texto antes ou depois:

{{
  "title": "titulo curto do video (para referencia interna)",{'"person_description": "descricao fixa da aparencia da pessoa para consistencia visual",' if source_type == 'ai_motion' else ''}
  "hook": {{
    "type": "bold_statement|question|pattern_interrupt|proof_first|controversy",
    "narration": "texto exato da narracao do hook (max 15 palavras)",
    "on_screen_text": "TEXTO NA TELA (max 30 chars, caps para impacto)",
    "movement": "zoom_in_face",
    "duration_s": 3,
    "open_loop": "descricao do open loop que abre aqui"
  }},
  "scenes": [
    {{
      "name": "nome_da_cena",
      "narration": "texto exato da narracao desta cena",
      "on_screen_text": "TEXTO NA TELA",
      "movement": "zoom_in_face|zoom_out|ken_burns|zoom_pulse",
      "broll_prompt": "descricao para gerar video B-roll contextual (ou null se talking head)",{'"i2v_prompt": "prompt em ingles descrevendo a pessoa + acao + cenario (OBRIGATORIO se ai_motion)",' if source_type == 'ai_motion' else ''}
      {"" if source_type != "ai_motion" else '"camera_hint": "zoom_in|pan_right|pan_left|tilt_up|dolly_forward|static",'}
      "overlay_image_prompt": "descricao para gerar imagem de contexto (ou null)",
      "duration_s": 8,
      "sfx": "whoosh|pop|null",
      "loop_note": "se esta cena fecha/abre um loop, descreva qual"
    }}
  ],
  "callback": {{
    "narration": "frase final que reconecta com o hook",
    "on_screen_text": "TEXTO FINAL",
    "movement": "zoom_out",
    "duration_s": 5
  }},
  "config": {{
    "framework": "{framework}",
    "style": "{style}",
    "total_duration_s": {duration},
    "total_words": 0,
    "music_style": "{template['music_style']}",
    "caption_style": "{template['caption_style']}",
    "suggested_hashtags": ["max 5 hashtags relevantes"],
    "suggested_caption": "legenda SEO-friendly para o post (2-3 frases com keywords)"
  }}
}}

IMPORTANTE:
- A soma de duration_s de TODAS as cenas + hook + callback deve ser ~{duration}s.
- Total de palavras em narration deve ser ~{target_words}.
- Cada on_screen_text deve ter MAX 30 caracteres.
- Responda APENAS com o JSON, sem markdown, sem comentarios."""

    try:
        from agno.agent import Agent
        agent = Agent(
            model=_get_light_model(),
            description="Voce e um roteirista de videos virais. Responda APENAS com JSON valido.",
        )
        result = agent.run(prompt)
        raw = result.content if hasattr(result, "content") else str(result)

        # Clean up: remove markdown fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()

        script = json.loads(raw)

        # Validate basic structure
        if "hook" not in script or "scenes" not in script:
            raise ValueError("Script JSON missing required fields (hook, scenes)")

        # Calculate total words
        total_words = len(script["hook"].get("narration", "").split())
        for scene in script.get("scenes", []):
            total_words += len(scene.get("narration", "").split())
        total_words += len(script.get("callback", {}).get("narration", "").split())
        if "config" in script:
            script["config"]["total_words"] = total_words

        return script

    except json.JSONDecodeError as e:
        logger.error("Script generator returned invalid JSON: %s", e)
        return {"error": f"Roteiro gerado com formato invalido. Tente novamente. ({e})"}
    except Exception as e:
        logger.error("Script generation failed: %s", e)
        return {"error": f"Nao consegui gerar o roteiro. Tente novamente. ({e})"}


def format_script_preview(script: dict) -> str:
    """Formata o roteiro para exibicao amigavel ao usuario."""
    if "error" in script:
        return script["error"]

    lines = []
    title = script.get("title", "Sem titulo")
    config = script.get("config", {})

    lines.append(f"**{title}**")
    lines.append(f"Formato: {config.get('style', '?')} | Framework: {config.get('framework', '?')}")
    lines.append(f"Duracao: ~{config.get('total_duration_s', '?')}s | Palavras: ~{config.get('total_words', '?')}")
    lines.append("")

    # Hook
    hook = script.get("hook", {})
    lines.append(f"**HOOK ({hook.get('duration_s', 3)}s)** [{hook.get('type', '?')}]")
    lines.append(f'  Fala: "{hook.get("narration", "")}"')
    lines.append(f'  Tela: {hook.get("on_screen_text", "")}')
    if hook.get("open_loop"):
        lines.append(f'  Open loop: {hook["open_loop"]}')
    lines.append("")

    # Scenes
    for i, scene in enumerate(script.get("scenes", []), 1):
        lines.append(f"**CENA {i}: {scene.get('name', '')}** ({scene.get('duration_s', '?')}s) [{scene.get('movement', '')}]")
        lines.append(f'  Fala: "{scene.get("narration", "")}"')
        lines.append(f'  Tela: {scene.get("on_screen_text", "")}')
        if scene.get("broll_prompt"):
            lines.append(f'  B-roll: {scene["broll_prompt"]}')
        if scene.get("overlay_image_prompt"):
            lines.append(f'  Overlay: {scene["overlay_image_prompt"]}')
        if scene.get("loop_note"):
            lines.append(f'  Loop: {scene["loop_note"]}')
        lines.append("")

    # Callback
    callback = script.get("callback", {})
    if callback:
        lines.append(f"**CALLBACK ({callback.get('duration_s', 5)}s)**")
        lines.append(f'  Fala: "{callback.get("narration", "")}"')
        lines.append(f'  Tela: {callback.get("on_screen_text", "")}')
        lines.append("")

    # Config
    if config.get("suggested_caption"):
        lines.append(f"**Legenda sugerida:** {config['suggested_caption']}")
    if config.get("suggested_hashtags"):
        lines.append(f"**Hashtags:** {' '.join(config['suggested_hashtags'])}")

    return "\n".join(lines)

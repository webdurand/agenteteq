"""
Script Generator para videos virais com HeyGen.
Gera roteiros JSON estruturados usando Gemini, focado em narracao natural e emocional.
A quantidade de cenas e duracao se adaptam ao pedido do usuario.
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
    source_type: str = "heygen",
    person_description: str = "",
) -> dict:
    from src.video.templates import get_template, estimate_target_words

    template = get_template(style)
    framework = template["framework"]
    guidance = template["guidance"]
    target_words = estimate_target_words(duration)

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

    person_desc = person_description or "o creator"

    prompt = f"""Voce e uma equipe de 3 especialistas fundidos em um so:
1. ROTEIRISTA DE VIDEOS VIRAIS — domina hooks, open loops, pacing, Zeigarnik effect
2. ESTRATEGISTA DE MARKETING DIGITAL — domina algoritmo Instagram/TikTok 2026, metricas
3. ESPECIALISTA EM FALA NATURAL — domina ritmo, emocao, pausas, entonacao para TTS

Seu trabalho: criar roteiros que MAXIMIZAM retencao e compartilhamentos, com narracao que soa HUMANA e EMOCIONAL.

TAREFA: Crie um roteiro de video viral sobre: "{topic}"
FORMATO: {template['name']} (framework {framework})
DURACAO ALVO: ~{duration} segundos (~{target_words} palavras de narracao)
IDIOMA: Portugues brasileiro (coloquial, direto, como conversa com amigo)
ORIENTACAO DO FORMATO: {guidance}
{brand_instruction}{reference_instruction}

=== REGRA DE OURO: ADAPTE AO CONTEUDO ===

O numero de cenas NAO e fixo. Crie quantas cenas forem necessarias para o conteudo e duracao pedidos.
- Video de 15s: pode ter 1-2 cenas + hook + callback
- Video de 30s: pode ter 2-3 cenas
- Video de 60s: pode ter 3-5 cenas
- Video de 90s: pode ter 5-8 cenas
O conteudo dita a estrutura, nao o contrario. NAO encha linguica pra atingir duracao.

=== PRINCIPIOS DE NEUROCIENCIA APLICADA ===

A. DOPAMINA E RETENCAO:
   - Cada 3-5 segundos o cerebro precisa de um "reward signal" (dado novo, revelacao, mudanca)
   - Open loops ativam o "completion desire" — o cerebro NAO consegue parar sem resolver
   - Numeros especificos (6.2h, R$847, 3 passos) ativam mais que generalidades

B. PATTERN INTERRUPTS:
   - A atencao cai a cada 8-12 segundos — PRECISA de pattern interrupt
   - Tipos: dado surpreendente, mudanca de tom, pergunta retorica, pausa dramatica

C. FACE PROCESSING:
   - Contato visual direto = sensacao de conversa pessoal = mais engajamento

=== REGRAS DE ROTEIRIZACAO ===

1. HOOK (0-3 segundos):
   - 71% dos viewers decidem em 3 segundos. O hook PRECISA ser forte.
   - Tipos: bold_statement | question | pattern_interrupt | proof_first | controversy
   - ABRA um OPEN LOOP no hook: promessa que SO fecha no callback.

2. OPEN LOOPS (Efeito Zeigarnik):
   - Abra Loop 1 no hook. Pode abrir Loop 2 no meio se a duracao permitir.
   - Feche loops em sequencia. O ultimo fecha no callback.

3. ARCO EMOCIONAL ({framework}):
   - Deve ter CONTRASTE: alto-baixo-alto. Monotonia = scroll.

4. LOOP OPTIMIZATION:
   - Ultima frase reconecta com a primeira semanticamente.

5. LINGUAGEM (PORTUGUES BR):
   - "voce" (singular, direto). Contraste linguistico. Numeros especificos.

=== VOZ NATURAL E EMOCIONAL (CRITICO — DEFINE A QUALIDADE DO VIDEO) ===

O avatar digital vai FALAR esse texto. Se nao for natural, soa ROBOTICO e o video fica ruim.

REGRAS PARA NATURALIDADE:
- Escreva como se estivesse FALANDO COM UM AMIGO. Leia em voz alta antes.
- Use contracoes: "ta", "ne", "pra", "voce", "ce" (nao "esta", "nao e", "para").
- Frases CURTAS. Cada frase = uma respiracao. Maximo 12 palavras.
- Use reticencias (...) ANTES de revelacoes: "E o resultado... foi incrivel."
- Use travessao (—) pra pausas dramaticas: "Eu testei tudo — e nada funcionava."
- NUNCA junte ideias numa frase so. Cada ideia = uma frase.
- Varie comprimento: curta, media, curta. Cria ritmo natural.
- EVITE frases que comecam com "E" repetidamente.
- EVITE texto formal: "criacao de conteudo" → "criar conteudo".
- Use PERGUNTAS RETORICAS: "Sabe o que aconteceu?", "Sacou?"
- Transicao entre cenas deve ser COERENTE — conecte naturalmente.
- SIMETRIA: cada cena deve ter tempo SIMILAR de fala (variacao max 30%).

EMOCAO POR CENA (heygen_emotion):
- "Excited" — energia alta, entusiasmo. Use em hooks e revelacoes (max 1-2 cenas).
- "Friendly" — conversa natural. PREFERIDO como base.
- "Serious" — peso, autoridade. Use em dados surpreendentes.
- "Soothing" — calma, confianca. Use em callbacks e CTAs.
- "Broadcaster" — tom profissional. Use em fatos e listagens.

VELOCIDADE (heygen_speed):
- Range: 0.95 a 1.05. Nunca mais que isso — soa robotico.
- Hook: 1.05 (levemente mais rapido). Explicacoes: 1.0. Revelacoes: 0.95.

=== REGRAS VISUAIS HEYGEN ===

Cada cena DEVE ter:
- heygen_background: cor hex variada por cena. NUNCA repetir cor em 2 cenas seguidas.
  Exemplos: "#0D1117", "#1a1a2e", "#e63946", "#2d6a4f", "#f77f00", "#7209b7"
- heygen_emotion: emocao da voz (ver acima)
- heygen_speed: velocidade (ver acima)
- person_description: "{person_desc}"

=== FORMATO DE SAIDA (JSON ESTRITO) ===

Retorne APENAS o JSON abaixo, sem texto antes ou depois.
O array "scenes" pode ter QUANTAS CENAS FOREM NECESSARIAS (1, 2, 5, 8...).

{{
  "title": "titulo curto do video",
  "person_description": "{person_desc}",
  "hook": {{
    "type": "bold_statement|question|pattern_interrupt|proof_first|controversy",
    "narration": "texto exato da narracao (max 15 palavras, portugues BR coloquial)",
    "on_screen_text": "TEXTO NA TELA (max 30 chars, CAPS)",
    "duration_s": 3,
    "open_loop": "descricao do open loop",
    "heygen_background": {{"type": "color", "value": "#hex"}},
    "heygen_emotion": "Excited",
    "heygen_speed": 1.05
  }},
  "scenes": [
    {{
      "name": "nome_da_cena",
      "narration": "texto exato (portugues BR coloquial, max 12 palavras por frase)",
      "on_screen_text": "TEXTO NA TELA (max 30 chars)",
      "duration_s": 8,
      "heygen_background": {{"type": "color", "value": "#hex"}},
      "heygen_emotion": "Friendly",
      "heygen_speed": 1.0,
      "loop_note": "se esta cena fecha/abre um loop"
    }}
  ],
  "callback": {{
    "narration": "frase final que reconecta com o hook",
    "on_screen_text": "TEXTO FINAL",
    "duration_s": 5,
    "heygen_background": {{"type": "color", "value": "#hex"}},
    "heygen_emotion": "Soothing",
    "heygen_speed": 1.0
  }},
  "config": {{
    "framework": "{framework}",
    "style": "{style}",
    "total_duration_s": {duration},
    "total_words": 0,
    "suggested_hashtags": ["max 5 hashtags"],
    "suggested_caption": "legenda SEO-friendly (2-3 frases)"
  }}
}}

REGRAS FINAIS:
- Total de palavras de narracao: ~{target_words} palavras. Ajuste o numero de cenas pra isso.
- Se a duracao alvo e curta (15-20s), use POUCAS cenas com texto denso. Nao force muitas cenas.
- Se a duracao alvo e longa (60-90s), pode usar mais cenas. Mas so se o conteudo justificar.
- on_screen_text max 30 caracteres.
- Narracao deve soar NATURAL e EMOCIONAL em portugues BR.
- NUNCA repita mesma cor de background em 2 cenas seguidas.
- heygen_speed entre 0.95 e 1.05.
- Responda APENAS com o JSON, sem markdown."""

    try:
        from agno.agent import Agent
        agent = Agent(
            model=_get_light_model(),
            description="Roteirista viral + especialista em fala natural. Responda APENAS com JSON valido.",
        )
        result = agent.run(prompt)
        raw = result.content if hasattr(result, "content") else str(result)

        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()

        script = json.loads(raw)

        if "hook" not in script or "scenes" not in script:
            raise ValueError("Script JSON missing required fields (hook, scenes)")

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
    if "error" in script:
        return script["error"]

    lines = []
    title = script.get("title", "Sem titulo")
    config = script.get("config", {})

    lines.append(f"**{title}**")
    lines.append(f"Formato: {config.get('style', '?')} | Framework: {config.get('framework', '?')}")
    lines.append(f"Duracao: ~{config.get('total_duration_s', '?')}s | Palavras: ~{config.get('total_words', '?')}")
    lines.append("")

    hook = script.get("hook") or {}
    if hook:
        emotion = hook.get("heygen_emotion", "")
        lines.append(f"**HOOK ({hook.get('duration_s', 3)}s)** [{hook.get('type', '?')}]{f' | {emotion}' if emotion else ''}")
        lines.append(f'  Fala: "{hook.get("narration", "")}"')
        if hook.get("on_screen_text"):
            lines.append(f'  Tela: {hook["on_screen_text"]}')
        if hook.get("open_loop"):
            lines.append(f'  Open loop: {hook["open_loop"]}')
        lines.append("")

    for i, scene in enumerate(script.get("scenes", []), 1):
        emotion = scene.get("heygen_emotion", "")
        lines.append(f"**CENA {i}: {scene.get('name', '')}** ({scene.get('duration_s', '?')}s){f' | {emotion}' if emotion else ''}")
        lines.append(f'  Fala: "{scene.get("narration", "")}"')
        if scene.get("on_screen_text"):
            lines.append(f'  Tela: {scene["on_screen_text"]}')
        lines.append("")

    callback = script.get("callback") or {}
    if callback and callback.get("narration"):
        emotion = callback.get("heygen_emotion", "")
        lines.append(f"**CALLBACK ({callback.get('duration_s', 5)}s)**{f' | {emotion}' if emotion else ''}")
        lines.append(f'  Fala: "{callback.get("narration", "")}"')
        lines.append("")

    if config.get("suggested_caption"):
        lines.append(f"**Legenda sugerida:** {config['suggested_caption']}")
    if config.get("suggested_hashtags"):
        lines.append(f"**Hashtags:** {' '.join(config['suggested_hashtags'])}")

    return "\n".join(lines)

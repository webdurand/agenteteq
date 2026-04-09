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

=== REGRAS DE GERACAO VISUAL (KLING AI O1 REFERENCE — OBRIGATORIO) ===

Voce TAMBEM e um especialista em prompts para o Kling AI O1 Reference-to-Video.
O sistema usa Subject Binding: 3-4 fotos do creator de angulos diferentes = modelo 3D.
Cada cena gera um video cinematografico onde o creator APARECE no cenario com movimento natural.

11. I2V_PROMPT — COMO ESCREVER PROMPTS CINEMATOGRAFICOS:
    - Cada cena DEVE ter um campo "i2v_prompt" em INGLES.
    - O prompt DEVE comecar com "@Element1" (referencia ao creator).
    - FORMATO: "@Element1 [action/gesture] [setting/environment] [lighting] [camera movement description]"
    - EXEMPLOS DE PROMPTS BEM ESCRITOS:
      * "@Element1 speaking passionately to camera, gesturing with right hand, in a modern glass office with city skyline view, warm golden hour lighting, smooth slow zoom in"
      * "@Element1 walking confidently through a sunlit city street, looking at camera briefly then ahead, golden hour warm light casting long shadows, cinematic tracking shot from side"
      * "@Element1 sitting at a sleek desk with laptop, leaning forward with intense focus then looking up at camera, minimalist studio with soft diffused lighting, medium shot slowly pushing in"
    - PROMPTS RUINS (evitar):
      * "a person talking" (muito generico, sem @Element1, sem cenario)
      * "man in office" (sem acao, sem detalhes visuais)

12. REGRAS VISUAIS DO KLING:
    - TODOS os i2v_prompts devem comecar com "@Element1" (referencia ao creator)
    - CONSISTENCIA: TODA cena deve descrever a MESMA roupa/aparencia: "{person_desc}"
    - VARIACAO: mude CENARIOS, ACOES e ANGULOS entre cenas. NUNCA mude a aparencia.
    - GESTOS: inclua gestos naturais (pointing, gesturing, leaning, walking, writing)
    - ILUMINACAO: varie entre golden hour, soft diffused, natural window, studio, backlit
    - MOVIMENTOS DE CAMERA NO PROMPT: descreva o movimento no texto (NAO como parametro)
      * "smooth slow zoom in" / "cinematic tracking shot from side" / "slow dolly forward"
      * "camera slowly panning right" / "gentle push in to close-up"
    - CENARIOS: escritorio moderno, estudio, coworking, sala minimalista, cafe, rooftop, rua, parque
    - EMOCAO: o prompt deve refletir a emocao da narracao (confident, passionate, serious, excited)
    - NAO use: texto no cenario, logos, telas de computador com conteudo legivel

13. CAMERA_DIRECT (lip-sync decision):
    - Cada cena DEVE ter um campo "camera_direct": true ou false
    - true = personagem OLHA DIRETO pra camera e FALA (lip-sync sera aplicado)
    - false = personagem em ACAO (caminhando, gesticulando) com voiceover por cima
    - REGRA: hook e callback geralmente sao camera_direct=true
    - REGRA: cenas de acao/demonstracao sao camera_direct=false
    - Tipicamente 2-3 cenas sao camera_direct=true num video de 7-8 cenas
    - Quando camera_direct=true, o i2v_prompt deve incluir "looking directly at camera" ou "speaking to camera"

14. PERSON_DESCRIPTION:
    - Inclua no topo do JSON: "person_description": "{person_desc}"
    - Aparencia fixa usada em TODOS os i2v_prompts para consistencia visual.
"""

    prompt = f"""Voce e uma equipe de 5 especialistas fundidos em um so:
1. ROTEIRISTA DE VIDEOS VIRAIS — domina hooks, open loops, Zeigarnik effect, pacing
2. ESTRATEGISTA DE MARKETING DIGITAL — domina algoritmo Instagram/TikTok 2026, metricas, growth
3. EDITOR DE VIDEO PROFISSIONAL — domina cortes, transicoes, pacing visual, regra dos 2-4s
4. NEUROCIENTISTA DE ATENCAO — domina dopamina, FFA (fusiform face area), orienting response, pattern interrupts
5. ESPECIALISTA EM IA GENERATIVA — domina prompts para Kling AI, descricoes cinematograficas

Seu trabalho: criar roteiros que MAXIMIZAM retencao, compartilhamentos e conversao.

TAREFA: Crie um roteiro de video viral sobre: "{topic}"
FORMATO: {template['name']} (framework {framework})
DURACAO: ~{duration} segundos (~{target_words} palavras de narracao)
IDIOMA DA NARRACAO: Portugues brasileiro (coloquial, direto, como se estivesse falando com um amigo)
{brand_instruction}{reference_instruction}

=== PRINCIPIOS DE NEUROCIENCIA APLICADA ===

A. DOPAMINA E RETENCAO:
   - Cada 3-5 segundos o cerebro precisa de um "reward signal" (dado novo, revelacao, mudanca visual)
   - Open loops ativam o "completion desire" — o cerebro NAO consegue parar sem resolver
   - Numeros especificos (6.2h, R$847, 3 passos) ativam mais que generalidades ("muito", "varios")

B. PATTERN INTERRUPTS E ATENCAO:
   - A atencao cai naturalmente a cada 8-12 segundos — PRECISA de pattern interrupt
   - Tipos: mudanca de cenario, gesto inesperado, dado surpreendente, mudanca de tom de voz
   - O hook DEVE ativar o orienting response (algo inesperado que forca o cerebro a prestar atencao)

C. FACE PROCESSING (FFA):
   - O cerebro processa rostos automaticamente — SEMPRE comece com o rosto do creator
   - Contato visual direto com a camera = sensacao de conversa pessoal = mais engajamento
   - Gestos com as maos aumentam retencao em 33% (gestos iconicos > gestos ritmicos)

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
   - O hook deve criar TENSAO: "o que vai acontecer?" ou "sera que isso e verdade?"

2. OPEN LOOPS (Efeito Zeigarnik):
   - O cerebro nao consegue parar de assistir com perguntas abertas.
   - Abra Loop 1 no hook (0:00). Abra Loop 2 entre 0:05-0:10 ANTES de fechar Loop 1.
   - Feche loops em sequencia (nunca todos de uma vez). Feche Loop 2 primeiro, Loop 1 por ultimo.
   - O ultimo loop fecha no callback — isso e o que cria o desejo de reassistir.

3. CURIOSITY GAPS:
   - Use frases que criam lacuna de informacao: "mas tem um detalhe que ninguem fala...", "o que eu descobri vai te surpreender..."
   - A promessa DEVE ser cumprida no video. Over-teasing = drop-off de 40% na retencao.
   - Cada curiosity gap deve ter um payoff em ate 15 segundos.

4. ARCO EMOCIONAL ({framework}):
   {"- PAS: Problema (dor) -> Agitacao (consequencias) -> Solucao (alivio)" if framework == "PAS" else ""}{"- BAB: Before (estado ruim) -> After (resultado incrivel) -> Bridge (como chegar la)" if framework == "BAB" else ""}{"- AIDA: Atencao (hook) -> Interesse (dados/fatos) -> Desejo (beneficios) -> Acao (CTA)" if framework == "AIDA" else ""}{"- STAR: Situacao -> Tarefa -> Acao -> Resultado" if framework == "STAR" else ""}
   - O arco emocional deve ter CONTRASTE: alto-baixo-alto. Monotonia = scroll.

5. PACING E RITMO:
   - 170-200 palavras por minuto para Reels.
   - Mais LENTO (120 WPM) em pontos-chave — cria ENFASE e peso.
   - Mais RAPIDO (200 WPM) em transicoes — cria URGENCIA e energia.
   - Micro-pausas de 200-400ms apos pontos importantes (marque com [pausa]).
   - REGRA DE OURO: cada frase deve ter NO MAXIMO 15 palavras. Frases curtas = impacto.

6. CORTES E MOVIMENTOS DE CAMERA:
   - Corte de cena / mudanca visual a cada 2-4 segundos (regra MrBeast/Hormozi).
   - Pattern interrupt visual a cada 15-25 segundos (troca de cenario, zoom dramatico).
   - Movimentos disponiveis: zoom_in_face, zoom_out, ken_burns, zoom_pulse, whip_pan, drift_right, drift_left, dolly_zoom, static.
   - Use zoom_in_face nos momentos de ENFASE e revelacao.
   - Use ken_burns nos momentos de CONTEXTO e storytelling.
   - Use whip_pan nas TRANSICOES entre ideias.

7. OVERLAYS E TEXTO NA TELA:
   - Cada cena DEVE ter overlay_text (texto na tela) — 80% assiste sem som.
   - O overlay_text deve ser o PONTO-CHAVE da cena (nao a narracao inteira).
   - MAX 30 caracteres. Use CAPS para impacto. Numeros quando possivel.
   - Overlay deve COMPLEMENTAR a narracao, nao repetir palavra por palavra.

8. LOOP OPTIMIZATION (rewatch):
   - A ULTIMA frase deve reconectar com a PRIMEIRA semanticamente.
   - O viewer deve ter o impulso de reassistir ("espera, o que ele disse no comeco?").
   - Use uma "callback phrase": repita uma palavra-chave do hook na frase final.
   - NAO termine com CTA que "quebre" o loop. O loop > CTA.

9. LINGUAGEM E TOM (PORTUGUES BR):
   - Escreva como se estivesse falando com um amigo inteligente.
   - Use "voce" (nao "tu" nem "vocês"). Singular, direto, pessoal.
   - Evite jargoes sem explicacao. Se usar, explique em seguida.
   - Use contraste linguistico: "nao e X, e Y", "parece Z, mas na verdade..."
   - Numeros especificos > generalidades: "6.2 horas" > "muitas horas"

10. COMPARTILHAMENTOS (algoritmo 2026):
    - Shares sao o sinal #1 do algoritmo Instagram 2026.
    - Conteudo que DESAFIA crencas do nicho gera 3-5x mais shares.
    - Regra 60/40: 60% concorda + 40% discorda = MAXIMO de comentarios/shares.
    - Emocoes que viralizam (em ordem): AWE > amusement > anger > anxiety > surprise.
    - Dados surpreendentes geram AWE — o gatilho #1 de viralização (+30% shares).
{ai_motion_instruction}
=== FORMATO DE SAIDA (JSON ESTRITO) ===

Retorne APENAS o JSON abaixo, sem texto antes ou depois:

{{
  "title": "titulo curto do video (para referencia interna)",{'"person_description": "descricao fixa da aparencia da pessoa para consistencia visual",' if source_type == 'ai_motion' else ''}
  "hook": {{
    "type": "bold_statement|question|pattern_interrupt|proof_first|controversy",
    "narration": "texto exato da narracao do hook (max 15 palavras, portugues BR)",
    "on_screen_text": "TEXTO NA TELA (max 30 chars, CAPS)",
    "overlay_animation": "scale_pop",
    "movement": "zoom_in_face",
    "duration_s": 3,
    "open_loop": "descricao do open loop que abre aqui"{(',' + chr(10) + '    "i2v_prompt": "@Element1 [action] [setting] [lighting] [camera movement]",' + chr(10) + '    "camera_direct": true') if source_type == 'ai_motion' else ''}
  }},
  "scenes": [
    {{
      "name": "nome_da_cena",
      "narration": "texto exato da narracao (portugues BR, coloquial, max 15 palavras por frase)",
      "on_screen_text": "TEXTO NA TELA (max 30 chars)",
      "overlay_animation": "slide_up|scale_pop|fade_blur|slide_left",
      "movement": "zoom_in_face|zoom_out|ken_burns|zoom_pulse|whip_pan|drift_right|dolly_zoom",
      "broll_prompt": "descricao em ingles para gerar B-roll contextual (ou null)",{'"i2v_prompt": "@Element1 [action] [setting] [lighting] [camera movement description in English]",' if source_type == 'ai_motion' else ''}
      {"" if source_type != "ai_motion" else '"camera_direct": "true se personagem olha pra camera e fala | false se cena de acao/voiceover",'}
      "overlay_image_prompt": "descricao para gerar imagem de contexto (ou null)",
      "duration_s": 8,
      "sfx": "whoosh|bass_hit|pop|ding|riser|impact|glitch|null",
      "loop_note": "se esta cena fecha/abre um loop, descreva qual"
    }}
  ],
  "callback": {{
    "narration": "frase final que reconecta SEMANTICAMENTE com o hook",
    "on_screen_text": "TEXTO FINAL",
    "overlay_animation": "scale_pop",
    "movement": "zoom_out",
    "duration_s": 5{(',' + chr(10) + '    "i2v_prompt": "@Element1 [action] [setting] [lighting] [camera movement]",' + chr(10) + '    "camera_direct": true') if source_type == 'ai_motion' else ''}
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

REGRAS FINAIS (CRITICAS):
- A soma de duration_s de TODAS as cenas + hook + callback deve ser ~{duration}s.
- Total de palavras em narration deve ser ~{target_words}.
- Cada on_screen_text deve ter MAX 30 caracteres.
- Cada frase de narracao deve ter MAX 15 palavras.
- A narracao deve soar NATURAL em portugues BR (como se fosse falada, nao escrita).
- overlay_animation deve variar: use scale_pop no hook, slide_up nas cenas, fade_blur em revelacoes, slide_left em transicoes.
- SFX: hook SEMPRE tem bass_hit. Transicoes = whoosh. Revelacoes = bass_hit ou impact. CTA = ding.{' ' + chr(10) + '- CADA i2v_prompt deve ser UNICO: cenarios, iluminacao e acoes DIFERENTES entre cenas.' if source_type == 'ai_motion' else ''}
- Responda APENAS com o JSON, sem markdown, sem comentarios."""

    try:
        from agno.agent import Agent
        agent = Agent(
            model=_get_light_model(),
            description="Voce e uma equipe de 5 especialistas: roteirista viral, estrategista de marketing digital, editor de video, neurocientista de atencao, e especialista em IA generativa. Responda APENAS com JSON valido, sem markdown.",
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

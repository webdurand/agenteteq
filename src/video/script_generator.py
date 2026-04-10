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

    heygen_instruction = ""
    if source_type == "heygen":
        person_desc = person_description or "o creator"
        heygen_instruction = f"""

=== REGRAS DE GERACAO VISUAL (HEYGEN AVATAR — OBRIGATORIO) ===

Voce TAMBEM e um especialista em videos com avatares digitais da HeyGen.
O sistema usa um avatar fotorrealista treinado do creator — ele APARECE falando em cada cena.
Cada cena pode ter cenario diferente (background), emocao diferente, e velocidade de fala diferente.
O HeyGen gera transicoes automaticas entre cenas.

11. HEYGEN_BACKGROUND — CENARIOS POR CENA:
    - Cada cena DEVE ter um campo "heygen_background" com tipo e valor.
    - Para AGORA, use apenas "color" com cores OUSADAS e VARIADAS.
      Exemplos: "#0D1117" (dark tech), "#1a1a2e" (dark blue), "#e63946" (vermelho bold),
      "#2d6a4f" (verde profundo), "#f77f00" (laranja vibrante), "#7209b7" (roxo),
      "#16213e" (navy), "#0f3460" (deep blue), "#533483" (deep purple), "#e94560" (pink bold)
    - VARIACAO OBRIGATORIA: NUNCA use a mesma cor em 2 cenas seguidas.
    - CONTRASTE EMOCIONAL:
      * Cenas de DOR/PROBLEMA = cores escuras e frias (#0D1117, #1a1a2e)
      * Cenas de ENERGIA/REVELACAO = cores quentes e vibrantes (#e63946, #f77f00)
      * Cenas de SOLUCAO/RESULTADO = cores ricas (#7209b7, #2d6a4f)
      * Hook = cor ousada que GRITA (#e63946, #e94560)
      * CTA/Callback = cor que CONVIDA (#2d6a4f, #16213e)

12. HEYGEN_SCENE_DESCRIPTION — DESCRICAO VISUAL CINEMATOGRAFICA:
    - Cada cena DEVE ter um campo "heygen_scene_description" em PORTUGUES.
    - Descreva o que o VIEWER deveria ver: expressao do creator, gestos, cenario ideal, camera.
    - Essa descricao e usada pro Seedance 2.0 (cenarios cinematograficos com IA).
    - EXEMPLOS BEM ESCRITOS:
      * "Creator em escritorio moderno, pilha de papeis e telas, expressando frustracao. Camera faz zoom out lento mostrando a sobrecarga."
      * "Close no rosto do creator com sorriso enigmatico. Efeito digital sutil. Corte rapido."
      * "Creator caminhando enquanto telas virtuais aparecem ao redor. Cenario dinamico e futurista."
      * "Creator para, sorriso confiante, gesticula para a tela. Cenario vibrante e impactante."
    - A descricao deve refletir a EMOCAO da narracao (frustracao, surpresa, confianca, entusiasmo).

13. HEYGEN_EMOTION — EMOCAO DA VOZ:
    - Cada cena DEVE ter um campo "heygen_emotion" com uma das opcoes:
      * "Excited" — energia alta, entusiasmo, surpresa. Use em hooks e revelacoes.
      * "Friendly" — conversa natural, acessivel. Use em explicacoes e transicoes.
      * "Serious" — peso, autoridade, dados importantes. Use em dados surpreendentes.
      * "Soothing" — calma, confianca, conclusao. Use em callbacks e CTAs.
      * "Broadcaster" — tom de apresentador profissional. Use em listagens e fatos.
    - VARIACAO: alterne emocoes entre cenas para criar dinamismo.
    - A emocao deve COMBINAR com o conteudo da narracao.
    - PREFERIR "Friendly" como base — soa mais natural e humano.
    - Usar "Excited" com moderacao (max 1-2 cenas) — uso excessivo soa falso.

14. HEYGEN_SPEED — VELOCIDADE DA FALA:
    - Cada cena pode ter "heygen_speed" (0.5 a 1.5, default 1.0).
    - REGRA: mantenha entre 0.95 e 1.1. Valores extremos soam roboticos.
    - Hook: 1.05 (levemente mais rapido, sutil).
    - Explicacoes: 1.0 (natural).
    - Revelacoes/dados: 0.95 (levemente mais lento, peso).
    - Callback/CTA: 1.0 (natural, nao forcar lentidao).

15. VOZ NATURAL (CRITICO — o TTS precisa soar HUMANO):
    - A narracao sera lida por um TTS com voz clonada. Se o texto nao for natural, a voz soa ROBOTICA.
    - REGRAS PARA NATURALIDADE:
      * Escreva como se estivesse FALANDO, nao escrevendo. Leia em voz alta antes.
      * Use contracoes e informalidade: "ta", "ne", "pra", "voce" (nao "esta", "nao e", "para", "voces").
      * Frases CURTAS com pausas naturais via pontuacao (ponto final = respiracao).
      * Use reticencias (...) ANTES de revelacoes: "E o resultado... foi incrivel."
      * Use travessao (—) pra pausas dramaticas: "Eu testei tudo — e nada funcionava."
      * NUNCA junte muitas ideias numa frase so. Cada ideia = uma frase.
      * Varie o comprimento: frase curta, frase media, frase curta. Cria ritmo.
      * EVITE frases que comecam com "E" repetidamente.
    - GESTOS E MAOS:
      * O avatar faz gestos automaticos baseados no ritmo do texto.
      * Frases curtas e pausadas = gestos CONTIDOS e naturais.
      * Frases longas e rapidas = gestos EXAGERADOS (mao pode passar no rosto — EVITAR).
      * PORTANTO: mantenha frases curtas e bem pontuadas pra gestos naturais.

16. PERSON_DESCRIPTION:
    - Inclua no topo do JSON: "person_description": "{person_desc}"
"""

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
5. ESPECIALISTA EM IA GENERATIVA — domina prompts para geracao de video com IA (Kling, HeyGen), descricoes cinematograficas

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

5. PACING, RITMO E PONTUACAO (CRITICO PARA TTS):
   - 170-200 palavras por minuto para Reels.
   - Mais LENTO (120 WPM) em pontos-chave — cria ENFASE e peso.
   - Mais RAPIDO (200 WPM) em transicoes — cria URGENCIA e energia.
   - REGRA DE OURO: cada frase deve ter NO MAXIMO 15 palavras. Frases curtas = impacto.

   PONTUACAO EXPRESSIVA (o TTS le pontuacao como respiracao e ritmo):
   - Use PONTO FINAL apos cada frase. Cada ponto = respiracao natural.
   - Use VIRGULAS pra criar micro-pausas ritmicas dentro da frase.
   - Use RETICENCIAS (...) antes de revelacoes e dados surpreendentes. Ex: "E o resultado... foi de 300%."
   - Use PONTO DE EXCLAMACAO com moderacao (max 1-2 por cena) pra enfase real.
   - Use TRAVESSAO (—) pra criar pausas dramaticas. Ex: "Eu testei tudo — e nada funcionava."
   - NUNCA junte frases longas sem pontuacao. Cada ideia = uma frase separada.
   - Exemplo RUIM: "eu descobri que a maioria das pessoas erra nessa parte e por isso nao consegue resultado"
   - Exemplo BOM: "Eu descobri algo. A maioria das pessoas... erra nessa parte. E por isso, nao consegue resultado."

   PARAGRAFOS SIMETRICOS (OBRIGATORIO):
   - Cada cena deve ter narracao com tamanho SIMILAR (variacao max 30% entre cenas).
   - Divida a narracao em blocos curtos de 2-3 frases, separados por ponto final.
   - Cada bloco deve ter entre 8-20 palavras.
   - Se uma cena tem 15 palavras, a proxima nao deve ter 40. Mantenha equilibrio.
   - Hook: 8-15 palavras (curto, impactante).
   - Cenas: 15-25 palavras cada (consistente entre elas).
   - Callback: 10-18 palavras (fechamento conciso).

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
{heygen_instruction}{ai_motion_instruction}
=== FORMATO DE SAIDA (JSON ESTRITO) ===

Retorne APENAS o JSON abaixo, sem texto antes ou depois:

{{
  "title": "titulo curto do video (para referencia interna)",{'"person_description": "descricao fixa da aparencia da pessoa para consistencia visual",' if source_type in ('ai_motion', 'heygen') else ''}
  "hook": {{
    "type": "bold_statement|question|pattern_interrupt|proof_first|controversy",
    "narration": "texto exato da narracao do hook (max 15 palavras, portugues BR)",
    "on_screen_text": "TEXTO NA TELA (max 30 chars, CAPS)",
    "overlay_animation": "scale_pop",
    "movement": "zoom_in_face",
    "duration_s": 3,
    "open_loop": "descricao do open loop que abre aqui"{(',' + chr(10) + '    "i2v_prompt": "@Element1 [action] [setting] [lighting] [camera movement]",' + chr(10) + '    "camera_direct": true') if source_type == 'ai_motion' else ''}{(',' + chr(10) + '    "heygen_background": {{"type": "color", "value": "#hex_ousado"}},' + chr(10) + '    "heygen_scene_description": "descricao visual cinematografica da cena em portugues",' + chr(10) + '    "heygen_emotion": "Excited",' + chr(10) + '    "heygen_speed": 1.1') if source_type == 'heygen' else ''}
  }},
  "scenes": [
    {{
      "name": "nome_da_cena",
      "narration": "texto exato da narracao (portugues BR, coloquial, max 15 palavras por frase)",
      "on_screen_text": "TEXTO NA TELA (max 30 chars)",
      "overlay_animation": "slide_up|scale_pop|fade_blur|slide_left",
      "movement": "zoom_in_face|zoom_out|ken_burns|zoom_pulse|whip_pan|drift_right|dolly_zoom",
      "broll_prompt": "descricao em ingles para gerar B-roll contextual (ou null)",{'"i2v_prompt": "@Element1 [action] [setting] [lighting] [camera movement description in English]",' if source_type == 'ai_motion' else ''}
      {"" if source_type != "ai_motion" else '"camera_direct": "true se personagem olha pra camera e fala | false se cena de acao/voiceover",'}{"" if source_type != "heygen" else '"heygen_background": {"type": "color|image", "value": "#hex ou url", "image_prompt": "scene description in english for background (optional)"},'}
      {"" if source_type != "heygen" else '"heygen_scene_description": "descricao visual cinematografica da cena (cenario, expressao, gestos, camera)",'}
      {"" if source_type != "heygen" else '"heygen_emotion": "Excited|Friendly|Serious|Soothing|Broadcaster",'}
      {"" if source_type != "heygen" else '"heygen_speed": 1.0,'}
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
    "duration_s": 5{(',' + chr(10) + '    "i2v_prompt": "@Element1 [action] [setting] [lighting] [camera movement]",' + chr(10) + '    "camera_direct": true') if source_type == 'ai_motion' else ''}{(',' + chr(10) + '    "heygen_background": {{"type": "color", "value": "#hex_ousado"}},' + chr(10) + '    "heygen_scene_description": "descricao visual cinematografica do callback",' + chr(10) + '    "heygen_emotion": "Soothing",' + chr(10) + '    "heygen_speed": 0.95') if source_type == 'heygen' else ''}
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
- A duracao e FLEXIVEL. O video deve ter o tempo que o conteudo PRECISAR — nao force {duration}s exatos. Se o conteudo pede 40s, faca 40s. Se pede 55s, faca 55s. A meta e ~{duration}s mas a naturalidade do conteudo e mais importante que bater o tempo exato.
- Total de palavras em narration deve ser ~{target_words} (ajuste proporcionalmente a duracao real).
- Cada on_screen_text deve ter MAX 30 caracteres.
- Cada frase de narracao deve ter MAX 15 palavras. Separe por ponto final.
- A narracao deve soar NATURAL em portugues BR (como se fosse falada, nao escrita).
- PONTUACAO: use pontos finais entre frases, virgulas pra ritmo, reticencias pra suspense, travessoes pra pausas dramaticas.
- SIMETRIA: as narracoes das cenas devem ter tamanho SIMILAR entre si (variacao max 30%).
- overlay_animation deve variar: use scale_pop no hook, slide_up nas cenas, fade_blur em revelacoes, slide_left em transicoes.
- SFX: hook SEMPRE tem bass_hit. Transicoes = whoosh. Revelacoes = bass_hit ou impact. CTA = ding.{' ' + chr(10) + '- CADA i2v_prompt deve ser UNICO: cenarios, iluminacao e acoes DIFERENTES entre cenas.' if source_type == 'ai_motion' else ''}{' ' + chr(10) + '- HEYGEN: cada cena DEVE ter heygen_background, heygen_scene_description, heygen_emotion e heygen_speed.' + chr(10) + '- HEYGEN: NUNCA repita a mesma cor de background em 2 cenas seguidas.' + chr(10) + '- HEYGEN: prefira "Friendly" como emocao base. Use "Excited" no maximo em 1-2 cenas.' + chr(10) + '- HEYGEN: velocidade entre 0.95 e 1.05 (valores extremos soam roboticos).' + chr(10) + '- HEYGEN: frases CURTAS e bem pontuadas = gestos naturais. Frases longas = gestos exagerados (mao no rosto).' if source_type == 'heygen' else ''}
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

    # Check if this is a HeyGen script
    is_heygen = bool(script.get("hook", {}).get("heygen_emotion"))

    # Hook
    hook = script.get("hook", {})
    hook_extra = ""
    if hook.get("heygen_emotion"):
        hook_extra = f' | {hook["heygen_emotion"]}'
    lines.append(f"**HOOK ({hook.get('duration_s', 3)}s)** [{hook.get('type', '?')}]{hook_extra}")
    lines.append(f'  Fala: "{hook.get("narration", "")}"')
    if is_heygen and hook.get("heygen_scene_description"):
        lines.append(f'  Visual: {hook["heygen_scene_description"]}')
    elif hook.get("on_screen_text"):
        lines.append(f'  Tela: {hook["on_screen_text"]}')
    if hook.get("open_loop"):
        lines.append(f'  Open loop: {hook["open_loop"]}')
    if hook.get("heygen_background"):
        bg = hook["heygen_background"]
        lines.append(f'  Background: {bg.get("value", "")}')
    lines.append("")

    # Scenes
    for i, scene in enumerate(script.get("scenes", []), 1):
        scene_extra = ""
        if scene.get("heygen_emotion"):
            scene_extra = f' | {scene["heygen_emotion"]}'
        lines.append(f"**CENA {i}: {scene.get('name', '')}** ({scene.get('duration_s', '?')}s){scene_extra}")
        lines.append(f'  Fala: "{scene.get("narration", "")}"')
        if is_heygen and scene.get("heygen_scene_description"):
            lines.append(f'  Visual: {scene["heygen_scene_description"]}')
        elif scene.get("on_screen_text"):
            lines.append(f'  Tela: {scene["on_screen_text"]}')
        if scene.get("heygen_background"):
            bg = scene["heygen_background"]
            lines.append(f'  Background: {bg.get("value", "")}')
        if scene.get("loop_note"):
            lines.append(f'  Loop: {scene["loop_note"]}')
        lines.append("")

    # Callback
    callback = script.get("callback", {})
    if callback:
        cb_extra = ""
        if callback.get("heygen_emotion"):
            cb_extra = f' | {callback["heygen_emotion"]}'
        lines.append(f"**CALLBACK ({callback.get('duration_s', 5)}s)**{cb_extra}")
        lines.append(f'  Fala: "{callback.get("narration", "")}"')
        if is_heygen and callback.get("heygen_scene_description"):
            lines.append(f'  Visual: {callback["heygen_scene_description"]}')
        elif callback.get("on_screen_text"):
            lines.append(f'  Tela: {callback["on_screen_text"]}')
        if callback.get("heygen_background"):
            bg = callback["heygen_background"]
            lines.append(f'  Background: {bg.get("value", "")}')
        lines.append("")

    # Config
    if config.get("suggested_caption"):
        lines.append(f"**Legenda sugerida:** {config['suggested_caption']}")
    if config.get("suggested_hashtags"):
        lines.append(f"**Hashtags:** {' '.join(config['suggested_hashtags'])}")

    return "\n".join(lines)

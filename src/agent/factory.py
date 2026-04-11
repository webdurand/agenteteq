import logging

from src.agent.assistant import get_assistant
from src.tools.web_search import create_web_search_tool, create_fetch_page_tool, create_explore_site_tool
from src.tools.deep_research import create_deep_research_tool
from src.tools.google_tools import create_google_tools
from src.tools.slack_tools import create_slack_tools
from src.memory.integrations import get_user_integrations

logger = logging.getLogger(__name__)


def create_agent_with_tools(
    session_id: str,
    notifier=None,
    include_explore: bool = False,
    user_id: str = None,
    channel: str = "whatsapp",
    extra_instructions: list[str] | None = None,
    include_scheduler: bool = True,
    include_knowledge: bool = True,
):
    phone = user_id or session_id
    search_tools = [
        create_web_search_tool(notifier, user_id=phone),
        create_fetch_page_tool(notifier),
        create_deep_research_tool(notifier, phone),
    ]
    if include_explore:
        search_tools.append(create_explore_site_tool(notifier))

    # Injeta Google tools se o usuario tiver integracoes ativas
    google_instructions = []
    try:
        read_emails, get_calendar_events, create_calendar_event = create_google_tools(phone)

        has_gmail = bool(get_user_integrations(phone, provider="gmail"))
        has_calendar = bool(get_user_integrations(phone, provider="google_calendar"))

        if has_gmail:
            search_tools.append(read_emails)
            google_instructions.append(
                "GMAIL: O usuario conectou o Gmail. Use read_emails para ler e-mails "
                "(busca por query do Gmail, ex: 'is:unread', 'from:pessoa@email.com', 'newer_than:1d'). "
                "Quando o usuario perguntar sobre e-mails, use essa tool automaticamente."
            )
        if has_calendar:
            search_tools.extend([get_calendar_events, create_calendar_event])
            google_instructions.append(
                "GOOGLE CALENDAR: O usuario conectou o Google Calendar. "
                "Use get_calendar_events para ver compromissos futuros. "
                "Use create_calendar_event para criar novos eventos (sempre confirme data, hora e timezone antes de criar). "
                "Quando o usuario pedir algo sobre agenda, reunioes ou compromissos, use essas tools automaticamente."
            )
    except Exception as e:
        logger.error("Erro ao carregar Google tools para %s: %s", phone, e)

    # Injeta Slack tools se o usuario tiver integracao ativa
    slack_instructions = []
    try:
        has_slack = bool(get_user_integrations(phone, provider="slack"))
        if has_slack:
            list_channels, read_messages, search_msgs = create_slack_tools(phone)
            search_tools.extend([list_channels, read_messages, search_msgs])
            slack_instructions.append(
                "SLACK: O usuario conectou o Slack. "
                "Use list_slack_channels para ver os canais disponiveis. "
                "Use read_slack_messages para ler mensagens recentes de um canal. "
                "Use search_slack para pesquisar mensagens por palavra-chave. "
                "Quando o usuario perguntar sobre notificacoes, mensagens ou conversas do Slack, use essas tools automaticamente."
            )
    except Exception as e:
        logger.error("Erro ao carregar Slack tools para %s: %s", phone, e)

    # Injeta Branding tools
    branding_instructions = []
    try:
        from src.tools.branding_tools import create_branding_tools
        branding_tools = create_branding_tools(phone)
        search_tools.extend(branding_tools)
        branding_instructions.append(
            "BRANDING/IDENTIDADE VISUAL: O usuario pode configurar perfis de marca com cores, fontes, logo e estilo. "
            "Use get_brand_profile para consultar o branding ANTES de gerar carrosseis — assim as cores e fontes "
            "ja saem no padrao da marca. Se o usuario nao tiver branding configurado e pedir um carrossel, "
            "SUGIRA criar um: 'Que tal configurar sua identidade visual comigo? Assim tudo que eu gerar ja sai "
            "com a cara da sua marca. Me conta: qual o nome da marca, suas cores principais e que estilo de fonte prefere.' "
            "Use update_brand_profile para criar/atualizar. Use list_brand_profiles para listar os perfis. "
            "Use extract_branding_from_image quando o usuario enviar artes existentes e quiser extrair o estilo. "
            "O usuario pode ter MULTIPLOS perfis (ex: um para cada projeto/rede social). "
            "Quando o usuario pedir carrossel e tiver branding, use as cores do perfil padrao automaticamente — "
            "injete color_palette e style_anchor nos slides baseado no branding.\n\n"
            "PRESETS DE CARROSSEL: O usuario pode salvar estilos de carrossel como presets reutilizaveis. "
            "Apos gerar um carrossel que o usuario gostar, OFERECA salvar o estilo: "
            "'Gostou desse estilo? Posso salvar como preset pra usar de novo. Que nome quer dar?' "
            "Use save_carousel_preset para salvar (nome, cores, style_anchor, formato). "
            "Use list_carousel_presets para listar os presets salvos. "
            "Quando o usuario pedir carrossel e mencionar um preset (ex: 'usa meu estilo escuro', "
            "'usa o preset Clean'), passe preset_name no generate_image. "
            "O preset sobrescreve o branding padrao quando especificado. "
            "NAO salve presets automaticamente — SEMPRE pergunte primeiro.\n\n"
            "REFERENCIAS DE ESTILO: O usuario pode salvar imagens como referencias visuais "
            "para manter consistencia de estilo. Use save_style_reference quando o usuario "
            "enviar uma imagem de inspiracao (print de post, screenshot de design, foto de referencia) "
            "e quiser guardar para consultar depois. Use list_style_references ANTES de criar "
            "qualquer design novo para checar se o usuario tem referencias salvas — se tiver, "
            "use-as como guia de estilo (cores, layout, vibe).\n\n"
            "FLUXO 'REPLIQUE ESSE ESTILO':\n"
            "Quando o usuario enviar uma imagem de referencia (print, link de post, foto de outro design):\n"
            "1. ANALISE a referencia: Use extract_branding_from_image para extrair cores, fontes, estilo. "
            "Identifique: layout, paleta de cores, tipografia, espacamento.\n"
            "2. CONFIRME com o usuario: 'Identifiquei o estilo: fundo escuro, titulo bold centralizado, "
            "cores X/Y/Z. Quer que eu siga esse estilo?'\n"
            "3. SALVE como referencia (se o usuario quiser): Use save_style_reference para guardar "
            "na galeria. 'Salvei como referencia. Quer que eu aplique ao brand profile tambem?'\n"
            "4. REPLIQUE com o Carousel HTML: Passe a URL da referencia em reference_image_url "
            "no generate_image com generation_mode='html'. O LLM analisa e gera HTML/CSS replicando o estilo.\n"
            "5. Para posts do Instagram (via link): Use view_post_by_url para analisar o post completo. "
            "Extraia cores dominantes, layout, tipografia."
        )
    except Exception as e:
        logger.error("Erro ao carregar Branding tools para %s: %s", phone, e)

    # Canvas Editor removido — substituído pelo Carousel HTML Engine
    canvas_instructions = []

    # Injeta Social Monitoring tools
    social_instructions = []
    try:
        from src.tools.social_monitor import create_social_tools
        social_tools_tuple = create_social_tools(phone, channel=channel, notifier=notifier)
        search_tools.extend(social_tools_tuple)
        social_instructions.append(
            "SOCIAL MONITORING: O usuario pode monitorar contas de redes sociais como referencia de conteudo. "
            "Plataformas suportadas: instagram e youtube. Passe platform='instagram' ou platform='youtube' conforme o contexto. "
            "FLUXO IMPORTANTE: Quando o usuario mencionar um perfil de rede social (ex: '@fulano do instagram', "
            "'me mostra o canal do MrBeast no youtube', 'me mostra o conteudo do natgeo'), "
            "PRIMEIRO use preview_account para buscar e MOSTRAR o perfil "
            "e os posts com metricas. SO DEPOIS pergunte se ele quer salvar para monitoramento continuo. "
            "Se o usuario confirmar, use track_account para salvar. "
            "Use list_tracked_accounts para ver as contas ja monitoradas. "
            "Use get_account_insights para analises de conteudo, topicos e tendencias de uma conta JA monitorada. "
            "Use get_trending_content para ver os posts com mais engajamento de uma conta JA monitorada. "
            "Use analyze_posts para OLHAR posts de QUALQUER conta publica (incluindo as IMAGENS) e responder perguntas — "
            "NAO precisa estar monitorada. Ex: 'sobre o que fala o ultimo post do @fulano?', 'descreve o post mais recente'. "
            "Use view_post_by_url quando o usuario enviar um LINK de post do Instagram (instagram.com/p/... ou /reel/...). "
            "A tool acessa o post, baixa as imagens/video e analisa o conteudo visual e textual em detalhe. "
            "Para Reels, a tool baixa e analisa o VIDEO COMPLETO (cenas, falas, texto na tela). "
            "Use view_youtube_video quando o usuario enviar um LINK do YouTube (youtube.com/watch, youtu.be/, youtube.com/shorts/). "
            "A tool baixa o video em baixa qualidade e analisa o conteudo completo (visual + audio + texto). "
            "Use create_content_script para gerar roteiros de carrossel/video inspirados em uma referencia.\n\n"
            "ALERTAS DE CONTEUDO: Apos salvar uma conta com track_account, OFERECA ativar alertas: "
            "'Quer que eu te avise no WhatsApp quando essa conta postar algo que bombar?' "
            "Se o usuario aceitar, use toggle_alerts(enabled=True). "
            "O alerta dispara automaticamente quando um post novo tem engajamento 2x acima da media. "
            "Use toggle_alerts(enabled=False) para desativar. "
            "NAO ative alertas automaticamente — SEMPRE pergunte primeiro.\n\n"
            "ALERTAS DE TENDENCIAS: Alem dos alertas por conta, o usuario pode ativar alertas de TENDENCIAS. "
            "Quando ativado, o TEQ analisa automaticamente os posts novos de TODAS as contas monitoradas "
            "e detecta temas em comum — indicando uma tendencia no nicho. "
            "Se detectar, envia sugestao de conteudo via WhatsApp (max 1 por dia). "
            "Use toggle_trend_alerts(enabled=True) para ativar e toggle_trend_alerts(enabled=False) para desativar. "
            "OFERECA ativar quando o usuario tiver 2+ contas monitoradas: "
            "'Voce tem varias contas monitoradas. Quer que eu te avise quando detectar uma tendencia "
            "em comum entre elas? Assim voce pode surfar os temas em alta no seu nicho.' "
            "NAO ative automaticamente — SEMPRE pergunte primeiro."
        )
    except Exception as e:
        logger.error("Erro ao carregar Social tools para %s: %s", phone, e)

    # Injeta Content Planner tools
    calendar_instructions = []
    try:
        from src.tools.content_planner import create_content_planner_tools
        plan_content, list_content_plan, update_content_plan_tool, delete_content_plan_tool = create_content_planner_tools(phone, notifier=notifier)
        search_tools.extend([plan_content, list_content_plan, update_content_plan_tool, delete_content_plan_tool])
        calendar_instructions.append(
            "CALENDARIO DE CONTEUDO: O usuario pode planejar publicacoes via chat. "
            "Use plan_content para adicionar conteudo ao calendario (titulo, tipo, plataforma, data). "
            "Use list_content_plan para listar conteudos planejados (filtro por status ou periodo). "
            "Use update_content_plan para atualizar status, data ou titulo. "
            "Use delete_content_plan para remover.\n\n"
            "FLUXO: Quando o usuario disser algo como 'agenda um post pra terca', 'quero planejar um carrossel', "
            "'planeja um conteudo sobre X pra semana que vem', use plan_content automaticamente. "
            "Infira o content_type do contexto (post, carousel, video, reels, blog). "
            "Infira a plataforma do contexto (instagram, youtube, blog). "
            "Se a data nao for mencionada, deixe sem data (o usuario pode definir depois). "
            "Se o usuario pedir pra ver o calendario, use list_content_plan.\n\n"
            "STATUS: idea → planned → producing → ready → published. "
            "Atualize o status conforme o progresso (ex: se o usuario gerou o carrossel, mude pra 'producing' ou 'ready'). "
            "Apos gerar conteudo (carrossel, roteiro), OFERECA adicionar ao calendario: "
            "'Quer que eu adicione isso ao seu calendario de conteudo?'"
        )
    except Exception as e:
        logger.error("Erro ao carregar Content Planner tools para %s: %s", phone, e)

    # Injeta Video Creation tools
    video_instructions = []
    try:
        from src.tools.video_tools import create_video_tools
        video_tools_tuple = create_video_tools(phone, channel=channel, notifier=notifier)
        search_tools.extend(video_tools_tuple)
        video_instructions.append(
            "CRIACAO DE VIDEO: O usuario pode criar videos virais (Reels/TikTok/Shorts) via chat. "
            "O fluxo e: entrevista → pesquisa → roteiro NO CHAT → aprovacao → geracao.\n\n"
            "TOOLS DISPONIVEIS:\n"
            "- list_video_templates: Mostra os formatos disponiveis (tutorial, storytelling, listicle, transformation, qa, behind_the_scenes).\n"
            "- create_video_script(topic, style, duration, reference_account): "
            "Gera roteiro estruturado via IA. Use APENAS quando o usuario pedir roteiro DO ZERO sem ter combinado falas.\n"
            "- create_video_script_direct(scenes_text, title, style): "
            "Converte falas JA APROVADAS no chat em roteiro pro HeyGen. "
            "Recebe as falas separadas por '|'. NAO usa IA — vai direto. "
            "Use SEMPRE que o usuario combinou e aprovou falas no chat.\n"
            "- edit_script(script_id, instructions): Edita um roteiro existente com instrucoes em linguagem natural. "
            "Ex: 'muda o hook', 'troca a cena 3'. O usuario pode pedir quantos ajustes quiser antes de gerar.\n"
            "- generate_video(script_id): Inicia a geracao do video com avatar HeyGen + voz do Digital Twin.\n"
            "- setup_avatar(media_urls, media_type): Configura o avatar do usuario para video. "
            "Aceita 1-4 fotos ou 1 video. Automaticamente cria avatar no HeyGen tambem.\n"
            "- setup_heygen_voice(voice_id): Configura a voz do HeyGen. "
            "Chame sem argumentos para listar vozes disponiveis. "
            "O usuario escolhe a voz e voce chama com o voice_id escolhido.\n"
            "- update_voice(audio_urls?, voice_name?, action?): Gerencia a voz clonada no ElevenLabs. "
            "action='add' salva amostras (pode enviar PICOTADO, 1 por vez). "
            "action='clone' clona no ElevenLabs com TODAS as amostras salvas. "
            "action='status' mostra quantas amostras tem. "
            "Fluxo: usuario envia audios → voce chama add pra cada → quando tiver bastante, chama clone.\n"
            "- setup_digital_twin(video_url?, consent_video_url?): Configura Digital Twin pro Seedance 2.0. "
            "Chame SEM argumentos pra mostrar instrucoes de gravacao ao usuario. "
            "Chame COM video_url + consent_video_url pra enviar pro HeyGen treinar. "
            "O Digital Twin permite videos CINEMATOGRAFICOS com cenarios dinamicos.\n"
            "- manage_digital_twin(action, twin_id?): Gerencia o Digital Twin do avatar ativo. "
            "action='status' checa se o treinamento terminou no HeyGen. "
            "action='set' + twin_id vincula um ID manual (verifica no HeyGen antes). "
            "action='remove' remove o twin e volta pra photo avatar. "
            "action='info' mostra info completa do avatar.\n"
            "- list_videos: Lista videos gerados.\n"
            "- adjust_video(video_id, instructions): Pede ajustes em video ja gerado e re-gera.\n"
            "- review_video(video_id): Analisa o video com IA e da nota + sugestoes.\n"
            "- add_video_to_calendar(video_id, scheduled_at?, platform?): Adiciona ao calendario.\n\n"

            "FLUXO RECOMENDADO (SIGA ESTA ORDEM):\n"
            "1. ENTREVISTA: Pergunte ao usuario: tema, publico-alvo, angulo, tom e duracao.\n"
            "2. PESQUISA (OBRIGATORIO): ANTES de montar o roteiro, SEMPRE pesquise sobre o tema:\n"
            "   a) Use web_search(tema, max_results=5) para buscar informacoes atuais sobre o assunto.\n"
            "   b) Use web_search(tema + ' instagram reels viral', max_results=3) para ver o que ta funcionando.\n"
            "   c) Se o tema for complexo ou tecnico, use deep_research(tema) para pesquisa aprofundada.\n"
            "   d) Se o usuario tiver contas monitoradas no nicho, use get_trending_content para ver posts top.\n"
            "   e) Analise os resultados e identifique: dados especificos, angulos unicos, gaps de informacao.\n"
            "   OBJETIVO: O roteiro deve ter DADOS REAIS e ATUAIS, nao informacao generica.\n"
            "3. ROTEIRO NO CHAT: Monte o roteiro DIRETAMENTE no chat, usando sua expertise em neuroscience, "
            "hooks virais, open loops, pacing, etc. Mostre as falas ao usuario em texto formatado. "
            "TODA a edicao e ajuste acontece aqui no chat, ate o usuario aprovar.\n"
            "4. ESTIMATIVA DE DURACAO: Antes de gerar, SEMPRE informe a duracao estimada ao usuario. "
            "Calcule: total de palavras das falas / 2.3 = segundos. "
            "Diga algo como: 'Essas falas dao ~20 segundos de video. Posso gerar?' "
            "Se o usuario quiser mais curto ou mais longo, ajuste as falas NO CHAT antes de gerar.\n"
            "5. APROVACAO → TOOL DIRETA: Quando o usuario aprovar ('ficou otimo', 'pode gerar', 'adorei'), "
            "use create_video_script_direct(scenes_text='fala1|fala2|fala3') com as falas EXATAS aprovadas. "
            "Isso converte direto pro formato HeyGen SEM reescrever nada.\n"
            "5. GERACAO: Gere com generate_video(script_id).\n"
            "6. POS-GERACAO: Ofereca review_video e add_video_to_calendar.\n\n"

            "QUANDO USAR CADA TOOL DE ROTEIRO:\n"
            "- create_video_script_direct: PREFERIDO. Use quando voce e o usuario ja combinaram as falas no chat. "
            "As falas aprovadas vao DIRETO pro HeyGen sem passar por IA intermediaria. Economiza custo e tempo.\n"
            "- create_video_script: Use APENAS quando o usuario pedir roteiro do zero SEM ter combinado falas "
            "(ex: 'cria um video sobre IA' sem dar as falas). Isso chama IA pra gerar o roteiro.\n\n"

            "REGRAS CRITICAS:\n"
            "- NUNCA chame generate_video MAIS DE UMA VEZ por aprovacao. "
            "Se o usuario disser 'pode fazer' ou 'ta otimo', chame generate_video UMA UNICA VEZ. "
            "NUNCA chame 2x na mesma resposta. Se ja chamou, NAO chame de novo.\n"
            "- NUNCA gere roteiro sem pesquisar o tema antes.\n"
            "- NUNCA gere video sem mostrar o roteiro e receber aprovacao.\n"
            "- O roteiro e EDITAVEL: o usuario pode pedir ajustes quantas vezes quiser no chat antes de gerar.\n"
            "- ANTES de afirmar que um video esta pronto, use list_videos para verificar o status REAL.\n"
            "- NUNCA adicione conteudo extra alem do que o usuario aprovou. "
            "Se o usuario aprovou 3 frases curtas, o video tera 3 frases curtas. Nao infle.\n\n"

            "REGRA DE OURO — FALAS APROVADAS VAO DIRETO:\n"
            "- Quando o usuario aprova falas no chat, use create_video_script_direct com as falas EXATAS.\n"
            "- NUNCA reescreva, resuma, expanda ou substitua falas aprovadas.\n"
            "- O que o usuario aprovou E o que vai pro video. Ponto final.\n"
            "- Se quiser sugerir melhorias, faca ANTES da aprovacao, no chat. Depois de aprovado, vai direto.\n\n"

            "DIGITAL TWIN E VOZ:\n"
            "- O Digital Twin e o que da voz e aparencia ao avatar HeyGen.\n"
            "- Se o usuario nao tem Digital Twin, oriente a criar pelo app.heygen.com\n"
            "- Use manage_digital_twin(action='set', twin_id=ID) para vincular o ID.\n"
            "- setup_heygen_voice configura a voz separada."
        )
    except Exception as e:
        logger.error("Erro ao carregar Video tools para %s: %s", phone, e)

    # Instruções do Co-Pilot de Conteúdo (Content Intelligence Layer)
    copilot_instructions = [
        "CO-PILOT DE CONTEUDO (Content Intelligence Layer):\n"
        "Quando o usuario compartilhar conteudo de referencia, voce atua como co-pilot de criacao.\n\n"

        "RECONHECIMENTO — detecte o modo co-pilot quando:\n"
        "- Usuario envia imagem com contexto: 'olha isso', 'gostei desse', 'quero algo parecido', "
        "'vi esse post', 'o que acha disso', 'me inspira nisso', 'cria algo nesse estilo'\n"
        "- Usuario encaminha screenshot de post de rede social\n"
        "- Usuario descreve post que viu: 'vi um post sobre X que bombou'\n"
        "- Usuario envia imagem SEM texto (analise e pergunte se quer se inspirar)\n\n"

        "ANALISE ESTRUTURADA — ao identificar conteudo de referencia, analise:\n"
        "1. TEMA: topico central e angulo abordado\n"
        "2. FORMATO: carrossel, reels, video longo, foto, stories\n"
        "3. ESTRUTURA: hook (como abre), desenvolvimento, CTA (como fecha)\n"
        "4. ESTILO VISUAL: cores dominantes, tipografia, composicao, identidade\n"
        "5. POR QUE FUNCIONA: o que torna o conteudo engajante\n"
        "6. OPORTUNIDADE: como o usuario pode abordar o mesmo tema com angulo proprio\n\n"

        "Apresente a analise de forma concisa (nao precisa numerar todos os pontos, "
        "foque no que e mais relevante para ACAO).\n\n"

        "ACOES — ofereça multiplos caminhos (use botoes no WhatsApp):\n"
        "- Gerar carrossel inspirado → use generate_image (aplique branding do usuario)\n"
        "- Gerar roteiro de video/reels → responda com roteiro estruturado\n"
        "- Monitorar a conta → se identificar o @ da conta, ofereça preview_account/track_account\n"
        "- Ver mais posts dessa conta → use analyze_posts\n"
        "- Adaptar formato → se era reels, ofereça carrossel; se era carrossel, ofereça roteiro\n"
        "- Explicar o que funciona → analise educativa sem gerar conteudo\n\n"

        "PRINCIPIOS:\n"
        "- INSPIRAR, nunca copiar. Conteudo gerado deve ser ORIGINAL.\n"
        "- Sempre aplicar branding do usuario quando gerar conteudo visual (consulte get_brand_profile).\n"
        "- Se o usuario nao especificar o que quer, ofereça 2-3 opcoes mais relevantes.\n"
        "- Conecte com o contexto do usuario: nicho, contas monitoradas, marca.\n"
        "- Quando o usuario escolher gerar carrossel, INCLUA o tema e estilo na descricao dos slides "
        "baseado na analise da referencia, adaptado ao branding do usuario.\n"
        "- Este e o ponto de entrada principal para criacao de conteudo. "
        "Qualquer input de referencia (imagem, link, descricao) passa por aqui."
    ]

    # Instruções de mensagens interativas (botões e listas)
    interactive_instructions = []
    if channel == "whatsapp":
        interactive_instructions.append(
            "MENSAGENS INTERATIVAS: Voce pode incluir botoes e listas nas suas respostas. "
            "O usuario vera botoes clicaveis no WhatsApp em vez de ter que digitar. "
            "Use o formato abaixo NO FINAL da sua resposta (apos o texto):\n\n"
            "Para BOTOES (max 3 opcoes, max 20 caracteres cada):\n"
            "[BUTTONS]\n"
            "Opcao 1\n"
            "Opcao 2\n"
            "Opcao 3\n"
            "[/BUTTONS]\n\n"
            "Para LISTAS (menus com mais opcoes):\n"
            "[LIST Menu]\n"
            "Item 1 — descricao curta\n"
            "Item 2 — descricao curta\n"
            "[/LIST]\n\n"
            "QUANDO USAR BOTOES:\n"
            "- Confirmar acao: Sim / Nao\n"
            "- Aprovar carrossel: Aprovado / Ajustar / Refazer\n"
            "- Completar task: Concluida / Adiar / Ver detalhes\n"
            "- Escolha entre 2-3 opcoes claras\n"
            "- Apos criar lembrete com due_date: Lembrar 1 dia antes / Lembrar no dia / Ambos\n"
            "QUANDO NAO USAR BOTOES:\n"
            "- Respostas informativas (o usuario perguntou algo, voce respondeu)\n"
            "- Perguntas abertas que precisam de texto livre\n"
            "- Conversas casuais\n"
            "NAO use botoes em TODA resposta. Use somente quando facilita a interacao com escolhas claras."
        )
    elif channel in ("web_text", "web_voice"):
        interactive_instructions.append(
            "BOTOES INTERATIVOS: Voce pode incluir botoes clicaveis nas respostas. "
            "Use o formato no FINAL da resposta:\n"
            "[BUTTONS]\n"
            "Opcao 1\n"
            "Opcao 2\n"
            "[/BUTTONS]\n\n"
            "Use apenas quando a resposta pede confirmacao ou escolha entre poucas opcoes (2-3). "
            "Exemplos: aprovar carrossel, confirmar acao, escolher entre opcoes. "
            "NAO use em respostas informativas ou conversas casuais."
        )

    # Instruções de tarefas: prioridade, categoria e auto-link com reminders
    from src.agent.instructions import (
        TASK_INSTRUCTIONS, UPSELL_INSTRUCTIONS,
        BRIEFING_INSTRUCTIONS, REPURPOSING_INSTRUCTIONS,
    )
    task_instructions = TASK_INSTRUCTIONS
    upsell_instructions = UPSELL_INSTRUCTIONS
    briefing_instructions = BRIEFING_INSTRUCTIONS

    repurposing_instructions = REPURPOSING_INSTRUCTIONS

    all_instructions = (extra_instructions or []) + google_instructions + slack_instructions + social_instructions + branding_instructions + canvas_instructions + calendar_instructions + video_instructions + copilot_instructions + interactive_instructions + task_instructions + upsell_instructions + briefing_instructions + repurposing_instructions

    return get_assistant(
        session_id=session_id,
        extra_tools=search_tools,
        channel=channel,
        extra_instructions=all_instructions if all_instructions else None,
        include_scheduler=include_scheduler,
        include_knowledge=include_knowledge,
        user_id=user_id or session_id,
    )

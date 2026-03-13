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
            "injete color_palette e style_anchor nos slides baseado no branding."
        )
    except Exception as e:
        logger.error("Erro ao carregar Branding tools para %s: %s", phone, e)

    # Injeta Social Monitoring tools
    social_instructions = []
    try:
        from src.tools.social_monitor import create_social_tools
        social_tools_tuple = create_social_tools(phone, channel=channel)
        search_tools.extend(social_tools_tuple)
        social_instructions.append(
            "SOCIAL MONITORING: O usuario pode monitorar contas de redes sociais como referencia de conteudo. "
            "FLUXO IMPORTANTE: Quando o usuario mencionar um perfil de rede social (ex: '@fulano do instagram', "
            "'me mostra o conteudo do natgeo'), PRIMEIRO use preview_account para buscar e MOSTRAR o perfil "
            "e os posts com metricas. SO DEPOIS pergunte se ele quer salvar para monitoramento continuo. "
            "Se o usuario confirmar, use track_account para salvar. "
            "Use list_tracked_accounts para ver as contas ja monitoradas. "
            "Use get_account_insights para analises de conteudo, topicos e tendencias de uma conta JA monitorada. "
            "Use get_trending_content para ver os posts com mais engajamento de uma conta JA monitorada. "
            "Use analyze_posts para OLHAR posts de QUALQUER conta publica (incluindo as IMAGENS) e responder perguntas — "
            "NAO precisa estar monitorada. Ex: 'sobre o que fala o ultimo post do @fulano?', 'descreve o post mais recente'. "
            "Use create_content_script para gerar roteiros de carrossel/video inspirados em uma referencia."
        )
    except Exception as e:
        logger.error("Erro ao carregar Social tools para %s: %s", phone, e)

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

    all_instructions = (extra_instructions or []) + google_instructions + slack_instructions + social_instructions + branding_instructions + interactive_instructions

    return get_assistant(
        session_id=session_id,
        extra_tools=search_tools,
        channel=channel,
        extra_instructions=all_instructions if all_instructions else None,
        include_scheduler=include_scheduler,
    )

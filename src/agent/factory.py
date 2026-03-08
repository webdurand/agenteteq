from src.agent.assistant import get_assistant
from src.tools.web_search import create_web_search_tool, create_fetch_page_tool, create_explore_site_tool
from src.tools.deep_research import create_deep_research_tool
from src.tools.google_tools import create_google_tools
from src.memory.integrations import get_user_integrations


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
        print(f"[FACTORY] Erro ao carregar Google tools para {phone}: {e}")

    all_instructions = (extra_instructions or []) + google_instructions

    return get_assistant(
        session_id=session_id,
        extra_tools=search_tools,
        channel=channel,
        extra_instructions=all_instructions if all_instructions else None,
        include_scheduler=include_scheduler,
    )

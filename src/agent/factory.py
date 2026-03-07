from src.agent.assistant import get_assistant
from src.tools.web_search import create_web_search_tool, create_fetch_page_tool, create_explore_site_tool
from src.tools.deep_research import create_deep_research_tool


def create_agent_with_tools(
    session_id: str,
    notifier=None,
    include_explore: bool = False,
    user_id: str = None,
    channel: str = "whatsapp",
    extra_instructions: list[str] | None = None,
):
    search_tools = [
        create_web_search_tool(notifier),
        create_fetch_page_tool(notifier),
        create_deep_research_tool(notifier, user_id or session_id),
    ]
    if include_explore:
        search_tools.append(create_explore_site_tool(notifier))
    return get_assistant(
        session_id=session_id,
        extra_tools=search_tools,
        channel=channel,
        extra_instructions=extra_instructions,
    )

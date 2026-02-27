import os
from agno.agent import Agent
from agno.db.sqlite import SqliteDb

def get_model():
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    
    if provider == "openai":
        from agno.models.openai import OpenAIChat
        return OpenAIChat(id=os.getenv("LLM_MODEL", "gpt-4o-mini"))
    elif provider == "anthropic":
        from agno.models.anthropic import Claude
        return Claude(id=os.getenv("LLM_MODEL", "claude-3-5-sonnet-20241022"))
    else:
        from agno.models.openai import OpenAIChat
        return OpenAIChat(id="gpt-4o-mini")

def get_assistant(session_id: str) -> Agent:
    """
    Retorna a instância do agente configurada para uma sessão específica (ex: número do WhatsApp).
    """
    db_path = os.getenv("AGNO_DB_PATH", "sessions.db")
    
    # Importação atrasada para evitar erro se a ferramenta ainda não estiver pronta
    try:
        from src.tools.blog_publisher import publish_post
        tools = [publish_post]
    except ImportError:
        tools = []
    
    return Agent(
        name="Assistente do Diario Teq",
        model=get_model(),
        session_id=session_id,
        db=SqliteDb(db_file=db_path),
        add_datetime_to_context=True,
        add_history_to_context=True,
        num_history_runs=5,
        markdown=True,
        instructions=[
            "Você é o assistente pessoal do blog Diario Teq.",
            "Sua principal função é receber transcrições de áudios ou textos enviados pelo autor e transformá-los em posts para o blog.",
            "Se o conteúdo estiver confuso ou faltarem informações, você deve fazer perguntas curtas e diretas ao autor.",
            "Quando você tiver informações suficientes, apresente uma sugestão do post contendo o título e o corpo do texto.",
            "O post deve ter um título criativo e seguir um formato de leitura agradável.",
            "Aguarde a confirmação explícita do autor antes de publicar.",
            "Só chame a ferramenta de publicação depois que o usuário aprovar o conteúdo."
        ],
        tools=tools,
    )

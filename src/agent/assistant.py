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
    elif provider == "gemini":
        from agno.models.google import Gemini
        return Gemini(id=os.getenv("LLM_MODEL", "gemini-2.0-flash"))
    else:
        from agno.models.openai import OpenAIChat
        return OpenAIChat(id="gpt-4o-mini")

def get_assistant(session_id: str) -> Agent:
    """
    Retorna a instância do agente configurada para uma sessão específica (ex: número do WhatsApp).
    """
    db_url = os.getenv("AGNO_DB_URL", "sqlite:///sessions.db")
    
    # Importação atrasada para evitar erro se a ferramenta ainda não estiver pronta
    try:
        from src.tools.blog_publisher import publish_post
        tools = [publish_post]
    except ImportError:
        tools = []
    
    # Se estiver usando Turso/libsql, precisaremos ajustar o prefixo para sqlite+libsql://
    if db_url.startswith("libsql://") or db_url.startswith("https://"):
        auth_token = os.getenv("AGNO_DB_AUTH_TOKEN", "")
        # O SQLAlchemy com driver libsql precisa do auth token na URL se não for localhost
        if auth_token:
            db_url = db_url.replace("libsql://", f"sqlite+libsql://") + f"?authToken={auth_token}"
        else:
            db_url = db_url.replace("libsql://", f"sqlite+libsql://")
    elif not db_url.startswith("sqlite"):
        db_url = f"sqlite:///{db_url}"
    
    return Agent(
        name="Assistente do Diario Teq",
        model=get_model(),
        session_id=session_id,
        db=SqliteDb(db_url=db_url, table_name="sessions"),
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

import os
from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from src.memory.knowledge import get_knowledge_base
from src.tools.memory_manager import add_memory, delete_memory, list_memories

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
        return Gemini(id=os.getenv("LLM_MODEL", "gemini-2.5-flash"))
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
        tools = [publish_post, add_memory, delete_memory, list_memories]
    except ImportError:
        tools = [add_memory, delete_memory, list_memories]
        
    knowledge_base = get_knowledge_base()
    search_knowledge = os.getenv("MEMORY_MODE", "agentic").lower() == "agentic" and knowledge_base is not None
    
    # Se estiver usando Turso/libsql, precisaremos ajustar o prefixo para sqlite+libsql://
    if db_url.startswith("libsql://") or db_url.startswith("https://"):
        auth_token = os.getenv("AGNO_DB_AUTH_TOKEN", "")
        # O SQLAlchemy com driver sqlite tem problemas em lidar com Turso/libsql em versões recentes
        # Então quando o usuário configurar Turso, nós voltamos para um sqlite local pra evitar
        # os erros de `api error: status=405 Method Not Allowed` ao tentar criar a tabela remotamente via ORM.
        print("Aviso: Conexões libsql remotas apresentam instabilidades com o ORM do Agno.")
        print("         Fazendo fallback para banco SQLite local em 'sessions.db' para garantir o funcionamento.")
        db_url = "sqlite:///sessions.db"
    elif not db_url.startswith("sqlite"):
        db_url = f"sqlite:///{db_url}"
    
    return Agent(
        name="Assistente do Diario Teq",
        model=get_model(),
        session_id=session_id,
        db=SqliteDb(db_url=db_url), # Removido o table_name="sessions" que não existe no sqlite
        knowledge=knowledge_base,
        search_knowledge=search_knowledge,
        add_datetime_to_context=True,
        add_history_to_context=True,
        num_history_runs=5,
        markdown=True,
        instructions=[
            "Você é o assistente pessoal do Diario Teq.",
            "Sua principal função é atuar como um parceiro conversacional inteligente, ajudando o usuário nas mais diversas tarefas.",
            "O usuário pode te enviar textos ou áudios.",
            "Utilize sua memória sobre o usuário para fornecer respostas personalizadas e assertivas.",
            "Além de conversar, você possui ferramentas para publicar posts no blog.",
            "Se o usuário pedir para criar um post, ajude-o a formatar o conteúdo com um título criativo e leitura agradável.",
            "Aguarde a confirmação explícita do autor antes de chamar a ferramenta de publicação."
        ],
        tools=tools,
    )

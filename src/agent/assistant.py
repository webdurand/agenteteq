import os
from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from src.memory.knowledge import get_knowledge_base
from src.tools.memory_manager import add_memory, delete_memory, list_memories
from src.tools.task_manager import add_task, list_tasks, complete_task, delete_task

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

def get_assistant(session_id: str, extra_tools: list = None) -> Agent:
    """
    Retorna a instancia do agente configurada para uma sessao especifica (ex: numero do WhatsApp).
    
    Args:
        session_id: Identificador da sessao (numero de WhatsApp do usuario).
        extra_tools: Tools adicionais injetadas pelo orchestrator (ex: web_search, deep_research).
                     Permite que o orchestrator injete contexto (notifier, user_id) sem acoplamento.
    """
    db_url = os.getenv("AGNO_DB_URL", "sqlite:///sessions.db")
    
    try:
        from src.tools.blog_publisher import publish_post
        from src.tools.weather import get_weather
        from src.tools.scheduler_tool import schedule_message, list_schedules, cancel_schedule
        tools = [
            publish_post,
            add_memory, delete_memory, list_memories,
            add_task, list_tasks, complete_task, delete_task,
            get_weather,
            schedule_message, list_schedules, cancel_schedule,
        ]
    except ImportError as e:
        print(f"[ASSISTANT] Aviso: algumas tools nao carregaram ({e}). Usando conjunto basico.")
        tools = [add_memory, delete_memory, list_memories, add_task, list_tasks, complete_task, delete_task]
    
    if extra_tools:
        tools.extend(extra_tools)
        
    knowledge_base = get_knowledge_base()
    search_knowledge = os.getenv("MEMORY_MODE", "agentic").lower() == "agentic" and knowledge_base is not None
    
    if db_url.startswith("libsql://") or db_url.startswith("https://"):
        print("Aviso: Conexoes libsql remotas apresentam instabilidades com o ORM do Agno.")
        print("         Fazendo fallback para banco SQLite local em 'sessions.db' para garantir o funcionamento.")
        db_url = "sqlite:///sessions.db"
    elif not db_url.startswith("sqlite"):
        db_url = f"sqlite:///{db_url}"
    
    return Agent(
        name="Teq",
        model=get_model(),
        session_id=session_id,
        db=SqliteDb(db_url=db_url),
        knowledge=knowledge_base,
        search_knowledge=search_knowledge,
        add_datetime_to_context=True,
        add_history_to_context=True,
        num_history_runs=5,
        markdown=True,
        instructions=[
            "Voce e o Teq, assistente pessoal do Durand — parceiro de confianca, direto ao ponto e com bom humor.",
            "Fale como um amigo proximo que por acaso e muito inteligente: linguagem informal, sem robotice, sem formalidade desnecessaria.",
            "Pode usar girias leves, contracoes do portugues falado ('to', 'ta', 'pra', 'ne', 'cara') e emojis quando encaixar bem, sem exagero.",
            "Seja conciso: sem enrolacao, sem repetir o que o usuario acabou de dizer, sem introducoes longas.",
            "Quando for direto ao ponto (tarefas, pesquisa, codigo), seja objetivo. Quando for conversa, seja descontraido.",
            "Se nao souber de algo, admita de boa — pode pesquisar ou pedir mais contexto sem drama.",
            "O usuario pode te enviar textos ou audios. Responda sempre no mesmo tom da conversa.",
            "Utilize sua memoria sobre o usuario para personalizar as respostas. Quando aprender algo novo e relevante sobre o Durand (preferencias, rotina, projetos), salve com add_memory.",
            "Voce tem ferramentas de pesquisa: use web_search para buscas rapidas e pontuais, e deep_research para temas que precisam de profundidade ou multiplas fontes. Apos pesquisas relevantes, salve os achados com add_memory.",
            "Voce pode publicar posts no blog. Se o usuario quiser criar um post, ajude com titulo criativo e leitura fluida. Aguarde confirmacao explicita antes de publicar.",
            "Voce gerencia uma lista de tarefas. Quando o usuario mencionar algo que precisa fazer, faca perguntas contextuais (prazo, local, observacoes) — so as relevantes para aquela tarefa. Confirme o resumo antes de chamar add_task. Use sempre o session_id como user_id nas ferramentas de tarefas.",
            "Para listar tarefas use list_tasks, para concluir use complete_task, para remover use delete_task.",
            "Voce pode agendar mensagens proativas com schedule_message. Para 'daqui X minutos/horas', use trigger_type='date' com o parametro minutes_from_now (ex: minutes_from_now=5 para 'daqui 5 minutos', minutes_from_now=60 para 'daqui 1 hora'). Para recorrente, use trigger_type='cron' com cron_expression (ex: '0 8 * * *' para todo dia as 8h UTC). Use list_schedules para listar agendamentos ativos e cancel_schedule para cancelar.",
            "Se o usuario pedir algo que voce nao consegue fazer com as ferramentas disponiveis, avise de forma tranquila — tipo 'boa ideia, mas ainda nao consigo fazer isso, vamos aguardar umas atualizacoes?'.",
            "Quando receber a instrucao de saudacao de nova sessao, consulte suas memorias ANTES de responder para saber quais informacoes o usuario quer no cumprimento.",
        ],
        tools=tools,
    )

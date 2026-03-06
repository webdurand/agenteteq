import os
from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from src.memory.knowledge import get_knowledge_base
from src.tools.memory_manager import create_memory_tools
from src.tools.task_manager import create_task_tools

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

def get_assistant(session_id: str, extra_tools: list = None, channel: str = "whatsapp") -> Agent:
    """
    Retorna a instancia do agente configurada para uma sessao especifica (ex: numero do WhatsApp).
    
    Args:
        session_id: Identificador da sessao (numero de WhatsApp do usuario).
        extra_tools: Tools adicionais injetadas pelo orchestrator (ex: web_search, deep_research).
                     Permite que o orchestrator injete contexto (notifier, user_id) sem acoplamento.
        channel: O canal de comunicacao (ex: "whatsapp", "web"). Influencia as instrucoes de formato.
    """
    db_url = os.getenv("AGNO_DB_URL", "sqlite:///sessions.db")
    
    add_task, list_tasks, complete_task, reopen_task, delete_task = create_task_tools(session_id)
    add_memory, delete_memory, list_memories = create_memory_tools(session_id)

    try:
        from src.tools.blog_publisher import create_blog_tools
        publish_post_tools = create_blog_tools(session_id)
        from src.tools.weather import get_weather
        from src.tools.scheduler_tool import create_scheduler_tools
        schedule_message, list_schedules, cancel_schedule = create_scheduler_tools(session_id)
        from src.tools.carousel_generator import create_carousel_tools
        generate_carousel, list_carousels = create_carousel_tools(session_id, channel=channel)
        tools = [
            *publish_post_tools,
            add_memory, delete_memory, list_memories,
            add_task, list_tasks, complete_task, reopen_task, delete_task,
            get_weather,
            schedule_message, list_schedules, cancel_schedule,
            generate_carousel, list_carousels,
        ]
    except ImportError as e:
        print(f"[ASSISTANT] Aviso: algumas tools nao carregaram ({e}). Usando conjunto basico.")
        tools = [add_memory, delete_memory, list_memories, add_task, list_tasks, complete_task, reopen_task, delete_task]
    
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
    
    base_instructions = [
        "Voce e o Teq, assistente e parceiro de confianca, direto ao ponto e com bom humor.",
        "Fale como um amigo proximo que por acaso e muito inteligente: linguagem informal, sem robotice, sem formalidade desnecessaria.",
        "Pode usar girias leves, contracoes do portugues falado ('to', 'ta', 'pra', 'ne', 'cara'), sem exagero.",
        "Seja conciso: sem enrolacao, sem repetir o que o usuario acabou de dizer, sem introducoes longas.",
        "NUNCA narre o que voce vai fazer antes de fazer. Nao diga 'Deixa eu ver suas tarefas', 'Vou pesquisar isso', 'Deixa eu dar uma olhada'. Va direto ao resultado. Se precisar usar uma ferramenta, use silenciosamente e entregue a resposta pronta.",
        "Se uma ferramenta falhar ou retornar erro, corrija silenciosamente e tente de novo. NUNCA narre falhas, retentativas ou erros de ferramentas para o usuario. Responda APENAS com o resultado final, como se tivesse funcionado de primeira.",
        "Quando for direto ao ponto (tarefas, pesquisa, codigo), seja objetivo. Quando for conversa, seja descontraido.",
        "Se nao souber de algo, admita de boa — pode pesquisar ou pedir mais contexto sem drama.",
        "Não precisa ficar repetindo o nome do usuario.",
        "O usuario pode te enviar textos ou audios. Responda sempre no mesmo tom da conversa.",
        "Utilize sua memoria sobre o usuario para personalizar as respostas. Quando aprender algo novo e relevante sobre o Durand (preferencias, rotina, projetos), salve com add_memory.",
        "Voce tem ferramentas de pesquisa: use web_search para buscas rapidas e pontuais, e deep_research para temas que precisam de profundidade ou multiplas fontes. Apos pesquisas relevantes, salve os achados com add_memory.",
        "Voce pode publicar posts no blog. Se o usuario quiser criar um post, ajude com titulo criativo e leitura fluida. Aguarde confirmacao explicita antes de publicar.",
        "Voce gerencia uma lista de tarefas. Quando o usuario mencionar algo que precisa fazer, faca perguntas contextuais (prazo, local, observacoes) — so as relevantes para aquela tarefa. Confirme o resumo antes de chamar add_task.",
        "Para listar tarefas use list_tasks, para concluir use complete_task, para reabrir/marcar como pendente use reopen_task, para remover use delete_task.",
        "Voce pode agendar mensagens proativas com schedule_message (o numero do usuario ja esta configurado). SEJA PRECISO na data e hora. Para 'daqui X minutos', use minutes_from_now. Para datas especificas como 'amanha de manha', calcule o horario exato e use run_date em formato ISO 8601. Para recorrentes, use cron_expression.",
        "MUITO IMPORTANTE SOBRE AGENDAMENTOS: O campo 'task_instructions' dita o que o seu 'eu do futuro' fará na hora do disparo. Seja EXTREMAMENTE ESPECIFICO e diga quais tools ele deve chamar se for preciso descobrir algo na hora.",
        "Exemplo 1: Se o usuario pedir 'amanha de manha me avisa se saiu uma novidade', o task_instructions DEVE SER 'Buscar na internet com web_search se a novidade X saiu hoje e avisar o usuario'.",
        "Exemplo 2: Se o usuario pedir 'amanha me avisa minhas tarefas', o task_instructions DEVE SER 'Execute a tool de tarefas (list_tasks), veja as pendentes e mande um resumo para o usuario'.",
        "Use list_schedules para listar agendamentos e cancel_schedule para cancelar.",
        "Se voce receber uma mensagem com '[EXECUÇÃO DE LEMBRETE AGENDADO]', isso significa que voce esta EXECUTANDO um lembrete agendado. NAO peca mais informacoes, NAO tente agendar nada, NAO faca perguntas. Execute as instrucoes diretamente e envie o resultado pronto.",
        "Quando receber a instrucao de saudacao de nova sessao, consulte suas memorias ANTES de responder para saber quais informacoes o usuario quer no cumprimento.",
    ]
    
    if channel == "web":
        base_instructions.extend([
            "ATENCAO - CANAL DE VOZ ATIVO: Sua resposta sera lida em voz alta por um Sintetizador de Voz (TTS).",
            "REGRAS DE FORMATAÇÃO (CRÍTICO):",
            "1. ABANDONE completamente o uso de markdown (*, _, #). Nao use asteriscos.",
            "2. NUNCA use emojis.",
            "3. Nao crie listas longas ou formatadas. Use texto corrido e fluido.",
            "4. NUNCA fale horarios tecnicos como 'UTC' ou 'Universal Coordinated Time'. Converta SEMPRE para 'Horario de Brasilia' e escreva por extenso de forma natural (ex: '7 e 40 da manha' em vez de '07:40').",
            "5. Use pontuacao clara e pausas curtas (virgulas e pontos) para ditar o ritmo da respiracao e tornar a fala natural.",
            "Lembre-se: escreva exatamente como deve ser lido, pois símbolos de marcação (como asteriscos) serão lidos em voz alta pelo robô.",
        ])
    else:
        base_instructions.extend([
            "ATENCAO - CANAL DE TEXTO (WHATSAPP): Sua resposta sera lida em uma tela pequena.",
            "Use emojis com moderacao para dar tom e expressividade.",
            "Pode usar formatacao do WhatsApp (*negrito*, _italico_) e listas curtas para melhorar a escaneabilidade da mensagem.",
        ])
        
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
        instructions=base_instructions,
        tools=tools,
    )

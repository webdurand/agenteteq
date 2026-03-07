import os
from agno.agent import Agent
from agno.db.postgres import PostgresDb
from agno.db.sqlite import SqliteDb
from src.memory.knowledge import get_knowledge_base
from src.tools.memory_manager import create_memory_tools, list_memories
from src.tools.task_manager import create_task_tools, list_tasks

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
        model_id = os.getenv("LLM_MODEL", "gemini-2.5-flash")
        kwargs = {}
        if "2.5" in model_id:
            kwargs["thinking_budget"] = int(os.getenv("GEMINI_THINKING_BUDGET", "2048"))
            kwargs["include_thoughts"] = True
        # Google Search nativo (grounding): o modelo decide quando buscar na web e retorna citações
        if os.getenv("GEMINI_GOOGLE_SEARCH", "").lower() in ("1", "true", "yes"):
            kwargs["search"] = True  # Gemini 2.0+; para modelos antigos use grounding=True
        return Gemini(id=model_id, **kwargs)
    else:
        from agno.models.openai import OpenAIChat
        return OpenAIChat(id="gpt-4o-mini")

def get_assistant(session_id: str, extra_tools: list = None, channel: str = "whatsapp", extra_instructions: list = None) -> Agent:
    """
    Retorna a instancia do agente configurada para uma sessao especifica (ex: numero do WhatsApp).
    
    Args:
        session_id: Identificador da sessao (numero de WhatsApp do usuario).
        extra_tools: Tools adicionais injetadas pelo orchestrator (ex: web_search, deep_research).
                     Permite que o orchestrator injete contexto (notifier, user_id) sem acoplamento.
        channel: O canal de comunicacao (ex: "whatsapp", "web"). Influencia as instrucoes de formato.
        extra_instructions: Instrucoes adicionais injetadas pelo caller (ex: contexto de lembrete).
                            Sao adicionadas ao final das instrucoes base como instrucoes de sistema.
    """
    db_url = os.getenv("AGNO_DB_URL", "sqlite:///sessions.db")
    
    add_task_tool, list_tasks_tool, complete_task_tool, reopen_task_tool, delete_task_tool = create_task_tools(session_id, channel=channel)
    add_memory_tool, delete_memory_tool, list_memories_tool = create_memory_tools(session_id)

    def get_greeting_context() -> str:
        """
        Retorna contexto personalizado para inicio de sessao: memorias, tarefas
        pendentes e lembretes proximos. Use no inicio de uma nova conversa ou
        quando o usuario mandar uma saudacao (oi, bom dia, etc).
        """
        parts = []
        try:
            mem = list_memories(session_id)
            if mem and "Não há memórias" not in mem and "Erro" not in mem:
                parts.append(f"MEMORIAS:\n{mem}")
        except Exception:
            pass
        try:
            tasks = list_tasks(session_id, status="pending")
            if tasks and "Nenhuma tarefa" not in tasks and "Erro" not in tasks:
                parts.append(f"TAREFAS PENDENTES:\n{tasks}")
        except Exception:
            pass
        try:
            from src.models.reminders import list_user_reminders
            reminders = list_user_reminders(session_id, status="active").get("reminders", [])
            if reminders:
                lines = [f"- {r.get('title', r.get('task_instructions', '')[:50])}" for r in reminders[:5]]
                parts.append(f"LEMBRETES ATIVOS:\n" + "\n".join(lines))
        except Exception:
            pass
        if not parts:
            return "Nenhum contexto salvo para este usuario ainda."
        return "\n\n".join(parts)

    try:
        from src.tools.blog_publisher import create_blog_tools
        publish_post_tools = create_blog_tools(session_id, channel=channel)
        from src.tools.weather import get_weather
        from src.tools.scheduler_tool import create_scheduler_tools
        schedule_message, list_schedules, cancel_schedule = create_scheduler_tools(session_id, channel=channel)
        from src.tools.carousel_generator import create_carousel_tools
        generate_carousel, list_carousels = create_carousel_tools(session_id, channel=channel)
        from src.tools.image_editor import create_image_editor_tools
        edit_image = create_image_editor_tools(session_id, channel=channel)
        tools = [
            *publish_post_tools,
            add_memory_tool, delete_memory_tool, list_memories_tool,
            add_task_tool, list_tasks_tool, complete_task_tool, reopen_task_tool, delete_task_tool,
            get_weather,
            schedule_message, list_schedules, cancel_schedule,
            generate_carousel, list_carousels,
            edit_image,
            get_greeting_context,
        ]
    except ImportError as e:
        print(f"[ASSISTANT] Aviso: algumas tools nao carregaram ({e}). Usando conjunto basico.")
        tools = [add_memory_tool, delete_memory_tool, list_memories_tool, add_task_tool, list_tasks_tool, complete_task_tool, reopen_task_tool, delete_task_tool, get_greeting_context]
    
    if extra_tools:
        tools.extend(extra_tools)
        
    knowledge_base = get_knowledge_base()
    search_knowledge = os.getenv("MEMORY_MODE", "agentic").lower() == "agentic" and knowledge_base is not None
    
    db_url = os.getenv("DATABASE_URL", "")
    if db_url.startswith("postgresql://"):
        pg_url = db_url.replace("postgresql://", "postgresql+psycopg://")
        storage = PostgresDb(session_table="agent_sessions", db_url=pg_url)
    else:
        sqlite_url = os.getenv("AGNO_DB_URL", "sqlite:///sessions.db")
        if sqlite_url.startswith("libsql://") or sqlite_url.startswith("https://"):
            print("Aviso: Conexoes libsql remotas apresentam instabilidades com o ORM do Agno.")
            print("         Fazendo fallback para banco SQLite local em 'sessions.db' para garantir o funcionamento.")
            sqlite_url = "sqlite:///sessions.db"
        elif not sqlite_url.startswith("sqlite"):
            sqlite_url = f"sqlite:///{sqlite_url}"
        storage = SqliteDb(db_url=sqlite_url)
    
    base_instructions = [
        # Identidade
        "Voce e o Teq, um agente de inteligencia artificial criado por Pedro Durand. "
        "Voce e o assistente pessoal e parceiro de confianca do usuario, direto ao ponto e com bom humor. "
        "Se alguem perguntar quem voce e, diga que e o Teq, criado pelo Pedro Durand.",

        # Personalidade
        "Fale como um amigo proximo que por acaso e muito inteligente: linguagem informal, sem robotice, sem formalidade desnecessaria.",
        "Pode usar girias leves, contracoes do portugues falado ('to', 'ta', 'pra', 'ne', 'cara'), sem exagero.",
        "Seja conciso: sem enrolacao, sem repetir o que o usuario acabou de dizer, sem introducoes longas.",
        "Quando for direto ao ponto (tarefas, pesquisa, codigo), seja objetivo. Quando for conversa, seja descontraido.",
        "Se nao souber de algo, admita de boa — pode pesquisar ou pedir mais contexto sem drama.",
        "Nao precisa ficar repetindo o nome do usuario.",
        "O usuario pode te enviar textos ou audios. Responda sempre no mesmo tom da conversa.",

        # Regras de execucao
        "NUNCA narre o que voce vai fazer antes de fazer. Nao diga 'Deixa eu ver suas tarefas', 'Vou pesquisar isso', 'Deixa eu dar uma olhada'. Va direto ao resultado. Se precisar usar uma ferramenta, use silenciosamente e entregue a resposta pronta.",
        "Se uma ferramenta falhar ou retornar erro, corrija silenciosamente e tente de novo. NUNCA narre falhas, retentativas ou erros de ferramentas para o usuario. Responda APENAS com o resultado final, como se tivesse funcionado de primeira.",
        "Quando o prompt incluir [STATUS LIMITES], use essa informacao como verdade absoluta sobre limites e bypass. Ignore qualquer informacao de limites do historico anterior.",

        # Suas capacidades (para voce saber o que pode oferecer ao usuario)
        "CAPACIDADES COMPLETAS DO TEQ: "
        "1) MEMORIA: Voce aprende sobre o usuario ao longo do tempo. Salve preferencias, rotina, projetos e informacoes relevantes com add_memory. Use suas memorias para personalizar cada interacao. "
        "2) TAREFAS: Gerencia uma lista de tarefas completa — criar (add_task), listar (list_tasks), concluir (complete_task), reabrir (reopen_task) e excluir (delete_task). Quando o usuario mencionar algo que precisa fazer, faca perguntas contextuais antes de criar a tarefa. "
        "3) LEMBRETES E AGENDAMENTOS: Programa avisos para o futuro com schedule_message — pode ser unico (daqui X minutos), recorrente (cron) ou por intervalo. Antes de agendar, confirme o canal de aviso (web, WhatsApp ou ambos) quando o usuario nao informar. Liste com list_schedules e cancele com cancel_schedule. "
        "4) PESQUISA WEB: Busca informacoes atualizadas na internet com web_search. Para pesquisas mais profundas e detalhadas, usa deep_research que faz multiplas buscas e sintetiza os resultados. Apos pesquisas relevantes, salve os achados com add_memory. "
        "5) PREVISAO DO TEMPO: Consulta o clima de qualquer cidade com get_weather. "
        "6) BLOG: Publica posts no blog do usuario. Aguarde confirmacao explicita antes de publicar. "
        "7) GERACAO DE IMAGENS: Para gerar imagens NOVAS do zero (sem referencia), use generate_carousel com 1 slide e use_reference_image=False. "
        "Confirme o formato com o usuario ANTES de gerar. Se nao mencionar formato, sugira 1350x1080. "
        "SOMENTE use use_reference_image=True quando o usuario EXPLICITAMENTE enviar uma imagem junto com o pedido e pedir para usa-la como base/referencia. "
        "Se o usuario nao mencionou nenhuma imagem anterior e quer algo novo do zero, NUNCA ative use_reference_image. "
        "8) EDICAO DE IMAGENS: Edita e transforma imagens JA EXISTENTES com edit_image_tool. "
        "ATENCAO: so use edit_image_tool quando o usuario EXPLICITAMENTE pedir para editar/modificar/transformar uma imagem que ele enviou ou que foi gerada anteriormente. "
        "NUNCA use edit_image_tool para gerar imagens novas do zero — para isso use generate_carousel. "
        "Use source='original' para mudanca radical de estilo (ex: 'faz mais realista', 'muda o estilo totalmente'). "
        "Use source='last_generated' para ajustes incrementais (ex: 'muda o fundo', 'adiciona um chapeu'). "
        "Na duvida entre editar ou gerar nova, PREFIRA gerar nova com generate_carousel. A edicao acontece em background e o resultado e enviado automaticamente. "
        "9) VOZ: O usuario pode interagir por voz tanto no app web quanto pelo WhatsApp. "
        "10) WHATSAPP: Voce esta integrado ao WhatsApp do usuario, podendo enviar e receber mensagens, audios e imagens.",

        # Contexto de sessao
        "Em saudacao de nova sessao, use get_greeting_context para buscar o contexto antes de responder.",
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

    if extra_instructions:
        base_instructions.extend(extra_instructions)

    return Agent(
        name="Teq",
        model=get_model(),
        session_id=session_id,
        db=storage,
        knowledge=knowledge_base,
        search_knowledge=search_knowledge,
        add_datetime_to_context=True,
        add_history_to_context=True,
        num_history_runs=5,
        markdown=True,
        instructions=base_instructions,
        tools=tools,
    )

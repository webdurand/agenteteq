import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from agno.agent import Agent

logger = logging.getLogger(__name__)
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

def get_assistant(session_id: str, extra_tools: list = None, channel: str = "whatsapp", extra_instructions: list = None, include_scheduler: bool = True) -> Agent:
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
        scheduler_tools = []
        if include_scheduler:
            from src.tools.scheduler_tool import create_scheduler_tools
            schedule_message, list_schedules, cancel_schedule = create_scheduler_tools(session_id, channel=channel)
            scheduler_tools = [schedule_message, list_schedules, cancel_schedule]
        from src.tools.carousel_generator import create_carousel_tools
        generate_carousel, list_carousels = create_carousel_tools(session_id, channel=channel)
        from src.tools.image_editor import create_image_editor_tools
        edit_image = create_image_editor_tools(session_id, channel=channel)
        from src.tools.channel_delivery import create_send_to_channel_tool
        send_to_channel = create_send_to_channel_tool(session_id)
        from src.tools.workflow_tool import create_workflow_tools
        workflow_tools = []
        if include_scheduler:
            run_workflow, schedule_workflow = create_workflow_tools(session_id, channel=channel)
            workflow_tools = [run_workflow, schedule_workflow]
        tools = [
            *publish_post_tools,
            add_memory_tool, delete_memory_tool, list_memories_tool,
            add_task_tool, list_tasks_tool, complete_task_tool, reopen_task_tool, delete_task_tool,
            get_weather,
            *scheduler_tools,
            *workflow_tools,
            generate_carousel, list_carousels,
            edit_image,
            send_to_channel,
            get_greeting_context,
        ]
    except ImportError as e:
        logger.warning("Aviso: algumas tools nao carregaram (%s). Usando conjunto basico.", e)
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
            logger.warning("Aviso: Conexoes libsql remotas apresentam instabilidades com o ORM do Agno.")
            logger.info("         Fazendo fallback para banco SQLite local em 'sessions.db' para garantir o funcionamento.")
            sqlite_url = "sqlite:///sessions.db"
        elif not sqlite_url.startswith("sqlite"):
            sqlite_url = f"sqlite:///{sqlite_url}"
        storage = SqliteDb(db_url=sqlite_url)
    
    # Injetar data/hora atual no fuso de Brasilia com dia da semana
    _dias_semana = ["segunda-feira", "terca-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sabado", "domingo"]
    _now_br = datetime.now(ZoneInfo("America/Sao_Paulo"))
    _dia_semana = _dias_semana[_now_br.weekday()]
    _datetime_str = _now_br.strftime(f"%d/%m/%Y ({_dia_semana}), %H:%M")

    base_instructions = [
        # Data e hora atual
        f"DATA E HORA ATUAL (Horario de Brasilia): {_datetime_str}. "
        "Use SEMPRE este horario como referencia. Todos os usuarios estao no fuso de Brasilia (UTC-3). "
        "Quando o usuario mencionar dias da semana (ex: 'terca', 'quinta'), use a data atual acima para calcular corretamente.",

        # Identidade
        "Voce e o Teq, um agente de inteligencia artificial criado por Pedro Durand. "
        "Voce e o assistente pessoal e parceiro de confianca do usuario, direto ao ponto e com bom humor. "
        "Se alguem perguntar quem voce e, diga que e o Teq, criado pelo Pedro Durand.",

        # Tom
        "Fale como um amigo proximo que por acaso e muito inteligente: linguagem informal, concisa, sem robotice. "
        "Pode usar girias leves ('to', 'ta', 'pra', 'ne'), seja objetivo em tarefas e descontraido em conversa. "
        "Se nao souber de algo, admita de boa. O usuario pode enviar textos ou audios.",

        # Regras de execucao
        "NUNCA narre o que voce vai fazer antes de fazer. Va direto ao resultado. Use ferramentas silenciosamente e entregue a resposta pronta.",
        "Se uma ferramenta falhar, corrija silenciosamente. NUNCA narre falhas ou retentativas. Responda APENAS com o resultado final.",
        "Quando o prompt incluir [STATUS LIMITES], use essa informacao como verdade absoluta sobre limites e bypass. Ignore qualquer informacao de limites do historico anterior.",

        # Tools disponiveis (detalhes nos docstrings de cada tool)
        "Voce tem tools para: memoria de longo prazo, tarefas, agendamentos/lembretes, pesquisa web, pesquisa aprofundada, previsao do tempo, geracao de imagens, edicao de imagens, publicacao no blog, interacao por voz/WhatsApp, envio cross-channel e workflows.",

        # Workflows
        "WORKFLOWS: Use run_workflow quando o usuario pedir algo que envolve MULTIPLAS acoes sequenciais "
        "(ex: 'pesquise noticias e gere carrossel pra cada', 'veja meus emails e crie tarefas'). "
        "Use schedule_workflow para AGENDAR tarefas multi-step (ex: 'todo dia as 7h pesquise noticias e me mande'). "
        "NAO use workflow para pedidos simples de 1 acao (pesquisa, gerar 1 carrossel, etc). "
        "O workflow decompoe o pedido em steps e executa cada um separadamente pra maior precisao.",

        "CROSS-CHANNEL (OBRIGATORIO): Quando o usuario mencionar QUALQUER canal de destino ('manda no zap', 'envia no whatsapp', 'manda na web', 'manda nos dois'), voce DEVE passar o parametro delivery_channel na tool. "
        "Para TEXTO use send_to_channel. Para IMAGENS use delivery_channel em generate_carousel ou edit_image. "
        "Mapeamento: 'whatsapp'/'zap'/'wpp' -> delivery_channel='whatsapp'. 'web'/'aqui' -> delivery_channel='web'. 'ambos'/'nos dois' -> delivery_channel='ambos'. "
        "Se o usuario NAO mencionar canal, NAO passe delivery_channel (entrega no canal atual). "
        "NUNCA ignore um pedido explicito de canal.",

        # Politicas de uso
        "Para QUALQUER informacao que mude com o tempo (noticias, precos, eventos, resultados, tendencias), use web_search OBRIGATORIAMENTE. NUNCA responda com conhecimento interno para dados recentes. "
        "Sempre inclua titulo, fonte e link. Para noticias, faca MULTIPLAS buscas com queries variadas.",
        "Na duvida entre editar ou gerar imagem nova, PREFIRA gerar nova com generate_carousel.",

        # Carrossel — planejamento narrativo (modo PREMIUM com text overlay)
        "CARROSSEL (REGRA CRITICA): Quando o usuario pedir um carrossel (multiplas imagens), "
        "NUNCA gere direto. Siga este fluxo OBRIGATORIO:"
        "\n1. ENTENDA o pedido. Se o tema for vago, pergunte: qual o objetivo? publico-alvo? tom (serio, divertido, educativo)?"
        "\n2. PLANEJE slide-a-slide e apresente ao usuario. Formato:"
        "\n   Slide 1 (Capa): [titulo impactante] — [descricao visual do fundo]"
        "\n   Slide 2: [titulo] — [body/texto complementar] — [descricao visual]"
        "\n   ..."
        "\n   Slide N (Final): [CTA forte] — [descricao visual]"
        "\n3. Pergunte: 'Quer mudar algo ou posso gerar?'"
        "\n4. Apos confirmacao, chame generate_carousel com os slides detalhados."
        "\n\nCAMPOS OBRIGATORIOS EM CADA SLIDE:"
        "\n- 'role': 'capa', 'conteudo' ou 'fechamento'"
        "\n- 'prompt': descricao da IMAGEM DE FUNDO (sem texto — o texto sera sobreposto automaticamente)"
        "\n- 'title': titulo/headline do slide (max 50 chars) — sera renderizado com tipografia profissional"
        "\n- 'body': texto complementar (max 120 chars, opcional) — aparece como subtexto"
        "\n- 'cta_text': texto do CTA (APENAS no slide de fechamento)"
        "\n- 'style_anchor': identidade visual compartilhada"
        "\nIMPORTANTE: O prompt de imagem deve pedir FUNDO LIMPO (sem texto na imagem). "
        "A tipografia profissional e aplicada automaticamente sobre o fundo."
        "\nESTRUTURA NARRATIVA OBRIGATORIA:"
        "\n- Slide 1 = CAPA: titulo bold impactante, fundo visual forte"
        "\n- Slides do meio = CONTEUDO: cada slide entrega 1 ponto de valor com titulo + body"
        "\n- Ultimo slide = FECHAMENTO: CTA forte que gera engajamento"
        "\nUse sequential_slides=True (padrao) para manter coerencia visual. "
        "Use False APENAS para colecoes independentes (ex: '10 logos diferentes').",
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
        add_history_to_context=True,
        num_history_runs=5,
        markdown=True,
        instructions=base_instructions,
        tools=tools,
    )

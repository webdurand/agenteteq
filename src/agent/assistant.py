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

def get_assistant(session_id: str, extra_tools: list = None, channel: str = "whatsapp", extra_instructions: list = None, include_scheduler: bool = True, include_knowledge: bool = True, user_id: str = None) -> Agent:
    """
    Retorna a instancia do agente configurada para uma sessao especifica.

    Args:
        session_id: Identificador da sessao Agno (pode ser rotacionado).
        extra_tools: Tools adicionais injetadas pelo orchestrator.
        channel: O canal de comunicacao (ex: "whatsapp", "web").
        extra_instructions: Instrucoes adicionais injetadas pelo caller.
        include_scheduler: Se True, inclui tools de agendamento.
        include_knowledge: Se True, inclui knowledge base.
        user_id: ID permanente do usuario (phone). Se None, usa session_id.
    """
    uid = user_id or session_id  # permanent user id for tools/data
    db_url = os.getenv("AGNO_DB_URL", "sqlite:///sessions.db")

    add_task_tool, list_tasks_tool, complete_task_tool, reopen_task_tool, delete_task_tool = create_task_tools(uid, channel=channel)
    add_memory_tool, delete_memory_tool, list_memories_tool = create_memory_tools(uid)

    def get_greeting_context() -> str:
        """
        Retorna contexto personalizado para inicio de sessao: memorias, tarefas
        pendentes e lembretes proximos. Use no inicio de uma nova conversa ou
        quando o usuario mandar uma saudacao (oi, bom dia, etc).
        """
        parts = []
        try:
            mem = list_memories(uid)
            if mem and "Não há memórias" not in mem and "Erro" not in mem:
                parts.append(f"MEMORIAS:\n{mem}")
        except Exception:
            pass
        try:
            tasks = list_tasks(uid, status="pending")
            if tasks and "Nenhuma tarefa" not in tasks and "Erro" not in tasks:
                parts.append(f"TAREFAS PENDENTES:\n{tasks}")
        except Exception:
            pass
        try:
            from src.models.reminders import list_user_reminders
            reminders = list_user_reminders(uid, status="active").get("reminders", [])
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
        publish_post_tools = create_blog_tools(uid, channel=channel)
        from src.tools.weather import get_weather
        scheduler_tools = []
        if include_scheduler:
            from src.tools.scheduler_tool import create_scheduler_tools
            schedule_message, list_schedules, cancel_schedule = create_scheduler_tools(uid, channel=channel)
            scheduler_tools = [schedule_message, list_schedules, cancel_schedule]
        from src.tools.image_generator import create_image_tools
        generate_image, list_gallery = create_image_tools(uid, channel=channel)
        from src.tools.channel_delivery import create_send_to_channel_tool
        send_to_channel = create_send_to_channel_tool(uid)
        from src.tools.workflow_tool import create_workflow_tools
        workflow_tools = []
        if include_scheduler:
            run_workflow, schedule_workflow = create_workflow_tools(uid, channel=channel)
            workflow_tools = [run_workflow, schedule_workflow]
        tools = [
            *publish_post_tools,
            add_memory_tool, delete_memory_tool, list_memories_tool,
            add_task_tool, list_tasks_tool, complete_task_tool, reopen_task_tool, delete_task_tool,
            get_weather,
            *scheduler_tools,
            *workflow_tools,
            generate_image, list_gallery,
            send_to_channel,
            get_greeting_context,
        ]
    except ImportError as e:
        logger.warning("Aviso: algumas tools nao carregaram (%s). Usando conjunto basico.", e)
        tools = [add_memory_tool, delete_memory_tool, list_memories_tool, add_task_tool, list_tasks_tool, complete_task_tool, reopen_task_tool, delete_task_tool, get_greeting_context]
    
    if extra_tools:
        tools.extend(extra_tools)
        
    knowledge_base = get_knowledge_base() if include_knowledge else None
    search_knowledge = include_knowledge and os.getenv("MEMORY_MODE", "agentic").lower() == "agentic" and knowledge_base is not None
    
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
        "Voce tem tools para: memoria de longo prazo, tarefas, agendamentos/lembretes, pesquisa web, pesquisa aprofundada, previsao do tempo, geracao/edicao de imagens (generate_image), publicacao no blog, interacao por voz/WhatsApp, envio cross-channel e workflows.",

        # Workflows
        "WORKFLOWS: Use run_workflow quando o usuario pedir algo que envolve MULTIPLAS acoes sequenciais "
        "(ex: 'pesquise noticias e gere carrossel pra cada', 'veja meus emails e crie tarefas'). "
        "Use schedule_workflow para AGENDAR tarefas multi-step (ex: 'todo dia as 7h pesquise noticias e me mande'). "
        "NAO use workflow para pedidos simples de 1 acao (pesquisa, gerar 1 carrossel, etc). "
        "O workflow decompoe o pedido em steps e executa cada um separadamente pra maior precisao.",

        "CROSS-CHANNEL (OBRIGATORIO): Quando o usuario mencionar QUALQUER canal de destino ('manda no zap', 'envia no whatsapp', 'manda na web', 'manda nos dois'), voce DEVE passar o parametro delivery_channel na tool. "
        "Para TEXTO use send_to_channel. Para IMAGENS use delivery_channel em generate_image. "
        "Mapeamento: 'whatsapp'/'zap'/'wpp' -> delivery_channel='whatsapp'. 'web'/'aqui' -> delivery_channel='web'. 'ambos'/'nos dois' -> delivery_channel='ambos'. "
        "Se o usuario NAO mencionar canal, NAO passe delivery_channel (entrega no canal atual). "
        "NUNCA ignore um pedido explicito de canal. "
        "REGRA CRITICA: NUNCA sugira ou decida por conta propria entregar em outro canal. "
        "Se a conversa esta no WhatsApp, o padrao e SEMPRE entregar no WhatsApp. "
        "So mude o canal se o USUARIO pedir explicitamente (ex: 'manda na web', 'envia no app'). "
        "NUNCA diga 'vou mandar na web' por iniciativa propria.",

        # Politicas de uso
        "Para QUALQUER informacao que mude com o tempo (noticias, precos, eventos, resultados, tendencias), use web_search OBRIGATORIAMENTE. NUNCA responda com conhecimento interno para dados recentes. "
        "Sempre inclua titulo, fonte e link. Para noticias, faca MULTIPLAS buscas com queries variadas.",
        # Geração de imagens (unificada: imagem unica, carrossel, edição)
        "GERACAO DE IMAGENS (generate_image): Use generate_image para TUDO: imagem unica, carrossel e edicao de foto."
        "\n- Para IMAGEM UNICA: Gere direto com 1 slide."
        "\n- Para EDITAR foto existente: Use reference_source='original' (recriacao de estilo) ou 'last_generated' (ajuste incremental)."
        "\n- Para CARROSSEL (2+ slides): OBRIGATORIO planejar antes. Siga este fluxo:"
        "\n  1. ENTENDA o pedido. Se vago, pergunte: objetivo, publico-alvo, tom."
        "\n  2. PLANEJE slide-a-slide e apresente ao usuario:"
        "\n     Slide 1 (Capa): [titulo impactante] — [descricao visual]"
        "\n     Slide 2: [titulo] — [body] — [descricao visual]"
        "\n     ..."
        "\n     Slide N (Final): [CTA forte] — [descricao visual]"
        "\n  3. Pergunte: 'Quer mudar algo ou posso gerar?'"
        "\n  4. Apos confirmacao, chame generate_image com todos os slides."
        "\n\nCAMPOS DO SLIDE:"
        "\n- 'role': 'capa', 'conteudo' ou 'fechamento'"
        "\n- 'prompt': descricao da IMAGEM DE FUNDO (sem texto — tipografia e aplicada automaticamente)"
        "\n- 'title': titulo/headline (max 50 chars) — sobreposto com tipografia profissional"
        "\n- 'body': texto complementar (max 120 chars, opcional)"
        "\n- 'cta_text': texto do CTA (APENAS no fechamento)"
        "\n- 'style_anchor': identidade visual compartilhada"
        "\nOVERLAY DE TEXTO: automatico quando slide tem title/body/cta_text. Omita esses campos para imagem pura sem texto."
        "\nIMPORTANTE: O prompt deve pedir FUNDO LIMPO quando houver overlay."
        "\nREGRA: SEMPRE 1 unica chamada para o mesmo pedido. '3 imagens' = 1 chamada com 3 slides. NUNCA crie multiplas chamadas separadas."
        "\nUse sequential_slides=True (padrao) para coerencia visual. False APENAS para colecoes independentes.",
        "Em saudacao de nova sessao, use get_greeting_context para buscar o contexto antes de responder.",

        # Dados em tempo real
        "DADOS EM TEMPO REAL: NUNCA responda sobre tarefas, lembretes ou memorias usando informacoes do historico de conversa. "
        "SEMPRE chame a tool correspondente (list_tasks_tool, list_memories_tool, list_schedules) para obter dados atualizados.",
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

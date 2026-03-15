import asyncio

# --- Tool Declarations for Gemini Live API ---
VOICE_TOOLS_DECLARATIONS = [
    {
        "name": "add_task",
        "description": "Adiciona uma tarefa a lista do usuario.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Titulo curto e descritivo da tarefa"},
                "description": {"type": "string", "description": "Descricao mais detalhada (opcional)"},
                "due_date": {"type": "string", "description": "Prazo ou data/hora (opcional)"},
                "location": {"type": "string", "description": "Endereco ou local (opcional)"},
                "notes": {"type": "string", "description": "Observacoes (opcional)"}
            },
            "required": ["title"]
        }
    },
    {
        "name": "list_tasks",
        "description": "Lista as tarefas do usuario.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filtro de status: 'pending', 'done', ou 'all'. Padrao: 'pending'"}
            }
        }
    },
    {
        "name": "complete_task",
        "description": "Marca uma tarefa como concluida.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "ID numerico da tarefa"}
            },
            "required": ["task_id"]
        }
    },
    {
        "name": "reopen_task",
        "description": "Marca uma tarefa como pendente.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "ID numerico da tarefa"}
            },
            "required": ["task_id"]
        }
    },
    {
        "name": "delete_task",
        "description": "Remove uma tarefa da lista.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "ID numerico da tarefa"}
            },
            "required": ["task_id"]
        }
    },
    {
        "name": "get_weather",
        "description": "Retorna a previsao do tempo atual para a cidade.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "Nome da cidade (ex: Sao Paulo)"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "add_memory",
        "description": "Adiciona um fato a memoria de longo prazo do usuario.",
        "parameters": {
            "type": "object",
            "properties": {
                "fato": {"type": "string", "description": "O fato a ser memorizado"}
            },
            "required": ["fato"]
        }
    },
    {
        "name": "delete_memory",
        "description": "Remove uma memoria do usuario baseada em uma query.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A busca para achar a memoria a deletar"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "list_memories",
        "description": "Lista todos os fatos memorizados sobre o usuario.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "schedule_message",
        "description": "Agenda uma mensagem proativa ou lembrete.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_instructions": {"type": "string", "description": "Instrucoes completas do que fazer na hora do disparo"},
                "trigger_type": {"type": "string", "description": "'date', 'cron' ou 'interval'"},
                "minutes_from_now": {"type": "integer", "description": "Daqui a quantos minutos (para trigger_type='date')"},
                "run_date": {"type": "string", "description": "Data ISO 8601 (para trigger_type='date' se nao usar minutes)"},
                "cron_expression": {"type": "string", "description": "Expressao cron (para trigger_type='cron')"},
                "interval_minutes": {"type": "integer", "description": "A cada X minutos (para trigger_type='interval')"},
                "title": {"type": "string", "description": "Titulo curto do agendamento"},
                "notification_channel": {"type": "string", "description": "'whatsapp_text' ou 'web_voice'"}
            },
            "required": ["task_instructions", "trigger_type"]
        }
    },
    {
        "name": "list_schedules",
        "description": "Lista os agendamentos ativos do usuario.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "cancel_schedule",
        "description": "Cancela um agendamento.",
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "O ID do agendamento"}
            },
            "required": ["job_id"]
        }
    },
    {
        "name": "web_search",
        "description": "Pesquisa na internet sobre qualquer assunto. Use topic='news' para noticias recentes dos ultimos N dias.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Consulta de busca"},
                "max_results": {"type": "integer", "description": "Numero maximo de resultados (padrao 5)"},
                "topic": {"type": "string", "description": "Tipo de busca: 'general' (padrao) ou 'news' para noticias recentes"},
                "days": {"type": "integer", "description": "Para topic='news', numero de dias a considerar (padrao 3)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "publish_post",
        "description": "Publica um post no blog do usuario.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Titulo do post"},
                "content": {"type": "string", "description": "Conteudo em markdown/MDX"}
            },
            "required": ["title", "content"]
        }
    },
    {
        "name": "generate_image",
        "description": "Gera ou edita imagens em background. Para imagem unica, passe 1 slide. Para carrossel, passe N slides. Para editar foto, preencha reference_source.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Titulo curto da geracao"},
                "description": {"type": "string", "description": "Descricao do que gerar, ex: 'paisagens brasileiras variadas', '10 gatos em estilo anime'. Para edicao, descreva a modificacao."},
                "num_slides": {"type": "integer", "description": "Quantidade de imagens/slides a gerar (padrao 1 para edicao, 5 para carrossel)"},
                "style": {"type": "string", "description": "Estilo visual, ex: Fotorrealista, Cinematico, Clean/Mockup, Anime, Aquarela"},
                "format": {"type": "string", "description": "Formato da imagem, ex: 1350x1080, 1080x1080, 16:9, 1:1"},
                "reference_source": {"type": "string", "description": "Para EDICAO de foto: 'original' (foto do usuario), 'last_generated' (ultima gerada), 'auto'. Vazio para gerar do zero."},
                "sequential_slides": {"type": "boolean", "description": "Se True (padrao), gera slide 1 como referencia visual para os demais. Use False para colecoes independentes."},
                "delivery_channel": {"type": "string", "description": "OBRIGATORIO quando o usuario mencionar WhatsApp/zap/wpp como destino. Valores: 'whatsapp', 'web', 'ambos'. Se nao informado, entrega no canal atual."}
            },
            "required": ["title", "description"]
        }
    },
    {
        "name": "list_gallery",
        "description": "Lista as imagens e carrosseis ja gerados pelo usuario.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "send_to_channel",
        "description": "Envia uma mensagem de texto para outro canal do usuario (WhatsApp, web ou ambos). Use quando o usuario pedir explicitamente para enviar algo em outro canal.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "O texto completo a ser enviado no canal de destino"},
                "channel": {"type": "string", "description": "Canal de destino: 'whatsapp' (ou 'wpp', 'zap'), 'web', ou 'ambos'"}
            },
            "required": ["message", "channel"]
        }
    },
    {
        "name": "run_workflow",
        "description": "Executa uma tarefa multi-step AGORA. Use quando o usuario pedir algo que envolve MULTIPLAS acoes sequenciais (ex: 'pesquise noticias e gere carrossel pra cada'). NAO use para pedidos simples de 1 acao. IMPORTANTE: chame esta tool diretamente SEM narrar ou comentar a complexidade. Nao diga 'que complexo' nem narre o que vai fazer. Apenas execute.",
        "parameters": {
            "type": "object",
            "properties": {
                "request": {"type": "string", "description": "O pedido completo do usuario em linguagem natural"}
            },
            "required": ["request"]
        }
    },
    {
        "name": "schedule_workflow",
        "description": "Agenda a execucao de uma tarefa complexa multi-step para o futuro. Use quando o usuario quer AGENDAR algo que envolve multiplas acoes (ex: 'todo dia as 7h pesquise noticias e me mande').",
        "parameters": {
            "type": "object",
            "properties": {
                "request": {"type": "string", "description": "O pedido completo do usuario (o que fazer quando disparar)"},
                "trigger_type": {"type": "string", "description": "'date' (unico), 'cron' (recorrente), 'interval'"},
                "minutes_from_now": {"type": "integer", "description": "Minutos a partir de agora (para date)"},
                "run_date": {"type": "string", "description": "Data/hora ISO 8601 (alternativo a minutes_from_now)"},
                "cron_expression": {"type": "string", "description": "Expressao cron de 5 campos (para cron)"},
                "interval_minutes": {"type": "integer", "description": "Intervalo em minutos (para interval)"},
                "title": {"type": "string", "description": "Titulo curto do agendamento"},
                "notification_channel": {"type": "string", "description": "Canal de entrega: 'whatsapp', 'web', 'ambos', 'web_voice'"}
            },
            "required": ["request"]
        }
    },
    # --- Social Monitoring ---
    {
        "name": "preview_account",
        "description": "Ver o perfil e conteudo recente de uma conta de rede social SEM salvar. Use ANTES de track_account.",
        "parameters": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "description": "Plataforma (instagram, youtube)"},
                "username": {"type": "string", "description": "Username da conta (ex: natgeo, @natgeo)"}
            },
            "required": ["platform", "username"]
        }
    },
    {
        "name": "track_account",
        "description": "Salvar uma conta de rede social para monitoramento continuo. Use DEPOIS de preview_account.",
        "parameters": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "description": "Plataforma (instagram, youtube)"},
                "username": {"type": "string", "description": "Username da conta"}
            },
            "required": ["platform", "username"]
        }
    },
    {
        "name": "untrack_account",
        "description": "Parar de monitorar uma conta de rede social.",
        "parameters": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "description": "Plataforma (instagram, youtube)"},
                "username": {"type": "string", "description": "Username da conta"}
            },
            "required": ["username"]
        }
    },
    {
        "name": "list_tracked_accounts",
        "description": "Listar todas as contas de redes sociais que estao sendo monitoradas.",
        "parameters": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "description": "Filtrar por plataforma (opcional)"}
            }
        }
    },
    {
        "name": "get_account_insights",
        "description": "Analisa o conteudo recente de uma conta monitorada. Retorna insights sobre topicos, engajamento e tendencias.",
        "parameters": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "description": "Plataforma (instagram, youtube)"},
                "username": {"type": "string", "description": "Username da conta monitorada"}
            },
            "required": ["username"]
        }
    },
    {
        "name": "get_trending_content",
        "description": "Mostra os conteudos com mais engajamento de uma conta monitorada.",
        "parameters": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "description": "Plataforma (instagram, youtube)"},
                "username": {"type": "string", "description": "Username da conta"}
            },
            "required": ["username"]
        }
    },
    {
        "name": "analyze_posts",
        "description": "Olha para os posts de uma conta (incluindo as imagens) e responde perguntas. Funciona com qualquer conta publica, mesmo nao monitorada.",
        "parameters": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "description": "Plataforma (instagram, youtube)"},
                "username": {"type": "string", "description": "Username da conta"},
                "sort": {"type": "string", "description": "Ordenacao: 'recent' ou 'top'. Padrao: recent"},
                "limit": {"type": "integer", "description": "Quantidade de posts (1 a 5). Padrao: 3"},
                "question": {"type": "string", "description": "Pergunta especifica sobre os posts (opcional)"}
            },
            "required": ["username"]
        }
    },
    {
        "name": "create_content_script",
        "description": "Cria um roteiro de conteudo (carousel, video, reels) inspirado nas melhores referencias de uma conta monitorada.",
        "parameters": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "description": "Plataforma (instagram, youtube)"},
                "reference_username": {"type": "string", "description": "Username da conta de referencia"},
                "content_type": {"type": "string", "description": "Tipo: carousel, video, reels. Padrao: carousel"},
                "topic": {"type": "string", "description": "Tema especifico (opcional)"}
            },
            "required": ["reference_username"]
        }
    },
    {
        "name": "toggle_alerts",
        "description": "Ativa ou desativa alertas proativos para uma conta monitorada. Quando ativo, notifica no WhatsApp quando a conta postar algo que bombar.",
        "parameters": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "description": "Plataforma (instagram, youtube)"},
                "username": {"type": "string", "description": "Username da conta monitorada"},
                "enabled": {"type": "boolean", "description": "True para ativar, False para desativar"}
            },
            "required": ["username", "enabled"]
        }
    },
    # --- Research & Web ---
    {
        "name": "fetch_page",
        "description": "Le e extrai o conteudo completo de uma pagina web a partir de uma URL. Use para detalhar o conteudo de um link encontrado em busca.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL completa da pagina a ler"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "deep_research",
        "description": "Pesquisa aprofundada e detalhada sobre um tema na internet. Usa multiplas fontes e perspectivas. Para buscas rapidas, prefira web_search.",
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Tema da pesquisa aprofundada"}
            },
            "required": ["topic"]
        }
    },
    # --- Carousel Presets ---
    {
        "name": "save_carousel_preset",
        "description": "Salva um preset/template de estilo para carrosseis. Se ja existir com o mesmo nome, atualiza. Use quando o usuario gostar de um estilo e quiser reutilizar.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nome do preset (ex: Meu Estilo Escuro, Clean Minimal)"},
                "style_anchor": {"type": "string", "description": "Descricao do estilo visual (ex: Fundo escuro, tipografia moderna)"},
                "primary_color": {"type": "string", "description": "Cor de fundo principal hex"},
                "accent_color": {"type": "string", "description": "Cor de destaque hex"},
                "text_primary_color": {"type": "string", "description": "Cor do texto principal hex"},
                "text_secondary_color": {"type": "string", "description": "Cor do texto secundario hex"},
                "default_format": {"type": "string", "description": "Formato padrao (ex: 1350x1080)"},
                "default_slide_count": {"type": "integer", "description": "Numero padrao de slides"},
                "sequential_slides": {"type": "boolean", "description": "Se True, slides com coerencia visual"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "list_carousel_presets",
        "description": "Lista todos os presets/templates de carrossel salvos pelo usuario.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    # --- Branding ---
    {
        "name": "get_brand_profile",
        "description": "Busca o perfil de marca/identidade visual do usuario. Retorna cores, fontes, logo e estilo configurados. Use antes de gerar carrosseis.",
        "parameters": {
            "type": "object",
            "properties": {
                "profile_name": {"type": "string", "description": "Nome do perfil. Vazio = perfil padrao."}
            }
        }
    },
    {
        "name": "update_brand_profile",
        "description": "Cria ou atualiza perfil de marca do usuario com cores, fontes, estilo e tom de voz.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nome da marca/perfil"},
                "primary_color": {"type": "string", "description": "Cor primaria hex"},
                "accent_color": {"type": "string", "description": "Cor accent hex"},
                "bg_color": {"type": "string", "description": "Cor de fundo hex"},
                "text_primary_color": {"type": "string", "description": "Cor do texto principal hex"},
                "font_heading": {"type": "string", "description": "Fonte para titulos"},
                "font_body": {"type": "string", "description": "Fonte para corpo"},
                "style_description": {"type": "string", "description": "Descricao do estilo visual"},
                "tone_of_voice": {"type": "string", "description": "Tom de comunicacao"},
                "target_audience": {"type": "string", "description": "Publico-alvo"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "list_brand_profiles",
        "description": "Lista todos os perfis de marca/identidade visual do usuario.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    }
]

# --- Conditional tool declarations (added dynamically based on user integrations) ---

VOICE_GMAIL_DECLARATIONS = [
    {
        "name": "read_emails",
        "description": "Le e-mails do Gmail do usuario. Retorna assunto, remetente e resumo.",
        "parameters": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "description": "Maximo de emails (padrao 10)"},
                "query": {"type": "string", "description": "Query Gmail (ex: is:unread, from:pessoa@email.com, newer_than:1d). Padrao: is:unread"}
            }
        }
    },
]

VOICE_CALENDAR_DECLARATIONS = [
    {
        "name": "get_calendar_events",
        "description": "Busca os proximos eventos na agenda do Google do usuario.",
        "parameters": {
            "type": "object",
            "properties": {
                "time_min": {"type": "string", "description": "Data/hora inicial ISO 8601 (opcional, padrao: agora)"},
                "time_max": {"type": "string", "description": "Data/hora final ISO 8601 (opcional)"},
                "max_results": {"type": "integer", "description": "Maximo de eventos (padrao 10)"}
            }
        }
    },
    {
        "name": "create_calendar_event",
        "description": "Cria um novo evento na agenda do Google do usuario.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Titulo do evento"},
                "start_time": {"type": "string", "description": "Data/hora inicio ISO 8601 (ex: 2026-03-08T10:00:00-03:00)"},
                "end_time": {"type": "string", "description": "Data/hora termino ISO 8601"},
                "description": {"type": "string", "description": "Descricao do evento (opcional)"},
                "location": {"type": "string", "description": "Local do evento (opcional)"}
            },
            "required": ["summary", "start_time", "end_time"]
        }
    },
]


def get_voice_tools_for_user(user_id: str) -> list[dict]:
    """Build the full voice tools list, adding conditional tools based on user integrations."""
    tools = list(VOICE_TOOLS_DECLARATIONS)

    try:
        from src.memory.integrations import get_user_integrations

        if get_user_integrations(user_id, provider="gmail"):
            tools.extend(VOICE_GMAIL_DECLARATIONS)
        if get_user_integrations(user_id, provider="google_calendar"):
            tools.extend(VOICE_CALENDAR_DECLARATIONS)
    except Exception:
        pass

    return tools


# --- Dispatcher ---
async def execute_voice_tool(user_id: str, function_name: str, args: dict) -> dict:
    """
    Executa a tool nativa do backend e retorna o resultado para ser devolvido ao Gemini Live API.
    A execução em si roda em uma thread para não bloquear o asyncio loop (já que algumas das tools são síncronas).
    """
    def _run_sync():
        try:
            if function_name == "add_task":
                from src.tools.task_manager import add_task
                return {"result": add_task(user_id, **args)}
            
            elif function_name == "list_tasks":
                from src.tools.task_manager import list_tasks
                return {"result": list_tasks(user_id, **args)}
                
            elif function_name == "complete_task":
                from src.tools.task_manager import complete_task
                return {"result": complete_task(user_id, **args)}
                
            elif function_name == "reopen_task":
                from src.tools.task_manager import reopen_task
                return {"result": reopen_task(user_id, **args)}
                
            elif function_name == "delete_task":
                from src.tools.task_manager import delete_task
                return {"result": delete_task(user_id, **args)}
                
            elif function_name == "get_weather":
                from src.tools.weather import get_weather
                return {"result": get_weather(**args)}
                
            elif function_name == "add_memory":
                from src.tools.memory_manager import add_memory
                return {"result": add_memory(args.get("fato"), user_id)}
                
            elif function_name == "delete_memory":
                from src.tools.memory_manager import delete_memory
                return {"result": delete_memory(args.get("query"), user_id)}
                
            elif function_name == "list_memories":
                from src.tools.memory_manager import list_memories
                return {"result": list_memories(user_id)}
                
            elif function_name == "schedule_message":
                from src.tools.scheduler_tool import create_scheduler_tools
                tools = create_scheduler_tools(user_id, channel="web_live")
                schedule_func = tools[0]
                return {"result": schedule_func(**args)}
                
            elif function_name == "list_schedules":
                from src.tools.scheduler_tool import create_scheduler_tools
                tools = create_scheduler_tools(user_id, channel="web_live")
                list_func = tools[1]
                return {"result": list_func(**args)}
                
            elif function_name == "cancel_schedule":
                from src.tools.scheduler_tool import create_scheduler_tools
                tools = create_scheduler_tools(user_id, channel="web_live")
                cancel_func = tools[2]
                return {"result": cancel_func(**args)}

            elif function_name == "web_search":
                from src.tools.web_search import web_search_raw
                query = args.get("query", "")
                max_results = int(args.get("max_results", 5) or 5)
                topic = args.get("topic", "general")
                days = int(args.get("days", 3) or 3)
                return {"result": web_search_raw(query, max_results=max_results, topic=topic, days=days)}

            elif function_name == "publish_post":
                from src.tools.blog_publisher import create_blog_tools
                publish_post = create_blog_tools(user_id, channel="web_voice")[0]
                return {"result": publish_post(**args)}

            elif function_name == "generate_image":
                from src.tools.image_generator import create_image_tools, expand_slides_from_description
                from src.queue.task_queue import pop_limit_flag
                from src.integrations.channel_router import resolve_channel

                delivery = args.pop("delivery_channel", None)
                effective_channel = resolve_channel(delivery) if delivery else "web_voice"
                if not effective_channel:
                    effective_channel = "web_voice"
                # Auto-upgrade: voz pedindo WhatsApp -> ambos (web + whatsapp) para manter feedback visual
                if effective_channel == "whatsapp_text":
                    effective_channel = "web_whatsapp"

                # Voice Live envia formato simplificado (description + num_slides)
                # em vez do array completo de slides.
                reference_source = args.pop("reference_source", "") or ""
                if "description" in args and "slides" not in args:
                    description = args.pop("description")
                    num_slides = int(args.pop("num_slides", 1 if reference_source else 5) or 5)
                    style = args.pop("style", "Fotorrealista") or "Fotorrealista"
                    sequential = args.get("sequential_slides", True)
                    args["slides"] = expand_slides_from_description(description, num_slides, style, sequential=sequential)

                if reference_source:
                    args["reference_source"] = reference_source

                generate_image, _ = create_image_tools(user_id, channel=effective_channel)
                tool_result = generate_image(**args)
                limit_info = pop_limit_flag(user_id)
                if limit_info:
                    return {
                        "result": limit_info["message"],
                        "limit_reached": True,
                        "plan_type": limit_info.get("plan_type", "free"),
                    }
                return {"result": tool_result}

            elif function_name == "list_gallery":
                from src.tools.image_generator import create_image_tools
                _, list_gallery = create_image_tools(user_id, channel="web_voice")
                return {"result": list_gallery()}

            # Legacy voice tool names (backwards compat)
            elif function_name == "generate_carousel":
                from src.tools.image_generator import create_image_tools, expand_slides_from_description
                from src.queue.task_queue import pop_limit_flag
                from src.integrations.channel_router import resolve_channel

                delivery = args.pop("delivery_channel", None)
                effective_channel = resolve_channel(delivery) if delivery else "web_voice"
                if not effective_channel:
                    effective_channel = "web_voice"
                if effective_channel == "whatsapp_text":
                    effective_channel = "web_whatsapp"

                if "description" in args and "slides" not in args:
                    description = args.pop("description")
                    num_slides = int(args.pop("num_slides", 5) or 5)
                    style = args.pop("style", "Fotorrealista") or "Fotorrealista"
                    sequential = args.get("sequential_slides", True)
                    args["slides"] = expand_slides_from_description(description, num_slides, style, sequential=sequential)

                # Map old use_reference_image to new reference_source
                if args.pop("use_reference_image", False):
                    args["reference_source"] = "auto"

                generate_image, _ = create_image_tools(user_id, channel=effective_channel)
                tool_result = generate_image(**args)
                limit_info = pop_limit_flag(user_id)
                if limit_info:
                    return {
                        "result": limit_info["message"],
                        "limit_reached": True,
                        "plan_type": limit_info.get("plan_type", "free"),
                    }
                return {"result": tool_result}

            elif function_name == "list_carousels":
                from src.tools.image_generator import create_image_tools
                _, list_gallery = create_image_tools(user_id, channel="web_voice")
                return {"result": list_gallery()}

            elif function_name == "edit_image":
                from src.tools.image_generator import create_image_tools, expand_slides_from_description
                from src.queue.task_queue import pop_limit_flag
                from src.integrations.channel_router import resolve_channel

                delivery = args.pop("delivery_channel", None)
                effective_channel = resolve_channel(delivery) if delivery else "web_voice"
                if not effective_channel:
                    effective_channel = "web_voice"
                if effective_channel == "whatsapp_text":
                    effective_channel = "web_whatsapp"

                # Map old edit_image args to generate_image args
                edit_instructions = args.pop("edit_instructions", "")
                source = args.pop("source", "auto")
                args["slides"] = [{"prompt": edit_instructions}]
                args["reference_source"] = source
                if "title" not in args:
                    args["title"] = f"Edição: {edit_instructions[:60]}"

                generate_image, _ = create_image_tools(user_id, channel=effective_channel)
                tool_result = generate_image(**args)
                limit_info = pop_limit_flag(user_id)
                if limit_info:
                    return {
                        "result": limit_info["message"],
                        "limit_reached": True,
                        "plan_type": limit_info.get("plan_type", "free"),
                    }
                return {"result": tool_result}
                
            elif function_name == "send_to_channel":
                from src.tools.channel_delivery import create_send_to_channel_tool
                send_fn = create_send_to_channel_tool(user_id)
                return {"result": send_fn(**args)}

            # --- Social Monitoring ---
            elif function_name == "preview_account":
                from src.tools.social_monitor import create_social_tools
                tools = create_social_tools(user_id, channel="web_voice")
                preview_fn = tools[0]
                return {"result": preview_fn(**args)}

            elif function_name == "track_account":
                from src.tools.social_monitor import create_social_tools
                tools = create_social_tools(user_id, channel="web_voice")
                track_fn = tools[1]
                return {"result": track_fn(**args)}

            elif function_name == "untrack_account":
                from src.tools.social_monitor import create_social_tools
                tools = create_social_tools(user_id, channel="web_voice")
                untrack_fn = tools[2]
                return {"result": untrack_fn(**args)}

            elif function_name == "list_tracked_accounts":
                from src.tools.social_monitor import create_social_tools
                tools = create_social_tools(user_id, channel="web_voice")
                list_fn = tools[3]
                return {"result": list_fn(**args)}

            elif function_name == "get_account_insights":
                from src.tools.social_monitor import create_social_tools
                tools = create_social_tools(user_id, channel="web_voice")
                insights_fn = tools[4]
                return {"result": insights_fn(**args)}

            elif function_name == "get_trending_content":
                from src.tools.social_monitor import create_social_tools
                tools = create_social_tools(user_id, channel="web_voice")
                trending_fn = tools[5]
                return {"result": trending_fn(**args)}

            elif function_name == "analyze_posts":
                from src.tools.social_monitor import create_social_tools
                tools = create_social_tools(user_id, channel="web_voice")
                analyze_fn = tools[6]
                return {"result": analyze_fn(**args)}

            elif function_name == "create_content_script":
                from src.tools.social_monitor import create_social_tools
                tools = create_social_tools(user_id, channel="web_voice")
                script_fn = tools[7]
                return {"result": script_fn(**args)}

            elif function_name == "toggle_alerts":
                from src.tools.social_monitor import create_social_tools
                tools = create_social_tools(user_id, channel="web_voice")
                toggle_fn = tools[8]
                return {"result": toggle_fn(**args)}

            # --- Research & Web ---
            elif function_name == "fetch_page":
                from src.tools.web_search import fetch_page_raw, _is_blocked_url
                url = args.get("url", "")
                if _is_blocked_url(url):
                    return {"result": f"Nao consegui acessar {url} — redes sociais bloqueiam acesso de robos. Use track_account para monitorar contas."}
                return {"result": fetch_page_raw(url)}

            elif function_name == "deep_research":
                from src.tools.deep_research import create_deep_research_tool
                research_fn = create_deep_research_tool(notifier=None, user_id=user_id)
                return {"result": research_fn(**args)}

            # --- Carousel Presets ---
            elif function_name == "save_carousel_preset":
                from src.tools.branding_tools import create_branding_tools
                branding_tools = create_branding_tools(user_id)
                save_preset_fn = branding_tools[4]
                return {"result": save_preset_fn(**args)}

            elif function_name == "list_carousel_presets":
                from src.tools.branding_tools import create_branding_tools
                branding_tools = create_branding_tools(user_id)
                list_presets_fn = branding_tools[5]
                return {"result": list_presets_fn()}

            # --- Branding ---
            elif function_name == "get_brand_profile":
                from src.tools.branding_tools import create_branding_tools
                branding_tools = create_branding_tools(user_id)
                return {"result": branding_tools[0](**args)}

            elif function_name == "update_brand_profile":
                from src.tools.branding_tools import create_branding_tools
                branding_tools = create_branding_tools(user_id)
                return {"result": branding_tools[1](**args)}

            elif function_name == "list_brand_profiles":
                from src.tools.branding_tools import create_branding_tools
                branding_tools = create_branding_tools(user_id)
                return {"result": branding_tools[2]()}

            # --- Google Integrations (conditional) ---
            elif function_name == "read_emails":
                from src.tools.google_tools import create_google_tools
                read_fn, _, _ = create_google_tools(user_id)
                return {"result": read_fn(**args)}

            elif function_name == "get_calendar_events":
                from src.tools.google_tools import create_google_tools
                _, calendar_fn, _ = create_google_tools(user_id)
                return {"result": calendar_fn(**args)}

            elif function_name == "create_calendar_event":
                from src.tools.google_tools import create_google_tools
                _, _, create_fn = create_google_tools(user_id)
                return {"result": create_fn(**args)}

            elif function_name == "run_workflow":
                from src.tools.workflow_tool import create_workflow_tools
                run_wf, _ = create_workflow_tools(user_id, channel="web_voice")
                return {"result": run_wf(**args)}

            elif function_name == "schedule_workflow":
                from src.tools.workflow_tool import create_workflow_tools
                _, schedule_wf = create_workflow_tools(user_id, channel="web_voice")
                return {"result": schedule_wf(**args)}

            else:
                return {"error": f"Tool '{function_name}' not found."}
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"error": str(e)}

    return await asyncio.to_thread(_run_sync)

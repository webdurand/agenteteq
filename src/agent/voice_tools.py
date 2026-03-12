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
        "name": "generate_carousel",
        "description": "Gera imagens (carrossel ou imagem unica) em background. Passe uma descricao simples do que gerar e a quantidade de slides.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Titulo curto da geracao"},
                "description": {"type": "string", "description": "Descricao do que gerar, ex: 'paisagens brasileiras variadas', '10 gatos em estilo anime'"},
                "num_slides": {"type": "integer", "description": "Quantidade de imagens/slides a gerar (padrao 5)"},
                "style": {"type": "string", "description": "Estilo visual, ex: Fotorrealista, Cinematico, Clean/Mockup, Anime, Aquarela"},
                "format": {"type": "string", "description": "Formato da imagem, ex: 1350x1080, 1080x1080, 16:9"},
                "use_reference_image": {"type": "boolean", "description": "Usar imagem de referencia da conversa"},
                "delivery_channel": {"type": "string", "description": "Canal onde entregar as imagens: 'whatsapp', 'web' ou 'ambos'. Se nao informado, entrega na web."}
            },
            "required": ["title", "description"]
        }
    },
    {
        "name": "list_carousels",
        "description": "Lista os carrosseis ja gerados pelo usuario.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "edit_image",
        "description": "Edita uma imagem existente em background.",
        "parameters": {
            "type": "object",
            "properties": {
                "edit_instructions": {"type": "string", "description": "Instrucao detalhada de edicao"},
                "source": {"type": "string", "description": "original, last_generated ou auto"},
                "format": {"type": "string", "description": "Formato de saida, ex: 1:1, 4:3, 16:9"},
                "delivery_channel": {"type": "string", "description": "Canal onde entregar a imagem editada: 'whatsapp', 'web' ou 'ambos'. Se nao informado, entrega na web."}
            },
            "required": ["edit_instructions"]
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
    }
]

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

            elif function_name == "generate_carousel":
                from src.tools.carousel_generator import create_carousel_tools, expand_slides_from_description
                from src.queue.task_queue import pop_limit_flag
                from src.integrations.channel_router import resolve_channel

                delivery = args.pop("delivery_channel", None)
                effective_channel = resolve_channel(delivery) if delivery else "web_voice"
                if not effective_channel:
                    effective_channel = "web_voice"

                # Voice Live envia formato simplificado (description + num_slides)
                # em vez do array completo de slides.
                if "description" in args and "slides" not in args:
                    description = args.pop("description")
                    num_slides = int(args.pop("num_slides", 5) or 5)
                    style = args.pop("style", "Fotorrealista") or "Fotorrealista"
                    args["slides"] = expand_slides_from_description(description, num_slides, style)

                generate_carousel, _ = create_carousel_tools(user_id, channel=effective_channel)
                tool_result = generate_carousel(**args)
                limit_info = pop_limit_flag(user_id)
                if limit_info:
                    return {
                        "result": limit_info["message"],
                        "limit_reached": True,
                        "plan_type": limit_info.get("plan_type", "free"),
                    }
                return {"result": tool_result}

            elif function_name == "list_carousels":
                from src.tools.carousel_generator import create_carousel_tools
                _, list_carousels = create_carousel_tools(user_id, channel="web_voice")
                return {"result": list_carousels()}

            elif function_name == "edit_image":
                from src.tools.image_editor import create_image_editor_tools
                from src.queue.task_queue import pop_limit_flag
                from src.integrations.channel_router import resolve_channel

                delivery = args.pop("delivery_channel", None)
                effective_channel = resolve_channel(delivery) if delivery else "web_voice"
                if not effective_channel:
                    effective_channel = "web_voice"

                edit_image = create_image_editor_tools(user_id, channel=effective_channel)
                tool_result = edit_image(**args)
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

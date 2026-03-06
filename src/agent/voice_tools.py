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
                tools = create_scheduler_tools(user_id)
                schedule_func = tools[0]
                return {"result": schedule_func(**args)}
                
            elif function_name == "list_schedules":
                from src.tools.scheduler_tool import create_scheduler_tools
                tools = create_scheduler_tools(user_id)
                list_func = tools[1]
                return {"result": list_func(**args)}
                
            elif function_name == "cancel_schedule":
                from src.tools.scheduler_tool import create_scheduler_tools
                tools = create_scheduler_tools(user_id)
                cancel_func = tools[2]
                return {"result": cancel_func(**args)}
                
            else:
                return {"error": f"Tool '{function_name}' not found."}
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"error": str(e)}

    return await asyncio.to_thread(_run_sync)

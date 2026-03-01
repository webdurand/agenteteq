"""
Dispatcher para mensagens proativas agendadas.
Quando um job do APScheduler dispara, esta funcao cria um Agno Agent,
executa as instrucoes e envia o resultado via WhatsApp.

Nota: APScheduler executa jobs em threads, por isso usamos asyncio.run()
para chamar o cliente async do WhatsApp a partir de um contexto sincrono.
"""
import asyncio
import traceback


def dispatch_proactive_message(user_phone: str, task_instructions: str):
    """
    Funcao chamada pelo APScheduler quando um job agendado dispara.
    Cria o agente Agno, executa as instrucoes e envia o resultado via WhatsApp.

    Args:
        user_phone: Numero de telefone do usuario (session_id do agente).
        task_instructions: Instrucao para o agente executar (ex: "Envie as tarefas do dia").
    """
    print(f"[DISPATCHER] Disparando mensagem proativa para {user_phone}: {task_instructions[:80]}...")
    try:
        from src.agent.assistant import get_assistant
        from src.integrations.whatsapp import whatsapp_client
        from src.tools.web_search import create_web_search_tool, create_fetch_page_tool
        from src.tools.deep_research import create_deep_research_tool

        # Para jobs proativos nao ha notifier (nao ha mensagem do usuario pra responder)
        search_tools = [
            create_web_search_tool(None),
            create_fetch_page_tool(None),
            create_deep_research_tool(None, user_phone),
        ]

        agent = get_assistant(session_id=user_phone, extra_tools=search_tools)
        response = agent.run(task_instructions, knowledge_filters={"user_id": user_phone})

        if response and response.content:
            asyncio.run(whatsapp_client.send_text_message(user_phone, response.content))
            print(f"[DISPATCHER] Mensagem proativa enviada com sucesso para {user_phone}.")
        else:
            print(f"[DISPATCHER] Agente retornou resposta vazia para {user_phone}.")

    except Exception as e:
        print(f"[DISPATCHER] Erro ao disparar mensagem proativa para {user_phone}: {e}")
        traceback.print_exc()

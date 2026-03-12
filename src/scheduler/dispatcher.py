"""
Dispatcher para mensagens proativas agendadas.
Quando um job do APScheduler dispara, esta funcao busca o reminder no banco de dados,
verifica se ainda esta ativo, cria o Agno Agent, executa as instrucoes e envia 
o resultado via canal configurado.

Nota: APScheduler executa jobs em threads, por isso usamos asyncio.run()
para chamar o cliente async do WhatsApp a partir de um contexto sincrono.
"""
import asyncio
import traceback
import logging

logger = logging.getLogger(__name__)

def dispatch_proactive_message(reminder_id: int):
    """
    Funcao chamada pelo APScheduler quando um job agendado dispara.
    Busca o lembrete no banco, cria o agente, executa e envia.

    Args:
        reminder_id: ID do lembrete na tabela reminders.
    """
    from src.db.session import get_engine, _is_sqlite
    engine = get_engine() if not _is_sqlite() else None
    
    if engine:
        from sqlalchemy import text
        with engine.connect() as conn:
            # Hash reminder_id to use as int lock key
            lock_acquired = conn.execute(text("SELECT pg_try_advisory_lock(hashtext(:id))"), {"id": str(reminder_id)}).scalar()
            if not lock_acquired:
                logger.info("Outro pod ja esta processando o reminder %s. Abortando localmente.", reminder_id)
                return
    
    logger.info("Disparando reminder_id %s...", reminder_id)
    try:
        from src.models.reminders import get_reminder, mark_fired
        
        reminder = get_reminder(reminder_id)
        if not reminder:
            logger.info("Reminder %s nao encontrado no banco. Abortando.", reminder_id)
            return
            
        if reminder["status"] != "active":
            logger.info("Reminder %s nao esta ativo (status: %s). Abortando.", reminder_id, reminder['status'])
            return

        user_phone = reminder["user_id"]
        task_instructions = reminder["task_instructions"]
        channel = reminder["notification_channel"]
        workflow_id = reminder.get("workflow_id")
        
        logger.info("Reminder %s | User: %s | Channel: %s | Workflow: %s | Task: %s...",
                     reminder_id, user_phone, channel, workflow_id, task_instructions[:60])

        # --- Workflow path: executa steps sequencialmente ---
        if workflow_id:
            response_content = _execute_workflow_for_reminder(workflow_id, user_phone, channel)
            if not response_content:
                logger.info("Workflow %s retornou vazio para reminder %s.", workflow_id, reminder_id)
                return
        else:
            # --- Legacy path: single agent.run ---
            response_content = None
            if channel in ["whatsapp_text", "whatsapp_call", "web_voice", "web_text", "web_whatsapp"]:
                from src.agent.factory import create_agent_with_tools
                from src.agent.response_utils import extract_final_response

                reminder_instructions = [
                    "EXECUCAO DE LEMBRETE AGENDADO: Voce esta executando um lembrete que o usuario agendou anteriormente.",
                    "NAO peca mais informacoes, NAO tente agendar nada novo, NAO faca perguntas.",
                    "Execute as instrucoes diretamente e envie o resultado pronto.",
                    "REGRA CRITICA: Se as instrucoes envolverem noticias, pesquisa, informacoes atualizadas "
                    "ou qualquer dado que mude com o tempo, voce DEVE obrigatoriamente usar web_search "
                    "(com topic='news' para noticias) ou deep_research. NUNCA responda usando apenas "
                    "seu conhecimento interno para dados que mudam.",
                    "Para noticias: faca MULTIPLAS buscas com queries especificas e variadas. "
                    "Inclua SEMPRE: titulo real da noticia, fonte, data e link.",
                ]

                agent_channel = "web" if channel in {"web_voice", "web_text"} else "whatsapp"
                agent = create_agent_with_tools(
                    session_id=user_phone,
                    user_id=user_phone,
                    channel=agent_channel,
                    extra_instructions=reminder_instructions,
                    include_scheduler=False,
                )
                response = agent.run(task_instructions, knowledge_filters={"user_id": user_phone})

                from src.memory.analytics import log_run_metrics
                try:
                    log_run_metrics(user_phone, agent_channel, response)
                except Exception:
                    pass
                
                if response and response.content:
                    response_content = extract_final_response(response)
                else:
                    logger.info("Agente retornou resposta vazia para %s.", user_phone)
                    return

        # 2. Enviar pelo canal especifico
        if channel == "whatsapp_text":
            from src.integrations.whatsapp import whatsapp_client
            asyncio.run(whatsapp_client.send_text_message(user_phone, response_content))
            logger.info("Mensagem enviada com sucesso para %s via whatsapp_text.", user_phone)
            
        elif channel == "whatsapp_call":
            # Futuro: Implementar chamada de audio WhatsApp aqui
            logger.info("[FUTURO] Ligacao WhatsApp para %s solicitada. Falaria: %s", user_phone, response_content)
            
        elif channel == "web_voice":
            from src.endpoints.web import ws_manager
            from src.integrations.tts import get_tts
            import base64
            
            if not ws_manager.is_online(user_phone):
                logger.info("Usuario %s nao esta online na web. Fazendo fallback para whatsapp_text.", user_phone)
                from src.integrations.whatsapp import whatsapp_client
                fallback_msg = f"(Lembrete do Agente de Voz)\n\n{response_content}"
                asyncio.run(whatsapp_client.send_text_message(user_phone, fallback_msg))
            else:
                logger.info("Usuario %s online na web. Gerando audio para falar...", user_phone)
                tts = get_tts()
                try:
                    audio_out, mime_type = asyncio.run(tts.synthesize(response_content))
                    audio_b64 = base64.b64encode(audio_out).decode() if audio_out else ""
                except Exception as e:
                    logger.info("TTS falhou: %s", e)
                    audio_b64 = ""
                    mime_type = "browser"
                
                msg_payload = {
                    "type": "response",
                    "text": response_content,
                    "audio_b64": audio_b64,
                    "mime_type": mime_type,
                    "needs_follow_up": False
                }
                asyncio.run(ws_manager.send_personal_message(user_phone, msg_payload))
                logger.info("Lembrete falado enviado com sucesso para %s via web_voice.", user_phone)

        elif channel == "web_text":
            from src.endpoints.web import ws_manager
            from src.integrations.whatsapp import whatsapp_client

            msg_payload = {
                "type": "response",
                "text": response_content,
                "audio_b64": "",
                "mime_type": "none",
                "needs_follow_up": False,
            }

            if asyncio.run(ws_manager.send_personal_message(user_phone, msg_payload)):
                logger.info("Lembrete em texto enviado com sucesso para %s via web_text.", user_phone)
            else:
                logger.info("Usuario %s nao esta online na web. Fallback para WhatsApp.", user_phone)
                fallback_msg = f"(Lembrete da web)\n\n{response_content}"
                asyncio.run(whatsapp_client.send_text_message(user_phone, fallback_msg))

        elif channel == "web_whatsapp":
            from src.endpoints.web import ws_manager
            from src.integrations.whatsapp import whatsapp_client

            # 1) Sempre envia no WhatsApp
            asyncio.run(whatsapp_client.send_text_message(user_phone, response_content))
            logger.info("Lembrete enviado para %s via whatsapp_text (canal combinado).", user_phone)

            # 2) Tenta enviar na web (se online)
            msg_payload = {
                "type": "response",
                "text": response_content,
                "audio_b64": "",
                "mime_type": "none",
                "needs_follow_up": False,
            }
            if asyncio.run(ws_manager.send_personal_message(user_phone, msg_payload)):
                logger.info("Lembrete enviado para %s via web_text (canal combinado).", user_phone)
            else:
                logger.info("Usuario %s offline na web. Entrega feita apenas no WhatsApp.", user_phone)
            
        else:
            logger.info("Canal nao suportado: %s", channel)

        # 3. Se for disparo unico, marcar como fired
        if reminder["trigger_type"] == "date":
            mark_fired(reminder_id)
            logger.info("Reminder %s marcado como 'fired'.", reminder_id)

    except Exception as e:
        logger.error("Erro ao disparar reminder %s: %s", reminder_id, e)
        traceback.print_exc()
        
    finally:
        if engine:
            try:
                from sqlalchemy import text

                with engine.connect() as conn:
                    conn.execute(text("SELECT pg_advisory_unlock(hashtext(:id))"), {"id": str(reminder_id)})
                    conn.commit()
            except Exception as e:
                logger.error("Erro ao liberar lock do reminder %s: %s", reminder_id, e)


def _execute_workflow_for_reminder(workflow_id: str, user_phone: str, channel: str) -> str | None:
    """
    Executa um workflow linkado a um reminder.
    Reseta steps (para execucoes recorrentes) e roda o executor.

    Returns:
        Output do ultimo step, ou None se falhou.
    """
    try:
        from src.models.workflows import get_workflow, reset_workflow_steps
        from src.workflow.executor import execute_workflow

        workflow = get_workflow(workflow_id)
        if not workflow:
            logger.error("[Dispatcher] Workflow %s nao encontrado.", workflow_id)
            return None

        # Reseta steps pra pending (importante pra execucoes recorrentes)
        reset_workflow_steps(workflow_id)

        logger.info("[Dispatcher] Executando workflow %s (%s steps) para %s...",
                     workflow_id, len(workflow["steps"]), user_phone)

        result = execute_workflow(workflow_id)

        if result and not result.startswith("Erro"):
            return result

        logger.error("[Dispatcher] Workflow %s falhou: %s", workflow_id, result)
        return None

    except Exception as e:
        logger.error("[Dispatcher] Erro ao executar workflow %s: %s", workflow_id, e, exc_info=True)
        return None

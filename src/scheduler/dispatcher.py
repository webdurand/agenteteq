"""
Dispatcher para mensagens proativas agendadas.
Quando um job do APScheduler dispara, esta funcao busca o reminder no banco de dados,
verifica se ainda esta ativo, cria o Agno Agent, executa as instrucoes e envia 
o resultado via canal configurado.

Nota: APScheduler executa jobs em threads, por isso usamos asyncio.run()
para chamar o cliente async do WhatsApp a partir de um contexto sincrono.
"""
import asyncio
import time
import traceback
import logging

logger = logging.getLogger(__name__)

def dispatch_proactive_message(reminder_id: int):
    """
    Funcao chamada pelo APScheduler quando um job agendado dispara.
    Busca o lembrete no banco, cria o agente, executa e envia.

    Usa pg_try_advisory_lock para garantir que apenas 1 pod execute cada reminder.
    A conexao que segura o lock fica aberta durante toda a execucao.

    Args:
        reminder_id: ID do lembrete na tabela reminders.
    """
    from src.db.session import get_engine, _is_sqlite
    engine = get_engine() if not _is_sqlite() else None

    lock_conn = None
    try:
        if engine:
            from sqlalchemy import text
            lock_conn = engine.connect()
            lock_acquired = lock_conn.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:id))"),
                {"id": str(reminder_id)}
            ).scalar()
            if not lock_acquired:
                logger.info("Outro pod ja esta processando o reminder %s. Abortando localmente.", reminder_id)
                lock_conn.close()
                lock_conn = None
                return

        logger.info("Disparando reminder_id %s...", reminder_id)

        from src.models.reminders import get_reminder, mark_fired

        reminder = get_reminder(reminder_id)
        if not reminder:
            logger.info("Reminder %s nao encontrado no banco. Abortando.", reminder_id)
            return

        if reminder["status"] != "active":
            logger.info("Reminder %s nao esta ativo (status: %s). Removendo job orfao.", reminder_id, reminder['status'])
            try:
                from src.scheduler.engine import get_scheduler
                scheduler = get_scheduler()
                for jid in [f"reminder_{reminder_id}", reminder.get("apscheduler_job_id")]:
                    if jid:
                        try:
                            scheduler.remove_job(jid)
                            logger.info("Job orfao %s removido do APScheduler.", jid)
                        except Exception:
                            pass
            except Exception as e:
                logger.warning("Nao foi possivel remover job orfao: %s", e)
            return

        user_phone = reminder["user_id"]
        task_instructions = reminder["task_instructions"]
        channel = reminder["notification_channel"]
        workflow_id = reminder.get("workflow_id")

        # [SEC] Check if user plan is active and has budget before running agent
        from src.memory.identity import get_user, is_plan_active
        from src.config.feature_gates import check_budget
        user_data = get_user(user_phone)
        if not user_data or not is_plan_active(user_data):
            logger.info("Reminder %s: usuario %s*** sem plano ativo. Pulando.", reminder_id, user_phone[:4])
            return
        budget_info = check_budget(user_phone)
        if budget_info and budget_info.get("percentage_used", 0) >= 100:
            logger.info("Reminder %s: usuario %s*** sem budget. Pulando.", reminder_id, user_phone[:4])
            return

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

                from datetime import datetime
                from zoneinfo import ZoneInfo
                now_br = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y %H:%M")

                reminder_instructions = [
                    f"DATA E HORA ATUAL: {now_br} (Horario de Brasilia).",
                    "EXECUCAO INDEPENDENTE: Esta e uma execucao isolada. Voce NAO tem contexto de execucoes anteriores. "
                    "OBRIGATORIO: Use as ferramentas (tools) para buscar dados em tempo real. NUNCA reutilize dados de contexto.",
                    "EXECUCAO DE LEMBRETE AGENDADO: Voce esta executando um lembrete que o usuario agendou anteriormente.",
                    "NAO peca mais informacoes, NAO tente agendar nada novo, NAO faca perguntas.",
                    "Execute as instrucoes diretamente e envie o resultado pronto.",
                    "REGRA CRITICA: Se as instrucoes envolverem noticias, pesquisa, informacoes atualizadas "
                    "ou qualquer dado que mude com o tempo, voce DEVE obrigatoriamente usar web_search "
                    "(com topic='news' para noticias) ou deep_research. NUNCA responda usando apenas "
                    "seu conhecimento interno para dados que mudam.",
                    "Para noticias: faca MULTIPLAS buscas com queries especificas e variadas. "
                    "Inclua SEMPRE: titulo real da noticia, fonte, data e link.",
                    "FRESCOR OBRIGATORIO: Busque SEMPRE as noticias e informacoes MAIS RECENTES (de HOJE). "
                    "NUNCA repita conteudo de execucoes anteriores. Cada execucao deste lembrete deve trazer "
                    "informacoes NOVAS e DIFERENTES. Inclua a data de publicacao de cada noticia.",
                    "DICA: Varie suas queries de busca. Use termos diferentes, angulos diferentes, "
                    "e adicione qualificadores temporais como 'hoje', 'ultimas horas' ou a data atual nas queries.",
                ]

                agent_channel = "web" if channel in {"web_voice", "web_text"} else "whatsapp"
                isolated_session = f"reminder_{reminder_id}_{int(time.time())}"
                agent = create_agent_with_tools(
                    session_id=isolated_session,
                    user_id=user_phone,
                    channel=agent_channel,
                    extra_instructions=reminder_instructions,
                    include_scheduler=False,
                    include_knowledge=False,
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
            from src.integrations.whatsapp_sender import send_whatsapp_with_interactive
            asyncio.run(send_whatsapp_with_interactive(user_phone, response_content))
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
                from src.integrations.whatsapp_sender import send_whatsapp_with_interactive
                fallback_msg = f"(Lembrete do Agente de Voz)\n\n{response_content}"
                asyncio.run(send_whatsapp_with_interactive(user_phone, fallback_msg))
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
                from src.integrations.whatsapp_sender import send_whatsapp_with_interactive
                fallback_msg = f"(Lembrete da web)\n\n{response_content}"
                asyncio.run(send_whatsapp_with_interactive(user_phone, fallback_msg))

        elif channel == "web_whatsapp":
            from src.endpoints.web import ws_manager
            from src.integrations.whatsapp_sender import send_whatsapp_with_interactive

            # 1) Sempre envia no WhatsApp
            asyncio.run(send_whatsapp_with_interactive(user_phone, response_content))
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
        if lock_conn:
            try:
                from sqlalchemy import text
                lock_conn.execute(text("SELECT pg_advisory_unlock(hashtext(:id))"), {"id": str(reminder_id)})
                lock_conn.commit()
            except Exception as e:
                logger.error("Erro ao liberar lock do reminder %s: %s", reminder_id, e)
            finally:
                lock_conn.close()


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

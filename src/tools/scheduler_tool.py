"""
Tools de agendamento para o agente Teq.
Usa o padrao factory (igual aos search tools) para injetar o user_phone automaticamente
via session_id, sem expor o numero de telefone ao LLM.

Tipos de agendamento suportados:
- "date"     : disparo unico (use minutes_from_now para casos como "daqui 5 minutos")
- "cron"     : recorrente com expressao cron (ex: "todo dia as 8h UTC")
- "interval" : recorrente por intervalo em minutos (ex: "a cada 30 minutos")
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional
import logging

logger = logging.getLogger(__name__)

def create_scheduler_tools(user_phone: str, channel: str = "unknown"):
    """
    Factory que cria as tools de agendamento com o numero de telefone do usuario pre-injetado.
    Garante que o LLM nao precise (nem possa) informar o numero de telefone manualmente.

    Args:
        user_phone: Numero de telefone do usuario (session_id).
        channel: Canal de origem (web, whatsapp, etc).

    Returns:
        Tuple com (schedule_message, list_schedules, cancel_schedule).
    """

    def schedule_message(
        task_instructions: str,
        trigger_type: str = "",
        minutes_from_now: Optional[int] = None,
        run_date: Optional[str] = None,
        cron_expression: Optional[str] = None,
        interval_minutes: Optional[int] = None,
        title: str = "",
        notification_channel: str = "",
    ) -> str:
        """
        Agenda uma mensagem proativa para ser enviada ao usuario.

        IMPORTANTE — ANTES de chamar esta tool, CONFIRME com o usuario:
        1. QUANDO: para quando agendar? (data/hora exata, daqui X minutos, frequencia)
        2. CANAL: onde entregar? (web, WhatsApp ou ambos)
        Se o usuario nao informou esses dados, PERGUNTE antes de chamar.

        Args:
            task_instructions: O que o seu 'eu do futuro' deve fazer quando o job disparar.
                               Deve ser UM PROMPT COMPLETO e EXTREMAMENTE ESPECIFICO — diga quais tools chamar.
                               Voce pode usar QUALQUER tool disponivel, incluindo generate_carousel para gerar imagens.
                               A imagem gerada sera entregue automaticamente pelo canal escolhido (web, WhatsApp ou ambos).
                               Ex: "Buscar na internet com web_search se a novidade X saiu hoje e avisar o usuario."
                               Ex: "Execute list_tasks, veja as pendentes e mande um resumo para o usuario."
                               Ex: "Pesquisar com deep_research sobre Y e enviar um relatorio."
                               Ex: "Gere uma imagem aleatoria com generate_carousel (1 slide, formato 1080x1080, prompt criativo, use_reference_image=False)."
                               REGRA CRITICA PARA AGENDAMENTOS RECORRENTES (cron/interval):
                               NUNCA inclua datas absolutas (ex: '13/03/2026') nas task_instructions.
                               Use SEMPRE termos relativos como 'de hoje', 'mais recentes', 'ultimas 24h'.
                               O agente que executar essas instrucoes no futuro tera acesso a data correta automaticamente.
                               Datas absolutas fariam a busca ficar presa no passado.
            trigger_type: OBRIGATORIO. Tipo de gatilho — "date" (unico), "cron" (recorrente), "interval" (por intervalo).
                          Se omitido, sera inferido dos outros args (minutes_from_now/run_date -> date, cron_expression -> cron, interval_minutes -> interval).
            minutes_from_now: PREFERIDO para disparo unico relativo. Numero de minutos a partir de agora.
                              Ex: 1 para "daqui 1 minuto", 60 para "daqui 1 hora".
            run_date: Alternativo ao minutes_from_now. Data/hora absoluta ISO 8601.
                      Ex: "2026-03-01T08:00:00". Use apenas se minutes_from_now nao for suficiente.
            cron_expression: Obrigatorio se trigger_type="cron". 5 campos separados por espaco.
                             Ex: "0 8 * * *" para todo dia as 8h UTC, "0 9 * * 1-5" seg-sex as 9h UTC.
            interval_minutes: Obrigatorio se trigger_type="interval". Intervalo em minutos.
                              Ex: 30 para "a cada 30 minutos".
            title: Um titulo curto descrevendo o agendamento (opcional).
            notification_channel: Canal pelo qual a mensagem sera enviada.
                                  Opcoes:
                                  - 'whatsapp_text'
                                  - 'web_text'
                                  - 'web_voice' (fala no navegador se aberto, senao fallback p/ wpp)
                                  - 'web_whatsapp' (envia na web + WhatsApp)

        Returns:
            Confirmacao com o ID do job criado ou mensagem de erro.
        """
        # Inferir trigger_type se o LLM nao informou
        if not trigger_type or trigger_type not in ("date", "cron", "interval"):
            if cron_expression:
                trigger_type = "cron"
            elif interval_minutes:
                trigger_type = "interval"
            elif minutes_from_now is not None or run_date:
                trigger_type = "date"
            else:
                return ("Preciso saber QUANDO agendar. Pergunte ao usuario: "
                        "para quando? (data/hora, daqui X minutos, frequencia)")
            logger.info("trigger_type inferido como '%s' a partir dos args", trigger_type)

        logger.info("schedule_message | user=%s | trigger=%s | minutes_from_now=%s | run_date=%s | cron=%s | interval=%s", user_phone, trigger_type, minutes_from_now, run_date, cron_expression, interval_minutes)
        try:
            import zoneinfo
            from src.scheduler.engine import get_scheduler
            from src.scheduler.dispatcher import dispatch_proactive_message
            from src.models.reminders import create_reminder, update_apscheduler_job_id
            from src.memory.identity import get_user

            scheduler = get_scheduler()

            raw_channel = (notification_channel or "").strip().lower()
            if not raw_channel:
                return "Preciso saber o canal antes de agendar. Pergunte ao usuario: web, WhatsApp ou ambos?"

            from src.integrations.channel_router import resolve_channel, SUPPORTED_CHANNELS
            notification_channel = resolve_channel(raw_channel) or raw_channel
            if notification_channel not in SUPPORTED_CHANNELS:
                return (
                    f"Canal '{raw_channel}' nao suportado. "
                    "Use: web, whatsapp, web_voice ou ambos."
                )
            
            # Buscar timezone do usuario
            user_data = get_user(user_phone)
            user_tz_str = user_data.get("timezone", "America/Sao_Paulo") if user_data else "America/Sao_Paulo"
            user_tz = zoneinfo.ZoneInfo(user_tz_str)

            trigger_config = {
                "minutes_from_now": minutes_from_now,
                "run_date": run_date,
                "cron_expression": cron_expression,
                "interval_minutes": interval_minutes,
                "timezone": user_tz_str
            }

            # 1. Gravar na tabela reminders
            reminder_id = create_reminder(
                user_id=user_phone,
                task_instructions=task_instructions,
                trigger_type=trigger_type,
                trigger_config=trigger_config,
                title=title,
                notification_channel=notification_channel
            )

            deterministic_id = f"reminder_{reminder_id}"
            job = None
            if trigger_type == "date":
                if minutes_from_now is not None:
                    run_dt = datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)
                    logger.info("Agendando para daqui %s minuto(s): %s", minutes_from_now, run_dt.isoformat())
                elif run_date:
                    run_dt = datetime.fromisoformat(run_date)
                    if run_dt.tzinfo is None:
                        # Assumir que esta no timezone do usuario
                        run_dt = run_dt.replace(tzinfo=user_tz)
                    logger.info("Agendando para data especifica: %s", run_dt.isoformat())
                else:
                    return "Para agendar um disparo unico, informe minutes_from_now (ex: 5) ou run_date (ISO 8601)."

                job = scheduler.add_job(
                    dispatch_proactive_message,
                    trigger="date",
                    run_date=run_dt,
                    id=deterministic_id,
                    replace_existing=True,
                    kwargs={"reminder_id": reminder_id},
                    misfire_grace_time=300,
                )
                friendly_time = run_dt.strftime("%d/%m/%Y as %H:%M %Z")
                logger.info("Job 'date' criado | RemID: %s | JobID: %s | disparo: %s", reminder_id, job.id, friendly_time)
                msg = f"Agendado! Vou disparar em {friendly_time}. ID do lembrete: {reminder_id}"

            elif trigger_type == "cron":
                if not cron_expression:
                    return "Para agendamento recorrente, informe cron_expression (ex: '0 8 * * *' para todo dia as 8h)."
                parts = cron_expression.strip().split()
                if len(parts) != 5:
                    return f"Expressao cron invalida: '{cron_expression}'. Use o formato '* * * * *' (minuto hora dia mes diasemana)."
                minute, hour, day, month, day_of_week = parts
                job = scheduler.add_job(
                    dispatch_proactive_message,
                    trigger="cron",
                    minute=minute,
                    hour=hour,
                    day=day,
                    month=month,
                    day_of_week=day_of_week,
                    timezone=user_tz,
                    id=deterministic_id,
                    replace_existing=True,
                    kwargs={"reminder_id": reminder_id},
                    misfire_grace_time=300,
                )
                logger.info("Job 'cron' criado | RemID: %s | JobID: %s | cron: %s %s", reminder_id, job.id, cron_expression, user_tz_str)
                msg = f"Agendamento recorrente criado! Cron: '{cron_expression}' ({user_tz_str}). ID do lembrete: {reminder_id}"

            elif trigger_type == "interval":
                if not interval_minutes or interval_minutes <= 0:
                    return "Para agendamento por intervalo, informe interval_minutes com valor positivo."
                job = scheduler.add_job(
                    dispatch_proactive_message,
                    trigger="interval",
                    minutes=interval_minutes,
                    id=deterministic_id,
                    replace_existing=True,
                    kwargs={"reminder_id": reminder_id},
                    misfire_grace_time=300,
                )
                logger.info("Job 'interval' criado | RemID: %s | JobID: %s | a cada %s min", reminder_id, job.id, interval_minutes)
                msg = f"Agendamento por intervalo criado! A cada {interval_minutes} minuto(s). ID do lembrete: {reminder_id}"

            else:
                return f"trigger_type invalido: '{trigger_type}'. Use 'date', 'cron' ou 'interval'."

            if job:
                update_apscheduler_job_id(reminder_id, job.id)
                
            from src.events import emit_event_sync
            emit_event_sync(user_phone, "reminder_updated")

            from src.events_broadcast import emit_action_log_sync
            emit_action_log_sync(user_phone, "Lembrete criado", title or task_instructions[:60], channel)
                
            return msg

        except Exception as e:
            logger.error("Erro ao criar agendamento: %s", e)
            import traceback
            traceback.print_exc()
            return f"Erro ao criar agendamento: {e}"

    def list_schedules() -> str:
        """
        Lista todos os agendamentos ativos do usuario.

        Returns:
            Lista formatada dos agendamentos ou mensagem informando que nao ha nenhum.
        """
        logger.info("list_schedules | user=%s", user_phone)
        try:
            from src.models.reminders import list_user_reminders
            from src.scheduler.engine import get_scheduler
            import zoneinfo
            from src.memory.identity import get_user
            
            scheduler = get_scheduler()
            user_jobs = list_user_reminders(user_phone, status="active").get("reminders", [])

            if not user_jobs:
                logger.info("Nenhum agendamento ativo para %s", user_phone)
                return "Voce nao tem nenhum agendamento ativo no momento."

            # Buscar timezone do usuario para mostrar tempos corretamente
            user_data = get_user(user_phone)
            user_tz_str = user_data.get("timezone", "America/Sao_Paulo") if user_data else "America/Sao_Paulo"
            user_tz = zoneinfo.ZoneInfo(user_tz_str)

            lines = [f"Voce tem {len(user_jobs)} agendamento(s) ativo(s):"]
            for r in user_jobs:
                reminder_id = r["id"]
                instructions = r.get("task_instructions", "")[:60]
                job_id = r.get("apscheduler_job_id")
                
                next_run_str = "aguardando"
                deterministic_id = f"reminder_{reminder_id}"
                job = scheduler.get_job(deterministic_id) or (scheduler.get_job(job_id) if job_id else None)
                if job and job.next_run_time:
                    next_run_dt = job.next_run_time.astimezone(user_tz)
                    next_run_str = next_run_dt.strftime("%d/%m/%Y %H:%M %Z")
                
                lines.append(f"• ID: {reminder_id} | Proximo disparo: {next_run_str} | Instrucao: {instructions}...")

            logger.info("%s agendamento(s) retornados para %s", len(user_jobs), user_phone)
            return "\n".join(lines)

        except Exception as e:
            logger.error("Erro ao listar agendamentos: %s", e)
            import traceback
            traceback.print_exc()
            return f"Erro ao listar agendamentos: {e}"

    def cancel_schedule(job_id: str) -> str:
        """
        Cancela um agendamento pelo seu ID (ID do lembrete).

        Args:
            job_id: ID do agendamento retornado por schedule_message ou list_schedules.

        Returns:
            Confirmacao do cancelamento ou mensagem de erro.
        """
        logger.info("cancel_schedule | reminder_id=%s", job_id)
        try:
            from src.scheduler.engine import get_scheduler
            from src.models.reminders import get_reminder, cancel_reminder

            scheduler = get_scheduler()
            reminder_id = int(job_id)
            reminder = get_reminder(reminder_id)
            
            if not reminder or reminder["user_id"] != user_phone:
                return f"Nao encontrei nenhum agendamento com o ID '{job_id}'. Usa list_schedules pra ver os IDs ativos."
                
            deterministic_id = f"reminder_{reminder_id}"
            for jid in [deterministic_id, reminder.get("apscheduler_job_id")]:
                if jid:
                    try:
                        scheduler.remove_job(jid)
                        logger.info("Job do APScheduler %s removido com sucesso.", jid)
                    except Exception:
                        pass
            
            cancel_reminder(reminder_id)
            from src.events import emit_event_sync
            emit_event_sync(user_phone, "reminder_updated")
            return f"Agendamento {job_id} cancelado com sucesso."

        except ValueError:
            return f"O ID do agendamento deve ser um numero valido (voce informou '{job_id}')."
        except Exception as e:
            logger.error("Erro ao cancelar job %s: %s", job_id, e)
            import traceback

            traceback.print_exc()
            return f"Erro ao cancelar agendamento {job_id}: {e}"

    return schedule_message, list_schedules, cancel_schedule
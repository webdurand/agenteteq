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


def create_scheduler_tools(user_phone: str):
    """
    Factory que cria as tools de agendamento com o numero de telefone do usuario pre-injetado.
    Garante que o LLM nao precise (nem possa) informar o numero de telefone manualmente.

    Args:
        user_phone: Numero de telefone do usuario (session_id).

    Returns:
        Tuple com (schedule_message, list_schedules, cancel_schedule).
    """

    def schedule_message(
        task_instructions: str,
        trigger_type: str,
        minutes_from_now: Optional[int] = None,
        run_date: Optional[str] = None,
        cron_expression: Optional[str] = None,
        interval_minutes: Optional[int] = None,
    ) -> str:
        """
        Agenda uma mensagem proativa para ser enviada ao usuario.

        Args:
            task_instructions: O que o agente deve fazer/dizer quando o job disparar.
                               Ex: "Envie as tarefas pendentes e a previsao do tempo."
            trigger_type: Tipo de gatilho — "date" (unico), "cron" (recorrente), "interval" (por intervalo).
            minutes_from_now: PREFERIDO para disparo unico relativo. Numero de minutos a partir de agora.
                              Ex: 1 para "daqui 1 minuto", 60 para "daqui 1 hora".
            run_date: Alternativo ao minutes_from_now. Data/hora absoluta ISO 8601.
                      Ex: "2026-03-01T08:00:00". Use apenas se minutes_from_now nao for suficiente.
            cron_expression: Obrigatorio se trigger_type="cron". 5 campos separados por espaco.
                             Ex: "0 8 * * *" para todo dia as 8h UTC, "0 9 * * 1-5" seg-sex as 9h UTC.
            interval_minutes: Obrigatorio se trigger_type="interval". Intervalo em minutos.
                              Ex: 30 para "a cada 30 minutos".

        Returns:
            Confirmacao com o ID do job criado ou mensagem de erro.
        """
        print(f"[SCHEDULER] schedule_message | user={user_phone} | trigger={trigger_type} | minutes_from_now={minutes_from_now} | run_date={run_date} | cron={cron_expression} | interval={interval_minutes}")
        try:
            from src.scheduler.engine import get_scheduler
            from src.scheduler.dispatcher import dispatch_proactive_message

            scheduler = get_scheduler()

            if trigger_type == "date":
                if minutes_from_now is not None:
                    run_dt = datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)
                    print(f"[SCHEDULER] Agendando para daqui {minutes_from_now} minuto(s): {run_dt.isoformat()}")
                elif run_date:
                    run_dt = datetime.fromisoformat(run_date)
                    if run_dt.tzinfo is None:
                        run_dt = run_dt.replace(tzinfo=timezone.utc)
                    print(f"[SCHEDULER] Agendando para data especifica: {run_dt.isoformat()}")
                else:
                    return "Para agendar um disparo unico, informe minutes_from_now (ex: 5) ou run_date (ISO 8601)."

                job = scheduler.add_job(
                    dispatch_proactive_message,
                    trigger="date",
                    run_date=run_dt,
                    kwargs={"user_phone": user_phone, "task_instructions": task_instructions},
                    misfire_grace_time=300,
                )
                friendly_time = run_dt.strftime("%d/%m/%Y as %H:%M UTC")
                print(f"[SCHEDULER] Job 'date' criado | ID: {job.id} | disparo: {friendly_time}")
                return f"Agendado! Vou disparar em {friendly_time}. ID: {job.id}"

            elif trigger_type == "cron":
                if not cron_expression:
                    return "Para agendamento recorrente, informe cron_expression (ex: '0 8 * * *' para todo dia as 8h UTC)."
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
                    timezone="UTC",
                    kwargs={"user_phone": user_phone, "task_instructions": task_instructions},
                    misfire_grace_time=300,
                )
                print(f"[SCHEDULER] Job 'cron' criado | ID: {job.id} | cron: {cron_expression}")
                return f"Agendamento recorrente criado! Cron: '{cron_expression}' (UTC). ID: {job.id}"

            elif trigger_type == "interval":
                if not interval_minutes or interval_minutes <= 0:
                    return "Para agendamento por intervalo, informe interval_minutes com valor positivo."
                job = scheduler.add_job(
                    dispatch_proactive_message,
                    trigger="interval",
                    minutes=interval_minutes,
                    kwargs={"user_phone": user_phone, "task_instructions": task_instructions},
                    misfire_grace_time=300,
                )
                print(f"[SCHEDULER] Job 'interval' criado | ID: {job.id} | a cada {interval_minutes} min")
                return f"Agendamento por intervalo criado! A cada {interval_minutes} minuto(s). ID: {job.id}"

            else:
                return f"trigger_type invalido: '{trigger_type}'. Use 'date', 'cron' ou 'interval'."

        except Exception as e:
            print(f"[SCHEDULER] Erro ao criar agendamento: {e}")
            return f"Erro ao criar agendamento: {e}"

    def list_schedules() -> str:
        """
        Lista todos os agendamentos ativos do usuario.

        Returns:
            Lista formatada dos agendamentos ou mensagem informando que nao ha nenhum.
        """
        print(f"[SCHEDULER] list_schedules | user={user_phone}")
        try:
            from src.scheduler.engine import get_scheduler

            scheduler = get_scheduler()
            jobs = scheduler.get_jobs()

            user_jobs = [
                job for job in jobs
                if job.kwargs.get("user_phone") == user_phone
            ]

            if not user_jobs:
                print(f"[SCHEDULER] Nenhum agendamento ativo para {user_phone}")
                return "Voce nao tem nenhum agendamento ativo no momento."

            lines = [f"Voce tem {len(user_jobs)} agendamento(s) ativo(s):"]
            for job in user_jobs:
                instructions = job.kwargs.get("task_instructions", "")[:60]
                next_run = job.next_run_time
                next_run_str = next_run.strftime("%d/%m/%Y %H:%M UTC") if next_run else "aguardando"
                lines.append(f"• ID: {job.id} | Proximo disparo: {next_run_str} | Instrucao: {instructions}...")

            print(f"[SCHEDULER] {len(user_jobs)} agendamento(s) retornados para {user_phone}")
            return "\n".join(lines)

        except Exception as e:
            print(f"[SCHEDULER] Erro ao listar agendamentos: {e}")
            return f"Erro ao listar agendamentos: {e}"

    def cancel_schedule(job_id: str) -> str:
        """
        Cancela um agendamento pelo seu ID.

        Args:
            job_id: ID do agendamento retornado por schedule_message ou list_schedules.

        Returns:
            Confirmacao do cancelamento ou mensagem de erro.
        """
        print(f"[SCHEDULER] cancel_schedule | job_id={job_id}")
        try:
            from src.scheduler.engine import get_scheduler

            scheduler = get_scheduler()
            scheduler.remove_job(job_id)
            print(f"[SCHEDULER] Job {job_id} cancelado com sucesso.")
            return f"Agendamento {job_id} cancelado com sucesso."

        except Exception as e:
            print(f"[SCHEDULER] Erro ao cancelar job {job_id}: {e}")
            if "No job by the id" in str(e) or "JobLookupError" in type(e).__name__:
                return f"Nao encontrei nenhum agendamento com o ID '{job_id}'. Usa list_schedules pra ver os IDs ativos."
            return f"Erro ao cancelar agendamento {job_id}: {e}"

    return schedule_message, list_schedules, cancel_schedule

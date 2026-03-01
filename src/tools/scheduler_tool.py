"""
Tools de agendamento para o agente Teq.
Permitem que o agente crie, liste e cancele mensagens proativas agendadas.

Tipos de agendamento suportados:
- "date"     : disparo unico em data/hora especifica (ex: "daqui 5 minutos")
- "cron"     : recorrente com expressao cron (ex: "todo dia as 8h")
- "interval" : recorrente por intervalo em minutos (ex: "a cada 30 minutos")
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional


def schedule_message(
    user_phone: str,
    task_instructions: str,
    trigger_type: str,
    run_date: Optional[str] = None,
    cron_expression: Optional[str] = None,
    interval_minutes: Optional[int] = None,
) -> str:
    """
    Agenda uma mensagem proativa para ser enviada ao usuario.

    Args:
        user_phone: Numero de telefone do usuario (formato: 5511999998888).
        task_instructions: O que o agente deve fazer/dizer quando o job disparar.
                           Ex: "Envie as tarefas pendentes do usuario e a previsao do tempo."
        trigger_type: Tipo de gatilho — "date" (unico), "cron" (recorrente), "interval" (por intervalo).
        run_date: Obrigatorio se trigger_type="date". Data/hora ISO 8601 ou descricao relativa.
                  Ex: "2026-03-01T08:00:00", "in_5_minutes" (use datetime calculado pelo agente).
        cron_expression: Obrigatorio se trigger_type="cron". Expressao cron padrao.
                         Ex: "0 8 * * *" (todo dia as 8h), "0 9 * * 1-5" (seg-sex as 9h).
        interval_minutes: Obrigatorio se trigger_type="interval". Intervalo em minutos.
                          Ex: 30 (a cada 30 minutos).

    Returns:
        Confirmacao com o ID do job criado ou mensagem de erro.
    """
    try:
        from src.scheduler.engine import get_scheduler
        from src.scheduler.dispatcher import dispatch_proactive_message

        scheduler = get_scheduler()

        if trigger_type == "date":
            if not run_date:
                return "Para agendar um disparo unico, informe run_date com a data/hora no formato ISO 8601 (ex: '2026-03-01T08:30:00')."
            run_dt = datetime.fromisoformat(run_date)
            if run_dt.tzinfo is None:
                run_dt = run_dt.replace(tzinfo=timezone.utc)
            job = scheduler.add_job(
                dispatch_proactive_message,
                trigger="date",
                run_date=run_dt,
                kwargs={"user_phone": user_phone, "task_instructions": task_instructions},
                misfire_grace_time=300,
            )
            friendly_time = run_dt.strftime("%d/%m/%Y as %H:%M UTC")
            return f"Agendado! Vou disparar em {friendly_time}. ID do agendamento: {job.id}"

        elif trigger_type == "cron":
            if not cron_expression:
                return "Para agendamento recorrente tipo cron, informe cron_expression (ex: '0 8 * * *' para todo dia as 8h UTC)."
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
            return f"Agendamento recorrente criado! Expressao cron: '{cron_expression}' (UTC). ID: {job.id}"

        elif trigger_type == "interval":
            if not interval_minutes or interval_minutes <= 0:
                return "Para agendamento por intervalo, informe interval_minutes com um valor positivo."
            job = scheduler.add_job(
                dispatch_proactive_message,
                trigger="interval",
                minutes=interval_minutes,
                kwargs={"user_phone": user_phone, "task_instructions": task_instructions},
                misfire_grace_time=300,
            )
            return f"Agendamento por intervalo criado! A cada {interval_minutes} minuto(s). ID: {job.id}"

        else:
            return f"trigger_type invalido: '{trigger_type}'. Use 'date', 'cron' ou 'interval'."

    except Exception as e:
        return f"Erro ao criar agendamento: {e}"


def list_schedules(user_phone: str) -> str:
    """
    Lista todos os agendamentos ativos para o usuario.

    Args:
        user_phone: Numero de telefone do usuario.

    Returns:
        Lista formatada dos agendamentos ou mensagem informando que nao ha nenhum.
    """
    try:
        from src.scheduler.engine import get_scheduler

        scheduler = get_scheduler()
        jobs = scheduler.get_jobs()

        user_jobs = [
            job for job in jobs
            if job.kwargs.get("user_phone") == user_phone
        ]

        if not user_jobs:
            return "Voce nao tem nenhum agendamento ativo no momento."

        lines = [f"Voce tem {len(user_jobs)} agendamento(s) ativo(s):"]
        for job in user_jobs:
            instructions = job.kwargs.get("task_instructions", "")[:60]
            next_run = job.next_run_time
            next_run_str = next_run.strftime("%d/%m/%Y %H:%M UTC") if next_run else "aguardando"
            lines.append(f"• ID: {job.id} | Proximo disparo: {next_run_str} | Instrucao: {instructions}...")

        return "\n".join(lines)

    except Exception as e:
        return f"Erro ao listar agendamentos: {e}"


def cancel_schedule(job_id: str) -> str:
    """
    Cancela um agendamento pelo seu ID.

    Args:
        job_id: ID do agendamento retornado por schedule_message ou list_schedules.

    Returns:
        Confirmacao do cancelamento ou mensagem de erro.
    """
    try:
        from src.scheduler.engine import get_scheduler
        from apscheduler.jobstores.base import JobLookupError

        scheduler = get_scheduler()
        scheduler.remove_job(job_id)
        return f"Agendamento {job_id} cancelado com sucesso."

    except Exception as e:
        if "No job by the id" in str(e) or "JobLookupError" in type(e).__name__:
            return f"Nao encontrei nenhum agendamento com o ID '{job_id}'. Usa list_schedules pra ver os IDs ativos."
        return f"Erro ao cancelar agendamento {job_id}: {e}"

"""
Motor de agendamento singleton baseado em APScheduler com persistencia em SQLite.
Responsavel por manter os jobs ativos entre restarts do servidor.
"""
import logging
import os
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    """Retorna a instancia singleton do scheduler, criando-a se necessario."""
    global _scheduler
    if _scheduler is None:
        db_url = os.getenv("DATABASE_URL", "sqlite:///scheduler.db")
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+psycopg2://")
            
        jobstores = {
            "default": SQLAlchemyJobStore(
                url=db_url,
                engine_options={
                    "pool_pre_ping": True,
                    "pool_recycle": 300
                }
            )
        }
        _scheduler = BackgroundScheduler(jobstores=jobstores, timezone="America/Sao_Paulo")
    return _scheduler


def cleanup_old_messages():
    from src.db.session import get_db
    from src.db.models import ProcessedMessage
    from datetime import datetime, timezone, timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    with get_db() as db:
        db.query(ProcessedMessage).filter(ProcessedMessage.created_at < cutoff).delete(synchronize_session=False)

def start_scheduler():
    """Inicia o scheduler se ainda nao estiver rodando e reconcilia os dados."""
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        logger.info("Scheduler iniciado com sucesso.")
        
        scheduler.add_job(
            cleanup_old_messages,
            trigger="interval",
            hours=1,
            id="cleanup_processed_messages",
            replace_existing=True
        )
        
        from src.queue.worker import _run_worker_sync
        scheduler.add_job(
            _run_worker_sync,
            trigger="interval",
            seconds=5,
            id="task_queue_worker",
            replace_existing=True,
            misfire_grace_time=10
        )
        
        from src.queue.task_queue import recover_stale_tasks
        scheduler.add_job(
            recover_stale_tasks,
            trigger="interval",
            minutes=2,
            id="recover_stale_tasks",
            replace_existing=True,
            misfire_grace_time=30
        )
        
        from src.auth.otp import cleanup_expired_codes
        scheduler.add_job(
            cleanup_expired_codes,
            trigger="interval",
            minutes=5,
            id="cleanup_expired_otp_codes",
            replace_existing=True
        )
        
        from src.endpoints.whatsapp import flush_ready_buffers
        scheduler.add_job(
            flush_ready_buffers,
            trigger="interval",
            seconds=2,
            id="flush_whatsapp_buffer",
            replace_existing=True,
            misfire_grace_time=10
        )
        
        from src.social.fetcher import fetch_all_tracked_accounts
        scheduler.add_job(
            fetch_all_tracked_accounts,
            trigger="interval",
            hours=int(os.getenv("SOCIAL_FETCH_INTERVAL_HOURS", "6")),
            id="social_media_fetcher",
            replace_existing=True,
            misfire_grace_time=600,
        )

        reconcile_reminders()


def reconcile_reminders():
    """Garante que todos os reminders 'active' no banco tenham um job no APScheduler."""
    logger.info("Iniciando reconciliacao de reminders...")
    try:
        from src.models.reminders import list_all_active_reminders, mark_fired, update_apscheduler_job_id, update_status
        from src.scheduler.dispatcher import dispatch_proactive_message
        import zoneinfo
        from datetime import datetime, timezone, timedelta
        
        scheduler = get_scheduler()
        active_reminders = list_all_active_reminders()
        
        for r in active_reminders:
            reminder_id = r["id"]
            trigger_type = r.get("trigger_type")
            config = r.get("trigger_config", {})

            deterministic_id = f"reminder_{reminder_id}"
            if scheduler.get_job(deterministic_id):
                continue

            # Limpar job antigo (formato legado) se existir
            old_job_id = r.get("apscheduler_job_id")
            if old_job_id and old_job_id != deterministic_id:
                try:
                    scheduler.remove_job(old_job_id)
                    logger.info("Job legado %s removido para reminder %s.", old_job_id, reminder_id)
                except Exception:
                    pass

            logger.info("Reminder %s (%s) sem job ativo. Recriando...", reminder_id, trigger_type)

            user_tz_str = config.get("timezone", "America/Sao_Paulo")
            user_tz = zoneinfo.ZoneInfo(user_tz_str)

            job = None

            if trigger_type == "date":
                run_date_str = config.get("run_date")
                minutes_from_now = config.get("minutes_from_now")

                run_dt = None
                if run_date_str:
                    run_dt = datetime.fromisoformat(run_date_str) if isinstance(run_date_str, str) else run_date_str
                    if run_dt.tzinfo is None:
                        run_dt = run_dt.replace(tzinfo=user_tz)
                elif minutes_from_now is not None:
                    created_at_str = r.get("created_at")
                    if created_at_str:
                        created_at = datetime.fromisoformat(created_at_str) if isinstance(created_at_str, str) else created_at_str
                        if created_at.tzinfo is None:
                            created_at = created_at.replace(tzinfo=timezone.utc)
                        run_dt = created_at + timedelta(minutes=minutes_from_now)

                if run_dt:
                    if run_dt <= datetime.now(timezone.utc):
                        logger.info("Reminder %s 'date' ja passou da hora. Disparando agora...", reminder_id)
                        run_dt = datetime.now(timezone.utc) + timedelta(seconds=5)

                    job = scheduler.add_job(
                        dispatch_proactive_message,
                        trigger="date",
                        run_date=run_dt,
                        id=deterministic_id,
                        replace_existing=True,
                        kwargs={"reminder_id": reminder_id},
                        misfire_grace_time=300,
                    )
            elif trigger_type == "cron":
                cron_expr = config.get("cron_expression")
                if cron_expr:
                    parts = cron_expr.strip().split()
                    if len(parts) == 5:
                        job = scheduler.add_job(
                            dispatch_proactive_message,
                            trigger="cron",
                            minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4],
                            timezone=user_tz,
                            id=deterministic_id,
                            replace_existing=True,
                            kwargs={"reminder_id": reminder_id},
                            misfire_grace_time=300,
                        )
            elif trigger_type == "interval":
                interval_minutes = config.get("interval_minutes")
                if interval_minutes:
                    job = scheduler.add_job(
                        dispatch_proactive_message,
                        trigger="interval",
                        minutes=interval_minutes,
                        id=deterministic_id,
                        replace_existing=True,
                        kwargs={"reminder_id": reminder_id},
                        misfire_grace_time=300,
                    )
                    
            if job:
                logger.info("Reminder %s reconciliado com novo job_id: %s", reminder_id, job.id)
                update_apscheduler_job_id(reminder_id, job.id)
            else:
                logger.warning("Nao foi possivel recriar job para reminder %s. Marcando como cancelado.", reminder_id)
                update_status(reminder_id, "cancelled")
                
        logger.info("Reconciliacao finalizada.")
    except Exception as e:
        logger.error("Erro durante reconciliacao: %s", e)


def shutdown_scheduler():
    """Para o scheduler de forma segura."""
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler encerrado.")

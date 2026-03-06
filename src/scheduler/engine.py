"""
Motor de agendamento singleton baseado em APScheduler com persistencia em SQLite.
Responsavel por manter os jobs ativos entre restarts do servidor.
"""
import os
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

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
    from src.config.system_config import _get_pg_engine, _get_sqlite_conn
    engine = _get_pg_engine()
    if engine:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM processed_messages WHERE created_at < NOW() - INTERVAL '24 hours'"))
            conn.commit()
    else:
        with _get_sqlite_conn() as conn:
            conn.execute("DELETE FROM processed_messages WHERE created_at < datetime('now', '-24 hours')")
            conn.commit()

def start_scheduler():
    """Inicia o scheduler se ainda nao estiver rodando e reconcilia os dados."""
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        print("[SCHEDULER] Iniciado com sucesso.")
        
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
        
        from src.endpoints.whatsapp import flush_ready_buffers
        scheduler.add_job(
            flush_ready_buffers,
            trigger="interval",
            seconds=2,
            id="flush_whatsapp_buffer",
            replace_existing=True,
            misfire_grace_time=10
        )
        
        reconcile_reminders()


def reconcile_reminders():
    """Garante que todos os reminders 'active' no banco tenham um job no APScheduler."""
    print("[SCHEDULER] Iniciando reconciliacao de reminders...")
    try:
        from src.models.reminders import list_all_active_reminders, mark_fired, update_apscheduler_job_id, update_status
        from src.scheduler.dispatcher import dispatch_proactive_message
        import zoneinfo
        from datetime import datetime, timezone, timedelta
        
        scheduler = get_scheduler()
        active_reminders = list_all_active_reminders()
        
        for r in active_reminders:
            reminder_id = r["id"]
            job_id = r.get("apscheduler_job_id")
            trigger_type = r.get("trigger_type")
            config = r.get("trigger_config", {})
            
            # Verificar se o job ja existe no APScheduler
            if job_id and scheduler.get_job(job_id):
                continue
                
            print(f"[SCHEDULER] Reminder {reminder_id} ({trigger_type}) sem job ativo. Recriando...")
            
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
                        print(f"[SCHEDULER] Reminder {reminder_id} 'date' ja passou da hora. Disparando agora...")
                        run_dt = datetime.now(timezone.utc) + timedelta(seconds=5)
                        
                    job = scheduler.add_job(
                        dispatch_proactive_message,
                        trigger="date",
                        run_date=run_dt,
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
                        kwargs={"reminder_id": reminder_id},
                        misfire_grace_time=300,
                    )
                    
            if job:
                print(f"[SCHEDULER] Reminder {reminder_id} reconciliado com novo job_id: {job.id}")
                update_apscheduler_job_id(reminder_id, job.id)
            else:
                print(f"[SCHEDULER] Nao foi possivel recriar job para reminder {reminder_id}. Marcando como cancelado.")
                update_status(reminder_id, "cancelled")
                
        print("[SCHEDULER] Reconciliacao finalizada.")
    except Exception as e:
        print(f"[SCHEDULER] Erro durante reconciliacao: {e}")


def shutdown_scheduler():
    """Para o scheduler de forma segura."""
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)
        print("[SCHEDULER] Encerrado.")

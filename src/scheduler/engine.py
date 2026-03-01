"""
Motor de agendamento singleton baseado em APScheduler com persistencia em SQLite.
Responsavel por manter os jobs ativos entre restarts do servidor.
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    """Retorna a instancia singleton do scheduler, criando-a se necessario."""
    global _scheduler
    if _scheduler is None:
        jobstores = {
            "default": SQLAlchemyJobStore(url="sqlite:///scheduler.db")
        }
        _scheduler = BackgroundScheduler(jobstores=jobstores, timezone="UTC")
    return _scheduler


def start_scheduler():
    """Inicia o scheduler se ainda nao estiver rodando."""
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        print("[SCHEDULER] Iniciado com sucesso.")


def shutdown_scheduler():
    """Para o scheduler de forma segura."""
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)
        print("[SCHEDULER] Encerrado.")

import json
import logging
import uuid
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

from sqlalchemy import func, text

from src.db.session import get_db, _is_sqlite
from src.db.models import BackgroundTask

logger = logging.getLogger(__name__)
from src.config.system_config import get_config
from src.config.feature_gates import get_plan_limit, is_admin_unlimited, get_user_plan_type

_limit_flags: Dict[str, dict] = {}
_limit_lock = threading.Lock()


def set_limit_flag(user_id: str, info: dict):
    with _limit_lock:
        _limit_flags[user_id] = info


def pop_limit_flag(user_id: str) -> Optional[dict]:
    with _limit_lock:
        return _limit_flags.pop(user_id, None)


def get_usage_status(user_id: str) -> dict:
    """
    Snapshot estruturado do estado atual de limites do usuário.
    """
    plan_type = get_user_plan_type(user_id)
    _is_admin = is_admin_unlimited(user_id)

    status = {
        "plan_type": plan_type,
        "effective_plan": "admin" if _is_admin else plan_type,
        "is_admin_bypass": _is_admin,
        "max_daily": None,
        "daily_used": None,
        "daily_remaining": None,
        "is_limited": False,
        "limit_message": "",
        "context": "",
    }

    if _is_admin:
        status["context"] = (
            "[STATUS LIMITES: Usuario admin com bypass ativo. "
            "Limites diarios NAO se aplicam agora. "
            "Ignore mensagens antigas sobre limite atingido.]"
        )
        return status

    max_daily = get_plan_limit(user_id, "max_tasks_per_user_daily", 5)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    with get_db() as session:
        daily = session.query(func.count(BackgroundTask.id)).filter(
            BackgroundTask.user_id == user_id,
            BackgroundTask.created_at >= cutoff,
        ).scalar() or 0

    remaining = max(0, max_daily - int(daily))
    status["max_daily"] = max_daily
    status["daily_used"] = int(daily)
    status["daily_remaining"] = remaining
    status["is_limited"] = remaining == 0

    if status["is_limited"]:
        if plan_type in ("trial", "free"):
            status["limit_message"] = (
                f"Poxa, seu limite de {max_daily} gerações de hoje acabou no plano gratuito. "
                "Mas você pode virar Premium agora e repor seu limite!"
            )
        else:
            status["limit_message"] = f"Limite diário de {max_daily} gerações atingido. Tente novamente amanhã!"

        status["context"] = (
            f"[STATUS LIMITES: Plano efetivo {plan_type}. "
            f"Limite diario de {max_daily} geracoes atingido. 0 restantes.]"
        )
        return status

    status["context"] = (
        f"[STATUS LIMITES: Plano efetivo {plan_type}. "
        f"{remaining}/{max_daily} geracoes restantes nas ultimas 24h.]"
    )
    return status


def get_usage_context(user_id: str) -> str:
    """
    Retorna um bloco textual curto com o estado ATUAL de limites.
    Esse contexto deve ser injetado no prompt para evitar que o LLM
    assuma limites com base em mensagens antigas do histórico.
    """
    return get_usage_status(user_id)["context"]


def check_daily_limit(user_id: str) -> Optional[str]:
    """
    Verifica se o usuário atingiu o limite diário de runs.
    Retorna mensagem de erro se atingiu, None se pode prosseguir.
    Seta um flag thread-safe que _process_text pode ler para enviar resposta determinística.
    """
    if is_admin_unlimited(user_id):
        return None

    plan_type = get_user_plan_type(user_id)
    max_daily = get_plan_limit(user_id, "max_tasks_per_user_daily", 5)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    with get_db() as session:
        daily = session.query(func.count(BackgroundTask.id)).filter(
            BackgroundTask.user_id == user_id,
            BackgroundTask.created_at >= cutoff,
        ).scalar()

    if daily >= max_daily:
        if plan_type in ("trial", "free"):
            message = (
                f"Poxa, seu limite de {max_daily} gerações de hoje acabou no plano gratuito. "
                "Mas você pode virar Premium agora e repor seu limite!"
            )
        else:
            message = f"Limite diário de {max_daily} gerações atingido. Tente novamente amanhã!"

        set_limit_flag(user_id, {"message": message, "plan_type": plan_type})
        return message

    return None


def enqueue_task(user_id: str, task_type: str, channel: str, payload: Dict[str, Any]) -> dict:
    _is_admin = is_admin_unlimited(user_id)
    plan_type = get_user_plan_type(user_id)

    with get_db() as session:
        now_iso = datetime.now(timezone.utc).isoformat()

        if not _is_admin:
            max_concurrent = get_plan_limit(user_id, "max_tasks_per_user", 2)
            max_daily = get_plan_limit(user_id, "max_tasks_per_user_daily", 5)

            concurrent = session.query(func.count(BackgroundTask.id)).filter(
                BackgroundTask.user_id == user_id,
                BackgroundTask.status.in_(["pending", "processing"]),
            ).scalar()

            if concurrent >= max_concurrent:
                return {"status": "limit_reached", "pending_count": concurrent}

            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            daily = session.query(func.count(BackgroundTask.id)).filter(
                BackgroundTask.user_id == user_id,
                BackgroundTask.created_at >= cutoff,
            ).scalar()

            if daily >= max_daily:
                # Mantem comportamento deterministico no websocket: mesmo que o agente
                # parafraseie o retorno da tool, o frontend recebe o evento limit_reached.
                if plan_type in ("trial", "free"):
                    message = (
                        f"Poxa, seu limite de {max_daily} gerações de hoje acabou no plano gratuito. "
                        "Mas você pode virar Premium agora e repor seu limite!"
                    )
                else:
                    message = f"Limite diário de {max_daily} gerações atingido. Tente novamente amanhã!"
                set_limit_flag(user_id, {"message": message, "plan_type": plan_type})
                return {"status": "daily_limit", "daily_limit": max_daily}

        cancelled = session.query(BackgroundTask).filter(
            BackgroundTask.user_id == user_id,
            BackgroundTask.task_type == task_type,
            BackgroundTask.status == "pending",
        ).update({"status": "cancelled", "updated_at": now_iso}, synchronize_session=False)

        if cancelled:
            logger.info("Canceladas %s tasks pendentes (%s) do usuario %s", cancelled, task_type, user_id)

        task = BackgroundTask(
            id=str(uuid.uuid4()),
            user_id=user_id,
            task_type=task_type,
            channel=channel,
            payload=json.dumps(payload),
            status="pending",
            created_at=now_iso,
            updated_at=now_iso,
        )
        session.add(task)
        session.flush()

        position = session.query(func.count(BackgroundTask.id)).filter(
            BackgroundTask.status == "pending",
            BackgroundTask.id != task.id,
        ).scalar() + 1

    avg_time = _get_avg_processing_time()
    max_global = int(get_config("max_global_processing", "3"))
    estimated_seconds = (position * avg_time) / max_global

    if estimated_seconds < 60:
        est_str = "menos de 1 minuto"
    elif estimated_seconds < 120:
        est_str = "1 a 2 minutos"
    else:
        est_str = f"~{int(estimated_seconds // 60)} minutos"

    return {
        "status": "queued",
        "task_id": task.id,
        "position": position,
        "estimated_wait": est_str,
    }


def _get_avg_processing_time() -> float:
    with get_db() as session:
        tasks = (
            session.query(BackgroundTask.started_at, BackgroundTask.completed_at)
            .filter(
                BackgroundTask.status == "done",
                BackgroundTask.completed_at.isnot(None),
                BackgroundTask.started_at.isnot(None),
            )
            .order_by(BackgroundTask.completed_at.desc())
            .limit(20)
            .all()
        )

    if not tasks:
        return 90.0

    total = 0.0
    count = 0
    for started, completed in tasks:
        try:
            s = datetime.fromisoformat(started)
            c = datetime.fromisoformat(completed)
            total += (c - s).total_seconds()
            count += 1
        except (ValueError, TypeError):
            continue

    return total / count if count > 0 else 90.0


def claim_next_task() -> Optional[dict]:
    with get_db() as session:
        if _is_sqlite():
            task = (
                session.query(BackgroundTask)
                .filter(BackgroundTask.status == "pending")
                .order_by(BackgroundTask.created_at.asc())
                .first()
            )
            if task:
                task.status = "processing"
                task.started_at = datetime.now(timezone.utc).isoformat()
                task.attempts = (task.attempts or 0) + 1
                session.flush()
                return {
                    "id": task.id,
                    "user_id": task.user_id,
                    "task_type": task.task_type,
                    "channel": task.channel,
                    "payload": json.loads(task.payload) if isinstance(task.payload, str) else task.payload,
                    "attempts": task.attempts,
                }
        else:
            row = session.execute(text(
                "UPDATE background_tasks "
                "SET status = 'processing', started_at = NOW(), attempts = attempts + 1 "
                "WHERE id = ("
                "  SELECT id FROM background_tasks "
                "  WHERE status = 'pending' "
                "  ORDER BY created_at ASC "
                "  FOR UPDATE SKIP LOCKED "
                "  LIMIT 1"
                ") "
                "RETURNING id, user_id, task_type, channel, payload, attempts"
            )).fetchone()
            if row:
                return {
                    "id": str(row[0]),
                    "user_id": row[1],
                    "task_type": row[2],
                    "channel": row[3],
                    "payload": json.loads(row[4]) if isinstance(row[4], str) else row[4],
                    "attempts": row[5],
                }
    return None


def complete_task(task_id: str, result: dict):
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as session:
        task = session.get(BackgroundTask, task_id)
        if task:
            task.status = "done"
            task.result = json.dumps(result)
            task.completed_at = now_iso
            task.updated_at = now_iso


def fail_task(task_id: str, error: str):
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_db() as session:
        task = session.get(BackgroundTask, task_id)
        if task:
            if (task.attempts or 0) < 3:
                task.status = "pending"
            else:
                task.status = "failed"
                task.result = json.dumps({"error": error})
            task.updated_at = now_iso


def is_task_cancelled(task_id: str) -> bool:
    with get_db() as session:
        task = session.get(BackgroundTask, task_id)
        if not task:
            return False
        return task.status in ("cancelled", "failed")


def cancel_task_by_carousel(user_id: str, carousel_id: str) -> Optional[str]:
    """
    Cancel a background task associated with a carousel.
    Sets task status to 'failed' (consistent with admin cancel).
    Also updates the carousel status to 'failed'.
    Returns task_id if cancelled, None otherwise.
    """
    from src.models.carousel import update_carousel_status

    now_iso = datetime.now(timezone.utc).isoformat()
    task_id = None

    with get_db() as session:
        # Find the task by scanning payload for carousel_id
        tasks = session.query(BackgroundTask).filter(
            BackgroundTask.user_id == user_id,
            BackgroundTask.task_type == "carousel",
            BackgroundTask.status.in_(["pending", "processing"]),
        ).all()

        for task in tasks:
            try:
                payload = json.loads(task.payload) if isinstance(task.payload, str) else task.payload
                if payload.get("carousel_id") == carousel_id:
                    task.status = "failed"
                    task.result = json.dumps({"error": "cancelled by user"})
                    task.updated_at = now_iso
                    task_id = task.id
                    break
            except (json.JSONDecodeError, TypeError):
                continue

    # Update carousel status regardless (covers edge case where task already finished)
    update_carousel_status(carousel_id, "failed")

    if task_id:
        logger.info("Task %s cancelada pelo usuario (carousel %s)", task_id, carousel_id)

    return task_id


def count_processing_tasks() -> int:
    with get_db() as session:
        return session.query(func.count(BackgroundTask.id)).filter(
            BackgroundTask.status == "processing",
        ).scalar()


def recover_stale_tasks():
    timeout = int(get_config("task_timeout_minutes", "5"))
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=timeout)).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_db() as session:
        session.execute(
            text(
                "UPDATE background_tasks "
                "SET status = CASE WHEN attempts < 3 THEN 'pending' ELSE 'failed' END, "
                "    result = CASE WHEN attempts >= 3 THEN :error_json ELSE result END, "
                "    updated_at = :now "
                "WHERE status = 'processing' AND started_at < :cutoff"
            ),
            {"now": now_iso, "cutoff": cutoff, "error_json": '{"error": "timeout"}'},
        )

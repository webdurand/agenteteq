from datetime import datetime
from typing import Optional

from src.db.session import get_db
from src.db.models import Task
import logging

logger = logging.getLogger(__name__)

def _init_db():
    pass

def add_task(
    user_id: str,
    title: str,
    description: str = "",
    due_date: str = "",
    location: str = "",
    notes: str = "",
    priority: str = "",
    category: str = "",
    channel: str = "unknown",
) -> str:
    """
    Adiciona uma tarefa à lista do usuário.

    Args:
        user_id: Número de telefone do usuário (identificador único).
        title: Título curto e descritivo da tarefa.
        description: Descrição mais detalhada da tarefa (opcional).
        due_date: Prazo ou data/hora no formato ISO 8601 ou texto livre (ex: '2026-03-02 10:00'). Opcional.
        location: Endereço ou local relacionado à tarefa (opcional).
        notes: Informações adicionais ou observações (opcional).
        priority: Prioridade — 'high', 'medium' ou 'low'. Opcional — infira do contexto se o usuário não especificar.
        category: Categoria/label (ex: 'Trabalho', 'Pessoal', 'Conteúdo'). Opcional — infira do contexto.
        channel: Canal de origem (web, whatsapp, etc).

    Returns:
        Mensagem de confirmação com o ID da tarefa criada.
    """
    logger.info("add_task | user=%s | titulo='%s' | prazo='%s' | prioridade='%s' | categoria='%s'", user_id, title, due_date, priority, category)
    created_at = datetime.now().isoformat()
    try:
        with get_db() as db:
            task = Task(
                user_id=user_id,
                title=title,
                description=description,
                due_date=due_date,
                location=location,
                notes=notes,
                priority=priority or None,
                category=category or None,
                status="pending",
                created_at=created_at,
            )
            db.add(task)
            db.flush()
            task_id = task.id

        logger.info("Tarefa #%s adicionada com sucesso: '%s'", task_id, title)
        from src.events import emit_event_sync
        emit_event_sync(user_id, "task_updated")
        from src.events_broadcast import emit_action_log_sync
        emit_action_log_sync(user_id, "Tarefa criada", title, channel)
        return f"Tarefa #{task_id} adicionada com sucesso: '{title}'."
    except Exception as e:
        logger.error("Erro ao adicionar tarefa: %s", e)
        return f"Erro ao adicionar tarefa: {e}"

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_PRIORITY_EMOJI = {"high": "🔴", "medium": "🟡", "low": "🟢"}

def list_tasks(user_id: str, status: str = "pending", category: str = "") -> str:
    """
    Lista as tarefas do usuário.

    Args:
        user_id: Número de telefone do usuário.
        status: Filtro de status — 'pending' para tarefas abertas, 'done' para concluídas.
                Use 'all' para listar todas. Padrão: 'pending'.
        category: Filtrar por categoria (ex: 'Trabalho'). Opcional — se vazio lista todas.

    Returns:
        Lista formatada das tarefas ou mensagem informando que não há tarefas.
    """
    logger.info("list_tasks | user=%s | status=%s | category=%s", user_id, status, category)
    try:
        with get_db() as db:
            q = db.query(Task).filter(Task.user_id == user_id)
            if status != "all":
                q = q.filter(Task.status == status)
            if category:
                q = q.filter(Task.category == category)
            q = q.order_by(Task.created_at.asc())
            rows = q.all()

        if not rows:
            label = {"pending": "abertas", "done": "concluídas", "all": ""}.get(status, status)
            return f"Nenhuma tarefa {label} encontrada." if label else "Nenhuma tarefa encontrada."

        # Sort by priority (high first), then by created_at
        rows.sort(key=lambda t: (_PRIORITY_ORDER.get(t.priority or "", 9), t.created_at or ""))

        lines = []
        for t in rows:
            emoji = "✅" if t.status == "done" else "🔲"
            prio = _PRIORITY_EMOJI.get(t.priority or "", "")
            line = f"{emoji} #{t.id} — {t.title}"
            if prio:
                line += f" {prio}"
            if t.category:
                line += f" [{t.category}]"
            if t.due_date:
                line += f"\n   📅 Prazo: {t.due_date}"
            if t.location:
                line += f"\n   📍 Local: {t.location}"
            if t.description:
                line += f"\n   📝 {t.description}"
            if t.notes:
                line += f"\n   💬 {t.notes}"
            lines.append(line)

        return "\n\n".join(lines)
    except Exception as e:
        logger.error("Erro ao listar tarefas: %s", e)
        return f"Erro ao listar tarefas: {e}"

def get_tasks(user_id: str, status: str = "pending", limit: int = 0, offset: int = 0) -> dict:
    """
    Lista as tarefas do usuário. Se limit > 0, pagina com has_more.
    Quando status="all" com paginação, retorna TODAS as pendentes + done paginado,
    garantindo que tarefas pendentes recentes nunca fiquem escondidas.
    """
    try:
        with get_db() as db:
            if status == "all" and limit > 0:
                pending = db.query(Task).filter(
                    Task.user_id == user_id, Task.status == "pending"
                ).order_by(Task.created_at.asc()).all()

                done_q = db.query(Task).filter(
                    Task.user_id == user_id, Task.status == "done"
                ).order_by(Task.created_at.asc())
                done_q = done_q.limit(limit + 1).offset(offset)
                done_rows = done_q.all()

                has_more = len(done_rows) > limit
                if has_more:
                    done_rows = done_rows[:limit]

                tasks = [t.to_dict() for t in pending] + [t.to_dict() for t in done_rows]
                return {"tasks": tasks, "has_more": has_more}

            fetch_limit = limit + 1 if limit > 0 else None
            q = db.query(Task).filter(Task.user_id == user_id)
            if status != "all":
                q = q.filter(Task.status == status)
            q = q.order_by(Task.created_at.asc())
            if fetch_limit:
                q = q.limit(fetch_limit).offset(offset)
            rows = q.all()

        tasks = [t.to_dict() for t in rows]
        has_more = False
        if limit > 0 and len(tasks) > limit:
            has_more = True
            tasks = tasks[:limit]

        return {"tasks": tasks, "has_more": has_more}
    except Exception as e:
        logger.error("Erro ao obter tarefas: %s", e)
        return {"tasks": [], "has_more": False}

def complete_task(user_id: str, task_id: int, channel: str = "unknown") -> str:
    """
    Marca uma tarefa como concluída.

    Args:
        user_id: Número de telefone do usuário (garante que só pode concluir suas próprias tarefas).
        task_id: ID numérico da tarefa a ser marcada como concluída.
        channel: Canal de origem (web, whatsapp, etc).

    Returns:
        Confirmação ou mensagem de erro.
    """
    logger.info("complete_task | user=%s | task_id=%s", user_id, task_id)
    try:
        with get_db() as db:
            task = db.query(Task).filter(Task.id == task_id, Task.user_id == user_id).first()
            if not task:
                return f"Tarefa #{task_id} não encontrada."
            task.status = "done"

        from src.events import emit_event_sync
        emit_event_sync(user_id, "task_updated")
        from src.events_broadcast import emit_action_log_sync
        emit_action_log_sync(user_id, "Tarefa concluida", f"#{task_id}", channel)
        return f"Tarefa #{task_id} marcada como concluída!"
    except Exception as e:
        logger.error("Erro ao concluir tarefa: %s", e)
        return f"Erro ao concluir tarefa: {e}"

def reopen_task(user_id: str, task_id: int) -> str:
    """
    Marca uma tarefa como pendente (incompleta).

    Args:
        user_id: Número de telefone do usuário (garante que só pode reabrir suas próprias tarefas).
        task_id: ID numérico da tarefa a ser marcada como pendente.

    Returns:
        Confirmação ou mensagem de erro.
    """
    logger.info("reopen_task | user=%s | task_id=%s", user_id, task_id)
    try:
        with get_db() as db:
            task = db.query(Task).filter(Task.id == task_id, Task.user_id == user_id).first()
            if not task:
                return f"Tarefa #{task_id} não encontrada."
            task.status = "pending"

        from src.events import emit_event_sync
        emit_event_sync(user_id, "task_updated")
        return f"Tarefa #{task_id} marcada como pendente!"
    except Exception as e:
        logger.error("Erro ao reabrir tarefa: %s", e)
        return f"Erro ao reabrir tarefa: {e}"

def delete_task(user_id: str, task_id: int) -> str:
    """
    Remove uma tarefa da lista do usuário.

    Args:
        user_id: Número de telefone do usuário (garante que só pode remover suas próprias tarefas).
        task_id: ID numérico da tarefa a ser removida.

    Returns:
        Confirmação ou mensagem de erro.
    """
    logger.info("delete_task | user=%s | task_id=%s", user_id, task_id)
    try:
        with get_db() as db:
            task = db.query(Task).filter(Task.id == task_id, Task.user_id == user_id).first()
            if not task:
                return f"Tarefa #{task_id} não encontrada."
            db.delete(task)

        from src.events import emit_event_sync

        emit_event_sync(user_id, "task_updated")
        return f"Tarefa #{task_id} removida com sucesso."
    except Exception as e:
        logger.error("Erro ao remover tarefa: %s", e)
        return f"Erro ao remover tarefa: {e}"

def create_task_tools(user_id: str, channel: str = "unknown"):
    """
    Factory que cria as tools de tarefas com o user_id pre-injetado via closure.
    O LLM nunca precisa fornecer ou conhecer o user_id — identificacao deterministica
    pelo numero de telefone que chegou no webhook.

    Args:
        user_id: Numero de telefone do usuario (session_id).
        channel: Canal de origem (web, whatsapp, etc).

    Returns:
        Tuple com (add_task_tool, list_tasks_tool, complete_task_tool, reopen_task_tool, delete_task_tool).
    """

    def add_task_tool(
        title: str,
        description: str = "",
        due_date: str = "",
        location: str = "",
        notes: str = "",
        priority: str = "",
        category: str = "",
    ) -> str:
        """
        Adiciona uma tarefa à lista do usuário.

        Args:
            title: Título curto e descritivo da tarefa.
            description: Descrição mais detalhada da tarefa (opcional).
            due_date: Prazo ou data/hora no formato ISO 8601 ou texto livre (ex: '2026-03-02 10:00'). Opcional.
            location: Endereço ou local relacionado à tarefa (opcional).
            notes: Informações adicionais ou observações (opcional).
            priority: Prioridade — 'high', 'medium' ou 'low'. Opcional — infira do contexto se o usuário não especificar.
            category: Categoria/label (ex: 'Trabalho', 'Pessoal', 'Conteúdo'). Opcional — infira do contexto.

        Returns:
            Mensagem de confirmação com o ID da tarefa criada.
        """
        return add_task(user_id, title, description, due_date, location, notes, priority=priority, category=category, channel=channel)

    def list_tasks_tool(status: str = "pending", category: str = "") -> str:
        """
        Lista as tarefas do usuário.

        Args:
            status: Filtro de status — 'pending' para tarefas abertas, 'done' para concluídas.
                    Use 'all' para listar todas. Padrão: 'pending'.
            category: Filtrar por categoria (ex: 'Trabalho'). Opcional — se vazio lista todas.

        Returns:
            Lista formatada das tarefas ou mensagem informando que não há tarefas.
        """
        return list_tasks(user_id, status, category=category)

    def complete_task_tool(task_id: int) -> str:
        """
        Marca uma tarefa como concluída.

        Args:
            task_id: ID numérico da tarefa a ser marcada como concluída.

        Returns:
            Confirmação ou mensagem de erro.
        """
        return complete_task(user_id, task_id, channel=channel)

    def reopen_task_tool(task_id: int) -> str:
        """
        Marca uma tarefa como pendente (incompleta).

        Args:
            task_id: ID numérico da tarefa a ser marcada como pendente.

        Returns:
            Confirmação ou mensagem de erro.
        """
        return reopen_task(user_id, task_id)

    def delete_task_tool(task_id: int) -> str:
        """
        Remove uma tarefa da lista do usuário.

        Args:
            task_id: ID numérico da tarefa a ser removida.

        Returns:
            Confirmação ou mensagem de erro.
        """
        return delete_task(user_id, task_id)

    return add_task_tool, list_tasks_tool, complete_task_tool, reopen_task_tool, delete_task_tool

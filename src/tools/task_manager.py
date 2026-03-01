import os
import sqlite3
from datetime import datetime
from typing import Optional

# ---------------------------------------------------------------------------
# Conexão: usa NeonDB (PostgreSQL) quando DATABASE_URL estiver configurado,
# caso contrário cai para SQLite local — mesmo padrão do identity.py.
# ---------------------------------------------------------------------------

def _get_db_url() -> Optional[str]:
    url = os.getenv("DATABASE_URL")
    if not url:
        return None
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://")
    return url


def _use_postgres() -> bool:
    return bool(os.getenv("DATABASE_URL"))


def _get_pg_engine():
    from sqlalchemy import create_engine
    return create_engine(_get_db_url())


def _get_sqlite_conn():
    return sqlite3.connect("users.db")


def _init_db():
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS tasks (
                        id SERIAL PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        description TEXT,
                        due_date TEXT,
                        location TEXT,
                        notes TEXT,
                        status TEXT DEFAULT 'pending',
                        created_at TEXT NOT NULL
                    )
                """))
                conn.commit()
        else:
            conn = _get_sqlite_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    due_date TEXT,
                    location TEXT,
                    notes TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT NOT NULL
                )
            """)
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[TASKS] Erro ao inicializar banco: {e}")


def add_task(
    user_id: str,
    title: str,
    description: str = "",
    due_date: str = "",
    location: str = "",
    notes: str = "",
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

    Returns:
        Mensagem de confirmação com o ID da tarefa criada.
    """
    print(f"[TASKS] add_task | user={user_id} | titulo='{title}' | prazo='{due_date}' | local='{location}'")
    _init_db()
    created_at = datetime.now().isoformat()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                result = conn.execute(
                    text("""
                        INSERT INTO tasks (user_id, title, description, due_date, location, notes, status, created_at)
                        VALUES (:user_id, :title, :description, :due_date, :location, :notes, 'pending', :created_at)
                        RETURNING id
                    """),
                    {
                        "user_id": user_id, "title": title, "description": description,
                        "due_date": due_date, "location": location, "notes": notes,
                        "created_at": created_at,
                    }
                )
                task_id = result.fetchone()[0]
                conn.commit()
        else:
            conn = _get_sqlite_conn()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO tasks (user_id, title, description, due_date, location, notes, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (user_id, title, description, due_date, location, notes, created_at),
            )
            task_id = cursor.lastrowid
            conn.commit()
            conn.close()
        print(f"[TASKS] Tarefa #{task_id} adicionada com sucesso: '{title}'")
        return f"Tarefa #{task_id} adicionada com sucesso: '{title}'."
    except Exception as e:
        print(f"[TASKS] Erro ao adicionar tarefa: {e}")
        return f"Erro ao adicionar tarefa: {e}"


def list_tasks(user_id: str, status: str = "pending") -> str:
    """
    Lista as tarefas do usuário.

    Args:
        user_id: Número de telefone do usuário.
        status: Filtro de status — 'pending' para tarefas abertas, 'done' para concluídas.
                Use 'all' para listar todas. Padrão: 'pending'.

    Returns:
        Lista formatada das tarefas ou mensagem informando que não há tarefas.
    """
    print(f"[TASKS] list_tasks | user={user_id} | status={status}")
    _init_db()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                if status == "all":
                    rows = conn.execute(
                        text("SELECT id, title, description, due_date, location, notes, status FROM tasks WHERE user_id = :u ORDER BY created_at ASC"),
                        {"u": user_id}
                    ).fetchall()
                else:
                    rows = conn.execute(
                        text("SELECT id, title, description, due_date, location, notes, status FROM tasks WHERE user_id = :u AND status = :s ORDER BY created_at ASC"),
                        {"u": user_id, "s": status}
                    ).fetchall()
        else:
            conn = _get_sqlite_conn()
            c = conn.cursor()
            if status == "all":
                c.execute(
                    "SELECT id, title, description, due_date, location, notes, status FROM tasks WHERE user_id = ? ORDER BY created_at ASC",
                    (user_id,)
                )
            else:
                c.execute(
                    "SELECT id, title, description, due_date, location, notes, status FROM tasks WHERE user_id = ? AND status = ? ORDER BY created_at ASC",
                    (user_id, status)
                )
            rows = c.fetchall()
            conn.close()

        if not rows:
            label = {"pending": "abertas", "done": "concluídas", "all": ""}.get(status, status)
            return f"Nenhuma tarefa {label} encontrada." if label else "Nenhuma tarefa encontrada."

        lines = []
        for row in rows:
            task_id, title, description, due_date, location, notes, task_status = row
            emoji = "✅" if task_status == "done" else "🔲"
            line = f"{emoji} #{task_id} — {title}"
            if due_date:
                line += f"\n   📅 Prazo: {due_date}"
            if location:
                line += f"\n   📍 Local: {location}"
            if description:
                line += f"\n   📝 {description}"
            if notes:
                line += f"\n   💬 {notes}"
            lines.append(line)

        return "\n\n".join(lines)
    except Exception as e:
        print(f"[TASKS] Erro ao listar tarefas: {e}")
        return f"Erro ao listar tarefas: {e}"


def complete_task(user_id: str, task_id: int) -> str:
    """
    Marca uma tarefa como concluída.

    Args:
        user_id: Número de telefone do usuário (garante que só pode concluir suas próprias tarefas).
        task_id: ID numérico da tarefa a ser marcada como concluída.

    Returns:
        Confirmação ou mensagem de erro.
    """
    print(f"[TASKS] complete_task | user={user_id} | task_id={task_id}")
    _init_db()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                result = conn.execute(
                    text("UPDATE tasks SET status = 'done' WHERE id = :id AND user_id = :u"),
                    {"id": task_id, "u": user_id}
                )
                conn.commit()
                affected = result.rowcount
        else:
            conn = _get_sqlite_conn()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE tasks SET status = 'done' WHERE id = ? AND user_id = ?",
                (task_id, user_id)
            )
            conn.commit()
            affected = cursor.rowcount
            conn.close()

        if affected == 0:
            return f"Tarefa #{task_id} não encontrada."
        return f"Tarefa #{task_id} marcada como concluída!"
    except Exception as e:
        print(f"[TASKS] Erro ao concluir tarefa: {e}")
        return f"Erro ao concluir tarefa: {e}"


def delete_task(user_id: str, task_id: int) -> str:
    """
    Remove uma tarefa da lista do usuário.

    Args:
        user_id: Número de telefone do usuário (garante que só pode remover suas próprias tarefas).
        task_id: ID numérico da tarefa a ser removida.

    Returns:
        Confirmação ou mensagem de erro.
    """
    print(f"[TASKS] delete_task | user={user_id} | task_id={task_id}")
    _init_db()
    try:
        if _use_postgres():
            from sqlalchemy import text
            engine = _get_pg_engine()
            with engine.connect() as conn:
                result = conn.execute(
                    text("DELETE FROM tasks WHERE id = :id AND user_id = :u"),
                    {"id": task_id, "u": user_id}
                )
                conn.commit()
                affected = result.rowcount
        else:
            conn = _get_sqlite_conn()
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM tasks WHERE id = ? AND user_id = ?",
                (task_id, user_id)
            )
            conn.commit()
            affected = cursor.rowcount
            conn.close()

        if affected == 0:
            return f"Tarefa #{task_id} não encontrada."
        return f"Tarefa #{task_id} removida com sucesso."
    except Exception as e:
        print(f"[TASKS] Erro ao remover tarefa: {e}")
        return f"Erro ao remover tarefa: {e}"

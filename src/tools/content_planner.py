"""
Content calendar tools for the agent.

Factory function creates tools with user_id pre-injected via closure,
following the same pattern as task_manager.py and social_monitor.py.
"""

import logging

from src.models.content_plans import (
    create_content_plan as db_create,
    list_content_plans as db_list,
    update_content_plan as db_update,
    delete_content_plan as db_delete,
)

logger = logging.getLogger(__name__)


def create_content_planner_tools(user_id: str, notifier=None):
    """Factory that creates content planner tools with user_id pre-injected."""

    def _notify(msg: str) -> None:
        if notifier:
            notifier.notify(msg)

    def plan_content(
        title: str,
        content_type: str = "post",
        platform: str = "instagram",
        scheduled_at: str = "",
        description: str = "",
        content_pillar: str = "",
    ) -> str:
        """
        Adicionar um conteudo ao calendario de publicacoes.
        Use quando o usuario quiser planejar um post, carrossel, video ou qualquer conteudo.

        Args:
            title: Titulo ou tema do conteudo (ex: "5 dicas de fotografia").
            content_type: Tipo de conteudo: post, carousel, video, reels, blog.
            platform: Plataforma alvo: instagram, youtube, blog. Pode separar com virgula para multiplas.
            scheduled_at: Data/hora planejada para publicacao (ISO 8601). Opcional.
            description: Descricao ou briefing do conteudo. Opcional.
            content_pillar: Pilar de conteudo: educacional, entretenimento, vendas, autoridade, comunidade, bastidores. Opcional.

        Returns:
            Confirmacao com detalhes do plano criado.
        """
        _notify("Adicionando ao calendario...")
        platforms = [p.strip().lower() for p in platform.split(",") if p.strip()]

        plan = db_create(
            user_id=user_id,
            title=title,
            content_type=content_type.lower().strip(),
            platforms=platforms,
            scheduled_at=scheduled_at,
            description=description,
            content_pillar=content_pillar.lower().strip() if content_pillar else "",
        )

        platform_str = ", ".join(platforms) if platforms else "nao definida"
        schedule_str = scheduled_at if scheduled_at else "sem data definida"

        return (
            f"Conteudo adicionado ao calendario!\n\n"
            f"**{plan['title']}**\n"
            f"Tipo: {plan['content_type']}\n"
            f"Plataforma(s): {platform_str}\n"
            f"Data: {schedule_str}\n"
            f"Status: ideia\n"
            f"ID: {plan['id']}"
        )

    def list_content_plan(status: str = "", period: str = "") -> str:
        """
        Listar conteudos planejados no calendario.

        Args:
            status: Filtrar por status: idea, planned, producing, ready, published. Vazio = todos.
            period: Filtrar por periodo: 'week' (proximos 7 dias), 'month' (proximo mes). Vazio = todos.

        Returns:
            Lista de conteudos planejados.
        """
        from datetime import datetime, timezone, timedelta

        from_date = ""
        to_date = ""
        if period == "week":
            now = datetime.now(timezone.utc)
            from_date = now.isoformat()
            to_date = (now + timedelta(days=7)).isoformat()
        elif period == "month":
            now = datetime.now(timezone.utc)
            from_date = now.isoformat()
            to_date = (now + timedelta(days=30)).isoformat()

        plans, has_more = db_list(
            user_id=user_id,
            status=status,
            from_date=from_date,
            to_date=to_date,
            limit=20,
        )

        if not plans:
            return "Nenhum conteudo planejado. Use plan_content para adicionar."

        status_icons = {
            "idea": "💡",
            "planned": "📅",
            "producing": "🔨",
            "ready": "✅",
            "published": "📤",
        }

        lines = [f"**{len(plans)} conteudo(s) no calendario:**\n"]
        for p in plans:
            icon = status_icons.get(p["status"], "📋")
            platforms = ", ".join(p.get("platforms", []))
            date_str = p.get("scheduled_at", "")[:10] if p.get("scheduled_at") else "sem data"
            lines.append(
                f"{icon} **{p['title']}** [{p['content_type']}]\n"
                f"   {platforms} · {date_str} · ID: {p['id']}"
            )

        if has_more:
            lines.append("\n(mostrando primeiros 20)")

        return "\n\n".join(lines)

    def update_content_plan(
        plan_id: int,
        status: str = "",
        scheduled_at: str = "",
        title: str = "",
        description: str = "",
    ) -> str:
        """
        Atualizar um conteudo no calendario.

        Args:
            plan_id: ID do plano a atualizar.
            status: Novo status: idea, planned, producing, ready, published.
            scheduled_at: Nova data/hora planejada (ISO 8601).
            title: Novo titulo (opcional).
            description: Nova descricao (opcional).

        Returns:
            Confirmacao da atualizacao.
        """
        kwargs = {}
        if status:
            kwargs["status"] = status.lower().strip()
        if scheduled_at:
            kwargs["scheduled_at"] = scheduled_at
        if title:
            kwargs["title"] = title
        if description:
            kwargs["description"] = description

        if not kwargs:
            return "Nenhuma alteracao especificada."

        plan = db_update(plan_id, user_id, **kwargs)
        if not plan:
            return f"Plano {plan_id} nao encontrado."

        return f"Plano atualizado: **{plan['title']}** — status: {plan['status']}"

    def delete_content_plan(plan_id: int) -> str:
        """
        Remover um conteudo do calendario.

        Args:
            plan_id: ID do plano a remover.

        Returns:
            Confirmacao.
        """
        ok = db_delete(plan_id, user_id)
        if not ok:
            return f"Plano {plan_id} nao encontrado."
        return f"Plano {plan_id} removido do calendario."

    return (
        plan_content,
        list_content_plan,
        update_content_plan,
        delete_content_plan,
    )

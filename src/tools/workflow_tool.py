"""
Tools de workflow para o agente Teq.
Usa o padrao factory para injetar user_id e notifier automaticamente.

Tools:
- run_workflow: execucao imediata de tarefa multi-step
- schedule_workflow: decompoe + agenda para execucao futura
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def create_workflow_tools(user_phone: str, channel: str = "whatsapp", notifier=None):
    """
    Factory que cria as tools de workflow com user_phone pre-injetado.

    Args:
        user_phone: Numero de telefone do usuario (session_id).
        channel: Canal de origem (web, whatsapp, etc).
        notifier: StatusNotifier/WebSocketNotifier para feedback em tempo real.

    Returns:
        Tuple com (run_workflow, schedule_workflow).
    """

    def run_workflow(request: str) -> str:
        """
        Executa uma tarefa complexa multi-step AGORA.
        Use quando o usuario pede algo que envolve MULTIPLAS acoes sequenciais.

        Exemplos de quando usar:
        - "Pesquise noticias e gere um carrossel pra cada"
        - "Veja meus emails, resuma e crie tarefas pro que for importante"
        - "Pesquise sobre X, escreva um post e publique no blog"

        Exemplos de quando NAO usar (responda direto):
        - "Que horas sao?" (1 acao simples)
        - "Pesquise sobre bitcoin" (1 tool call)
        - "Gere um carrossel sobre gatos" (1 tool call)

        Args:
            request: O pedido completo do usuario em linguagem natural.
                     Deve conter todas as informacoes necessarias para a execucao.

        Returns:
            Resultado final da execucao do workflow (output do ultimo step).
        """
        logger.info("[WorkflowTool] run_workflow | user=%s | request=%s...", user_phone, request[:80])

        try:
            from src.workflow.decomposer import decompose
            from src.workflow.executor import execute_workflow
            from src.models.workflows import create_workflow

            # 1. Decompor pedido em steps
            decomposed = decompose(request)
            title = decomposed.get("title", "Workflow")
            steps = decomposed.get("steps", [])

            if not steps:
                return "Nao consegui decompor o pedido em steps. Tente reformular."

            logger.info("[WorkflowTool] Decomposicao: '%s' com %s steps", title, len(steps))

            # 2. Salvar workflow no banco
            workflow_id = create_workflow(
                user_id=user_phone,
                original_request=request,
                steps=steps,
                title=title,
                notification_channel=channel,
                status="running",
            )

            # 3. Executar
            result = execute_workflow(workflow_id, notifier=notifier)

            from src.events_broadcast import emit_action_log_sync
            emit_action_log_sync(user_phone, "Workflow executado", title, channel)

            return result

        except Exception as e:
            logger.error("[WorkflowTool] Erro ao executar workflow: %s", e, exc_info=True)
            return f"Erro ao executar workflow: {e}"

    def schedule_workflow(
        request: str,
        trigger_type: str = "",
        minutes_from_now: Optional[int] = None,
        run_date: Optional[str] = None,
        cron_expression: Optional[str] = None,
        interval_minutes: Optional[int] = None,
        title: str = "",
        notification_channel: str = "",
    ) -> str:
        """
        Agenda a execucao de uma tarefa complexa multi-step para o futuro.
        Use quando o usuario quer AGENDAR algo que envolve multiplas acoes.

        IMPORTANTE — ANTES de chamar esta tool, CONFIRME com o usuario:
        1. QUANDO: para quando agendar? (data/hora exata, daqui X minutos, frequencia)
        2. CANAL: onde entregar? (web, WhatsApp ou ambos)
        Se o usuario nao informou esses dados, PERGUNTE antes de chamar.

        Exemplos:
        - "Todo dia as 7h, pesquise noticias e gere carrosseis, manda no zap"
        - "Toda segunda as 9h, veja meus emails e me mande um resumo"
        - "Daqui 30 minutos, pesquise X e me mande um relatorio"

        Args:
            request: O pedido completo do usuario (o que fazer quando disparar).
            trigger_type: "date" (unico), "cron" (recorrente), "interval".
            minutes_from_now: Minutos a partir de agora (para trigger_type="date").
            run_date: Data/hora ISO 8601 (alternativo a minutes_from_now).
            cron_expression: Expressao cron de 5 campos (para trigger_type="cron").
            interval_minutes: Intervalo em minutos (para trigger_type="interval").
            title: Titulo curto descrevendo o agendamento.
            notification_channel: Canal de entrega: 'whatsapp', 'web', 'ambos', 'web_voice'.

        Returns:
            Confirmacao com IDs do workflow e lembrete criados.
        """
        logger.info("[WorkflowTool] schedule_workflow | user=%s | request=%s...", user_phone, request[:80])

        try:
            from src.workflow.decomposer import decompose
            from src.models.workflows import create_workflow
            from src.tools.scheduler_tool import create_scheduler_tools

            # 1. Decompor pedido em steps
            decomposed = decompose(request)
            wf_title = title or decomposed.get("title", "Workflow agendado")
            steps = decomposed.get("steps", [])

            if not steps:
                return "Nao consegui decompor o pedido em steps. Tente reformular."

            logger.info("[WorkflowTool] Decomposicao: '%s' com %s steps", wf_title, len(steps))

            # 2. Resolver canal
            effective_channel = notification_channel or channel
            from src.integrations.channel_router import resolve_channel
            resolved = resolve_channel(effective_channel)
            if resolved:
                effective_channel = resolved

            # 3. Salvar workflow no banco (status=draft, sera executado pelo scheduler)
            workflow_id = create_workflow(
                user_id=user_phone,
                original_request=request,
                steps=steps,
                title=wf_title,
                notification_channel=effective_channel,
                status="draft",
            )

            # 4. Criar reminder apontando pro workflow
            # task_instructions contem descricao legivel; execucao real usa workflow_id
            schedule_message, _, _ = create_scheduler_tools(user_phone, channel=channel)

            task_desc = f"[WORKFLOW:{workflow_id}] {wf_title}"
            result = schedule_message(
                task_instructions=task_desc,
                trigger_type=trigger_type,
                minutes_from_now=minutes_from_now,
                run_date=run_date,
                cron_expression=cron_expression,
                interval_minutes=interval_minutes,
                title=wf_title,
                notification_channel=effective_channel,
            )

            # 5. Atualizar o reminder com workflow_id
            _link_workflow_to_latest_reminder(user_phone, workflow_id)

            steps_desc = "\n".join([f"  {i+1}. {s['instructions'][:80]}..." for i, s in enumerate(steps)])
            return (
                f"{result}\n\n"
                f"Workflow criado com {len(steps)} steps:\n{steps_desc}\n\n"
                f"ID do workflow: {workflow_id}"
            )

        except Exception as e:
            logger.error("[WorkflowTool] Erro ao agendar workflow: %s", e, exc_info=True)
            return f"Erro ao agendar workflow: {e}"

    return run_workflow, schedule_workflow


def _link_workflow_to_latest_reminder(user_phone: str, workflow_id: str):
    """Atualiza o reminder mais recente do usuario com o workflow_id."""
    try:
        from src.db.session import get_db
        from src.db.models import Reminder
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        with get_db() as db:
            reminder = (
                db.query(Reminder)
                .filter(Reminder.user_id == user_phone, Reminder.status == "active")
                .order_by(Reminder.created_at.desc())
                .first()
            )
            if reminder:
                reminder.workflow_id = workflow_id
                reminder.updated_at = now
                logger.info("[WorkflowTool] Reminder %s linkado ao workflow %s", reminder.id, workflow_id)
    except Exception as e:
        logger.error("[WorkflowTool] Falha ao linkar workflow ao reminder: %s", e)

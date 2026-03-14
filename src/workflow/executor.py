"""
Executor de workflows — itera steps sequencialmente, roda agent.run pra cada,
passa output entre eles, e atualiza estado no banco.

Funciona tanto em execucao imediata (chat) quanto em agendamentos (dispatcher).
"""
import logging
import time
from datetime import datetime, timezone

from src.agent.factory import create_agent_with_tools
from src.agent.response_utils import extract_final_response
from src.models.workflows import (
    get_workflow,
    update_workflow_status,
    update_workflow_step,
    mark_workflow_done,
    mark_workflow_failed,
)

logger = logging.getLogger(__name__)


def execute_workflow(workflow_id: str, notifier=None) -> str:
    """
    Executa todos os steps de um workflow sequencialmente.

    Cada step roda como um agent.run independente. O output dos steps
    anteriores e passado como contexto para o proximo.

    Args:
        workflow_id: ID do workflow a executar.
        notifier: Notifier opcional para enviar status ao usuario (chat em tempo real).

    Returns:
        Output do ultimo step executado, ou mensagem de erro.
    """
    workflow = get_workflow(workflow_id)
    if not workflow:
        logger.error("[WorkflowExecutor] Workflow %s nao encontrado.", workflow_id)
        return "Erro: workflow nao encontrado."

    user_id = workflow["user_id"]
    steps = workflow["steps"]
    total = len(steps)

    if total == 0:
        logger.warning("[WorkflowExecutor] Workflow %s sem steps.", workflow_id)
        return "Erro: workflow sem steps definidos."

    update_workflow_status(workflow_id, "running")
    logger.info("[WorkflowExecutor] Iniciando workflow %s (%s steps) para user %s",
                workflow_id, total, user_id[:8])

    accumulated_context = []
    last_output = ""

    for i, step in enumerate(steps):
        step_num = i + 1
        instructions = step.get("instructions", "")

        if not instructions:
            logger.warning("[WorkflowExecutor] Step %s sem instrucoes. Pulando.", i)
            continue

        # Notificar progresso
        if notifier and total > 1:
            try:
                _notify_progress(notifier, step_num, total)
            except Exception as e:
                logger.warning("[WorkflowExecutor] Falha ao notificar progresso: %s", e)

        # Marcar step como running
        now = datetime.now(timezone.utc).isoformat()
        update_workflow_step(workflow_id, i, {"status": "running", "started_at": now})

        # Montar prompt com contexto acumulado
        prompt = _build_step_prompt(instructions, accumulated_context, step_num, total)

        logger.info("[WorkflowExecutor] Executando step %s/%s: %s...",
                    step_num, total, instructions[:80])

        try:
            # Para execucao de workflow, o canal do agente deve ser 'whatsapp'
            # (canal de entrega padrao). O canal de entrega real das tools
            # (generate_carousel, send_to_channel) vem das instrucoes do step.
            agent_channel = "whatsapp"
            raw_channel = workflow.get("notification_channel") or ""
            if raw_channel in ("web", "web_text"):
                agent_channel = "web"

            isolated_session = f"workflow_{workflow_id}_step{step_num}_{int(time.time())}"
            agent = create_agent_with_tools(
                session_id=isolated_session,
                user_id=user_id,
                channel=agent_channel,
                extra_instructions=[
                    "EXECUCAO DE WORKFLOW: Voce esta executando um step de um workflow multi-step.",
                    "NAO peca mais informacoes, NAO tente agendar nada novo, NAO faca perguntas.",
                    "Execute as instrucoes diretamente e entregue o resultado pronto.",
                    "REGRA CRITICA: Se as instrucoes envolverem noticias, pesquisa ou informacoes atualizadas, "
                    "voce DEVE obrigatoriamente usar web_search ou deep_research.",
                    "FRESCOR: Busque SEMPRE informacoes MAIS RECENTES. Cada execucao deve trazer dados NOVOS.",
                    "Quando as instrucoes pedirem envio via WhatsApp, use delivery_channel='whatsapp' nas tools de imagem "
                    "ou send_to_channel para texto.",
                ],
                include_scheduler=False,
            )

            response = agent.run(prompt, knowledge_filters={"user_id": user_id})

            from src.memory.analytics import log_run_metrics
            try:
                log_run_metrics(user_id, "workflow", response)
            except Exception:
                pass

            output = ""
            if response and response.content:
                output = extract_final_response(response)

            # Se o agent so chamou tools sem gerar texto, extrair tool results
            if not output and hasattr(response, "messages") and response.messages:
                tool_outputs = []
                for msg in response.messages:
                    role = getattr(msg, "role", None)
                    content = getattr(msg, "content", None)
                    if role == "tool" and content:
                        tool_outputs.append(content)
                if tool_outputs:
                    output = "\n".join(tool_outputs)

            if not output:
                output = "(Step executado sem output textual)"

            # Salvar output e marcar step como done
            completed_at = datetime.now(timezone.utc).isoformat()
            update_workflow_step(workflow_id, i, {
                "status": "done",
                "output": output[:5000],  # limitar tamanho do output salvo
                "completed_at": completed_at,
            })

            accumulated_context.append({
                "step": step_num,
                "instructions": instructions,
                "output": output[:3000],  # contexto passado pros proximos steps
            })
            last_output = output

            logger.info("[WorkflowExecutor] Step %s/%s concluido. Output: %s...",
                        step_num, total, output[:100])

        except Exception as e:
            logger.error("[WorkflowExecutor] Step %s/%s falhou: %s", step_num, total, e, exc_info=True)
            error_at = datetime.now(timezone.utc).isoformat()
            update_workflow_step(workflow_id, i, {
                "status": "failed",
                "error": str(e)[:500],
                "completed_at": error_at,
            })
            mark_workflow_failed(workflow_id)
            return f"Erro no step {step_num}/{total}: {e}"

    mark_workflow_done(workflow_id)
    logger.info("[WorkflowExecutor] Workflow %s concluido com sucesso.", workflow_id)
    return last_output


def _build_step_prompt(instructions: str, context: list, step_num: int, total: int) -> str:
    """Monta o prompt para um step, incluindo contexto dos steps anteriores."""
    parts = []

    if context:
        parts.append("CONTEXTO DOS STEPS ANTERIORES:")
        for ctx in context:
            parts.append(f"--- Step {ctx['step']} ---")
            parts.append(f"Instrucao: {ctx['instructions']}")
            parts.append(f"Resultado: {ctx['output']}")
            parts.append("")

    parts.append(f"INSTRUCAO DO STEP ATUAL ({step_num}/{total}):")
    parts.append(instructions)

    return "\n".join(parts)


def _notify_progress(notifier, step_num: int, total: int):
    """Envia notificacao de progresso ao usuario via notifier existente."""
    status_msg = f"⏳ Executando tarefa {step_num}/{total}..."
    if hasattr(notifier, "send_status"):
        notifier.send_status(status_msg)
    elif hasattr(notifier, "notify"):
        notifier.notify(status_msg)

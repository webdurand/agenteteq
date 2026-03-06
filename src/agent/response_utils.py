"""
Utilitários para pós-processar a resposta do Agno Agent antes de enviar ao usuário.

Problema resolvido: quando o agente faz múltiplas iterações de tool-calling (ex: tool falha,
retenta com params diferentes), o response.content acumula TODO o texto intermediário gerado
pelo LLM em cada iteração, resultando em mensagens como:
  "Vou agendar... Putz, falhei... Caramba, errei de novo... Feito!"

A solução é extrair apenas a última mensagem final do assistente.
"""
from __future__ import annotations


def extract_final_response(response) -> str:
    """
    Extrai apenas o conteúdo da última mensagem do assistente,
    ignorando texto intermediário gerado durante o loop de tool-calling do Agno.

    Percorre response.messages de trás pra frente e retorna o content da
    primeira mensagem 'assistant' que tenha texto (e não seja apenas tool_call).
    """
    if hasattr(response, "messages") and response.messages:
        for msg in reversed(response.messages):
            role = getattr(msg, "role", None)
            content = getattr(msg, "content", None)
            tool_calls = getattr(msg, "tool_calls", None)

            if role == "assistant" and content and not tool_calls:
                return content

    return response.content or ""


def split_whatsapp_messages(text: str, max_length: int = 1500) -> list[str]:
    """
    Divide um texto longo em blocos menores para envio no WhatsApp.
    Preserva parágrafos inteiros sempre que possível.
    """
    if not text or len(text) <= max_length:
        return [text] if text else []

    paragraphs = text.split("\n\n")
    parts: list[str] = []
    current = ""

    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para

        if len(candidate) <= max_length:
            current = candidate
        else:
            if current:
                parts.append(current.strip())
            if len(para) <= max_length:
                current = para
            else:
                for i in range(0, len(para), max_length):
                    chunk = para[i:i + max_length]
                    parts.append(chunk.strip())
                current = ""

    if current.strip():
        parts.append(current.strip())

    return parts

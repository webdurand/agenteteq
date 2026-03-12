"""
Decomposer — recebe um pedido em linguagem natural e usa o LLM pra
decompor em steps estruturados de workflow.

Retorna uma lista de dicts com {instructions: str} para cada step.
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

DECOMPOSE_PROMPT = """Voce e um planejador de tarefas. Receba o pedido do usuario e decomponha em steps sequenciais.

TOOLS DISPONIVEIS DO AGENTE:
- web_search(query, topic, max_results, days): pesquisa na web. topic pode ser 'news' ou 'general'.
- deep_research(query): pesquisa aprofundada com multiplas fontes.
- generate_carousel(slides, aspect_ratio, use_reference_image, delivery_channel): gera carrossel de imagens. delivery_channel pode ser 'whatsapp', 'web' ou 'ambos'.
- edit_image(edit_instructions, aspect_ratio, delivery_channel): edita imagem.
- send_to_channel(message, channel): envia texto por outro canal ('whatsapp', 'web', 'ambos').
- add_memory(fato): salva informacao na memoria.
- add_task(title, description, due_date): cria tarefa.
- publish_post(title, content, tags): publica no blog.
- get_weather(city): previsao do tempo.
- read_emails(query): le emails do Gmail.
- get_calendar_events(): ve compromissos do Google Calendar.
- create_calendar_event(summary, start, end): cria evento no calendario.

REGRAS:
1. Cada step deve ser UMA instrucao clara e auto-contida.
2. Cada step deve referenciar explicitamente qual tool usar.
3. O output de um step anterior sera passado como contexto para o proximo.
4. Se o pedido for simples (1 acao), retorne apenas 1 step.
5. O ultimo step deve produzir o resultado final que sera entregue ao usuario.
6. NAO inclua steps de "enviar resultado" separadamente quando a tool ja entrega (ex: generate_carousel com delivery_channel).
7. Seja especifico nas instrucoes — diga exatamente o que pesquisar, quantos itens, etc.
8. Se o usuario mencionar envio por WhatsApp (zap, wpp), inclua delivery_channel='whatsapp' explicitamente nas instrucoes do step relevante.
9. Se o usuario mencionar envio pela web, inclua delivery_channel='web'. Se nao mencionar canal, use delivery_channel='whatsapp'.

FORMATO DE RESPOSTA (JSON puro, sem markdown):
{
  "title": "Titulo curto do workflow",
  "steps": [
    {"instructions": "Instrucao detalhada do step 1, referenciando tools especificas..."},
    {"instructions": "Instrucao detalhada do step 2, usando output do step anterior..."}
  ]
}

PEDIDO DO USUARIO:
"""


def decompose(request: str) -> dict:
    """
    Decompoe um pedido em steps de workflow usando o LLM.

    Args:
        request: pedido em linguagem natural do usuario.

    Returns:
        dict com 'title' (str) e 'steps' (list of dicts com 'instructions').
    """
    provider = os.getenv("LLM_PROVIDER", "openai").lower()

    try:
        if provider == "gemini":
            return _decompose_gemini(request)
        elif provider == "anthropic":
            return _decompose_anthropic(request)
        else:
            return _decompose_openai(request)
    except Exception as e:
        logger.error("Erro ao decompor workflow: %s", e, exc_info=True)
        return {
            "title": "Workflow",
            "steps": [{"instructions": request}],
        }


def _parse_response(text: str) -> dict:
    """Extrai JSON da resposta do LLM, tratando markdown fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = lines[1:]  # remove ```json
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    result = json.loads(cleaned)

    if "steps" not in result or not result["steps"]:
        raise ValueError("Resposta sem steps validos")

    for step in result["steps"]:
        if "instructions" not in step or not step["instructions"]:
            raise ValueError("Step sem instructions")

    return {
        "title": result.get("title", "Workflow"),
        "steps": result["steps"],
    }


def _decompose_openai(request: str) -> dict:
    from openai import OpenAI

    client = OpenAI()
    response = client.chat.completions.create(
        model=os.getenv("LLM_DECOMPOSE_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": DECOMPOSE_PROMPT},
            {"role": "user", "content": request},
        ],
        temperature=0.3,
        response_format={"type": "json_object"},
    )
    text = response.choices[0].message.content
    logger.info("[Decomposer/OpenAI] Resposta: %s", text[:200])
    return _parse_response(text)


def _decompose_gemini(request: str) -> dict:
    from google import genai
    from google.genai.types import GenerateContentConfig

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    client = genai.Client(api_key=api_key)

    model_name = os.getenv("LLM_DECOMPOSE_MODEL", "gemini-2.5-flash")
    response = client.models.generate_content(
        model=model_name,
        contents=DECOMPOSE_PROMPT + request,
        config=GenerateContentConfig(
            temperature=0.3,
            response_mime_type="application/json",
        ),
    )
    text = response.text
    logger.info("[Decomposer/Gemini] Resposta: %s", text[:200])
    return _parse_response(text)


def _decompose_anthropic(request: str) -> dict:
    from anthropic import Anthropic

    client = Anthropic()
    response = client.messages.create(
        model=os.getenv("LLM_DECOMPOSE_MODEL", "claude-3-5-haiku-20241022"),
        max_tokens=1024,
        system=DECOMPOSE_PROMPT,
        messages=[{"role": "user", "content": request}],
    )
    text = response.content[0].text
    logger.info("[Decomposer/Anthropic] Resposta: %s", text[:200])
    return _parse_response(text)

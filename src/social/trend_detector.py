"""
Cross-account trend detection for proactive content intelligence.

After the social fetcher processes all accounts, this module analyzes
new posts across a user's tracked accounts to detect common themes/trends.
If a trend is found and the user has opted in, sends a WhatsApp alert
with a content suggestion.

Throttle: max 1 trend alert per user per 24 hours.
"""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# Minimum accounts with new posts to attempt trend detection
MIN_ACCOUNTS_FOR_TREND = 2

# Hours between trend alerts per user
TREND_ALERT_COOLDOWN_HOURS = 24


def detect_and_send_trend_alerts(
    user_new_posts: dict[str, list[dict]],
    loop: asyncio.AbstractEventLoop,
):
    """
    Main entry point called by the social fetcher after processing all accounts.

    Args:
        user_new_posts: {user_id: [{"username": str, "platform": str, "posts": list[dict]}]}
        loop: asyncio event loop for async calls.
    """
    from src.models.social import (
        get_trend_alerts_enabled,
        get_last_trend_alert_at,
        update_last_trend_alert,
    )

    for user_id, account_posts in user_new_posts.items():
        # Check opt-in
        if not get_trend_alerts_enabled(user_id):
            continue

        # Need new posts from at least 2 different accounts
        accounts_with_posts = [ap for ap in account_posts if ap["posts"]]
        if len(accounts_with_posts) < MIN_ACCOUNTS_FOR_TREND:
            continue

        # Check throttle
        last_alert = get_last_trend_alert_at(user_id)
        if last_alert:
            if last_alert.tzinfo is None:
                last_alert = last_alert.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - last_alert < timedelta(hours=TREND_ALERT_COOLDOWN_HOURS):
                logger.debug("Trend alert throttled for %s (last: %s)", user_id[:8], last_alert)
                continue

        try:
            trend = _detect_trend(accounts_with_posts)
            if trend:
                _send_trend_alert(loop, user_id, trend)
                update_last_trend_alert(user_id)
                logger.info("Trend alert sent to %s: %s", user_id[:8], trend["topic"][:50])
        except Exception as e:
            logger.error("Trend detection failed for %s: %s", user_id[:8], e)


def _detect_trend(account_posts: list[dict]) -> dict | None:
    """
    Use LLM to analyze new posts across accounts and detect common themes.

    Returns dict with trend info or None if no trend found:
        {"topic": str, "summary": str, "suggestion": str, "accounts": list[str]}
    """
    # Build context for LLM
    lines = []
    for ap in account_posts:
        username = ap["username"]
        platform = ap["platform"]
        for post in ap["posts"][:5]:  # Max 5 posts per account
            caption = (post.get("caption", "") or "")[:200]
            hashtags = post.get("hashtags", []) or []
            lines.append(
                f"@{username} ({platform}): {caption}"
                f"{' | #' + ' #'.join(hashtags[:5]) if hashtags else ''}"
            )

    if len(lines) < 3:
        return None

    posts_text = "\n".join(lines)
    account_names = [f"@{ap['username']}" for ap in account_posts]

    prompt = (
        "Analise os posts recentes abaixo, de DIFERENTES contas de redes sociais "
        "que um criador de conteudo brasileiro monitora como referencia.\n\n"
        f"Posts recentes:\n{posts_text}\n\n"
        "Contas: " + ", ".join(account_names) + "\n\n"
        "Sua tarefa:\n"
        "1. Identifique se existe um TEMA EM COMUM ou TENDENCIA que aparece em "
        "posts de 2 ou mais contas diferentes.\n"
        "2. Se SIM, responda EXATAMENTE neste formato JSON (sem markdown, sem ```json):\n"
        '{"found": true, "topic": "nome curto do tema/tendencia", '
        '"summary": "resumo de 1-2 frases do que esta acontecendo", '
        '"suggestion": "sugestao de conteudo original que o criador poderia fazer sobre isso, '
        'incluindo formato recomendado (carrossel, reels, video)", '
        '"accounts": ["@conta1", "@conta2"]}\n\n'
        "3. Se NAO encontrar tema em comum relevante, responda:\n"
        '{"found": false}\n\n'
        "IMPORTANTE: So retorne found=true se o tema for REALMENTE relevante e acionavel "
        "para um criador de conteudo. Coincidencias vagas nao contam."
    )

    try:
        from agno.agent import Agent
        from agno.models.google import Gemini

        agent = Agent(
            model=Gemini(id="gemini-2.5-flash"),
            description="Voce e um analista de tendencias de conteudo digital.",
        )
        result = agent.run(prompt)
        response_text = result.content if hasattr(result, "content") else str(result)

        # Parse JSON response
        import json
        # Clean potential markdown wrapping
        clean = response_text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        data = json.loads(clean)
        if not data.get("found"):
            return None

        return {
            "topic": data.get("topic", ""),
            "summary": data.get("summary", ""),
            "suggestion": data.get("suggestion", ""),
            "accounts": data.get("accounts", []),
        }
    except Exception as e:
        logger.error("LLM trend analysis failed: %s", e)
        return None


def _send_trend_alert(
    loop: asyncio.AbstractEventLoop,
    user_id: str,
    trend: dict,
):
    """Send trend alert via WhatsApp with action buttons."""
    from src.integrations.whatsapp import whatsapp_client

    accounts_str = ", ".join(trend["accounts"])

    body = (
        f"Tendencia detectada no seu nicho!\n\n"
        f"Tema: {trend['topic']}\n\n"
        f"{trend['summary']}\n\n"
        f"Contas: {accounts_str}\n\n"
        f"Sugestao: {trend['suggestion']}"
    )

    buttons = [
        {"id": "btn_carousel", "title": "Criar carrossel"},
        {"id": "btn_script", "title": "Criar roteiro"},
        {"id": "btn_ignore", "title": "Ignorar"},
    ]

    try:
        loop.run_until_complete(
            whatsapp_client.send_button_message(user_id, body, buttons)
        )
    except Exception as e:
        logger.warning("Trend alert buttons failed, trying text: %s", e)
        try:
            fallback = (
                f"{body}\n\n"
                "Quer que eu crie conteudo sobre essa tendencia? "
                "Responda: carrossel, roteiro ou ignorar."
            )
            loop.run_until_complete(
                whatsapp_client.send_text_message(user_id, fallback)
            )
        except Exception as e2:
            logger.error("Trend alert failed completely for %s: %s", user_id[:8], e2)

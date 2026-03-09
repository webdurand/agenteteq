import logging
import httpx
from typing import Optional

from src.memory.integrations import get_user_integrations
from src.auth.crypto import decrypt

logger = logging.getLogger(__name__)

SLACK_API = "https://slack.com/api"


def _get_slack_token(user_phone: str) -> Optional[str]:
    integrations = get_user_integrations(user_phone, provider="slack", include_tokens=True)
    if not integrations:
        return None
    return integrations[0].get("access_token")


def _slack_get(token: str, method: str, params: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=15) as client:
        resp = client.get(f"{SLACK_API}/{method}", headers=headers, params=params or {})
        return resp.json()


def _slack_post(token: str, method: str, data: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=15) as client:
        resp = client.post(f"{SLACK_API}/{method}", headers=headers, json=data or {})
        return resp.json()


def _format_ts(ts: str) -> str:
    try:
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return ts


def create_slack_tools(user_phone: str):
    """
    Factory que cria as tools do Slack com o user_phone pre-injetado.
    Retorna tupla (list_slack_channels, read_slack_messages, search_slack).
    """

    def list_slack_channels(
        limit: int = 50,
        types: str = "public_channel,private_channel",
    ) -> str:
        """
        Lista os canais do Slack que o usuário participa.

        Args:
            limit: Número máximo de canais (padrão 50).
            types: Tipos de canais separados por vírgula (public_channel, private_channel, mpim, im).

        Returns:
            Lista formatada de canais ou mensagem de erro.
        """
        token = _get_slack_token(user_phone)
        if not token:
            return "Usuário não conectou o Slack. Peça para conectar em Configurações > Integrações."

        try:
            data = _slack_get(token, "conversations.list", {
                "types": types,
                "limit": min(limit, 200),
                "exclude_archived": "true",
            })

            if not data.get("ok"):
                return f"Erro na API do Slack: {data.get('error', 'desconhecido')}"

            channels = data.get("channels", [])
            if not channels:
                return "Nenhum canal encontrado."

            lines = []
            for ch in channels:
                name = ch.get("name", ch.get("id", "?"))
                purpose = ch.get("purpose", {}).get("value", "")
                is_member = "✓" if ch.get("is_member") else ""
                members = ch.get("num_members", "?")
                line = f"#{name} ({members} membros) {is_member}"
                if purpose:
                    line += f" — {purpose[:60]}"
                lines.append(line)

            return "\n".join(lines)
        except Exception as e:
            logger.error("Erro ao listar canais Slack para %s: %s", user_phone, e)
            return f"Erro ao acessar Slack: {str(e)}"

    def read_slack_messages(
        channel: str,
        limit: int = 20,
    ) -> str:
        """
        Lê as mensagens mais recentes de um canal do Slack.
        Use list_slack_channels primeiro para descobrir os nomes dos canais.

        Args:
            channel: Nome do canal (ex: "general") ou ID do canal (ex: "C01234ABCDE").
            limit: Número máximo de mensagens (padrão 20, máximo 100).

        Returns:
            Mensagens formatadas com autor, data e texto.
        """
        token = _get_slack_token(user_phone)
        if not token:
            return "Usuário não conectou o Slack. Peça para conectar em Configurações > Integrações."

        try:
            channel_id = channel
            if not channel.startswith("C") and not channel.startswith("G") and not channel.startswith("D"):
                channel_id = _resolve_channel_id(token, channel)
                if not channel_id:
                    return f"Canal '#{channel}' não encontrado. Use list_slack_channels para ver os canais disponíveis."

            data = _slack_get(token, "conversations.history", {
                "channel": channel_id,
                "limit": min(limit, 100),
            })

            if not data.get("ok"):
                error = data.get("error", "desconhecido")
                if error == "channel_not_found":
                    return f"Canal '{channel}' não encontrado ou sem acesso."
                if error == "not_in_channel":
                    return f"Você não é membro do canal '{channel}'."
                return f"Erro na API do Slack: {error}"

            messages = data.get("messages", [])
            if not messages:
                return f"Nenhuma mensagem recente no canal #{channel}."

            user_cache = {}
            lines = []
            for msg in reversed(messages):
                user_id = msg.get("user", "")
                username = _resolve_username(token, user_id, user_cache)
                ts = _format_ts(msg.get("ts", ""))
                text = msg.get("text", "")
                if len(text) > 500:
                    text = text[:500] + "..."
                lines.append(f"[{ts}] {username}: {text}")

            return "\n".join(lines)
        except Exception as e:
            logger.error("Erro ao ler mensagens Slack para %s: %s", user_phone, e)
            return f"Erro ao acessar Slack: {str(e)}"

    def search_slack(
        query: str,
        count: int = 10,
    ) -> str:
        """
        Pesquisa mensagens no Slack por palavra-chave.

        Args:
            query: Termo de busca (suporta operadores do Slack como 'from:@user', 'in:#channel', 'before:2026-01-01').
            count: Número máximo de resultados (padrão 10).

        Returns:
            Mensagens encontradas com canal, autor, data e texto.
        """
        token = _get_slack_token(user_phone)
        if not token:
            return "Usuário não conectou o Slack. Peça para conectar em Configurações > Integrações."

        try:
            data = _slack_get(token, "search.messages", {
                "query": query,
                "count": min(count, 20),
                "sort": "timestamp",
                "sort_dir": "desc",
            })

            if not data.get("ok"):
                return f"Erro na API do Slack: {data.get('error', 'desconhecido')}"

            matches = data.get("messages", {}).get("matches", [])
            if not matches:
                return f"Nenhuma mensagem encontrada para: '{query}'"

            lines = []
            for m in matches:
                channel_name = m.get("channel", {}).get("name", "?")
                username = m.get("username", "?")
                ts = _format_ts(m.get("ts", ""))
                text = m.get("text", "")
                if len(text) > 300:
                    text = text[:300] + "..."
                lines.append(f"[{ts}] #{channel_name} | {username}: {text}")

            total = data.get("messages", {}).get("total", len(matches))
            header = f"Encontradas {total} mensagens para '{query}' (mostrando {len(matches)}):\n"
            return header + "\n".join(lines)
        except Exception as e:
            logger.error("Erro ao pesquisar Slack para %s: %s", user_phone, e)
            return f"Erro ao pesquisar Slack: {str(e)}"

    return list_slack_channels, read_slack_messages, search_slack


def _resolve_channel_id(token: str, channel_name: str) -> Optional[str]:
    clean_name = channel_name.lstrip("#").lower()
    for ch_type in ["public_channel,private_channel", "mpim,im"]:
        data = _slack_get(token, "conversations.list", {
            "types": ch_type,
            "limit": 200,
            "exclude_archived": "true",
        })
        for ch in data.get("channels", []):
            if ch.get("name", "").lower() == clean_name:
                return ch["id"]
    return None


def _resolve_username(token: str, user_id: str, cache: dict) -> str:
    if not user_id:
        return "bot"
    if user_id in cache:
        return cache[user_id]
    try:
        data = _slack_get(token, "users.info", {"user": user_id})
        if data.get("ok"):
            profile = data["user"].get("profile", {})
            name = profile.get("display_name") or profile.get("real_name") or data["user"].get("name", user_id)
            cache[user_id] = name
            return name
    except Exception:
        pass
    cache[user_id] = user_id
    return user_id

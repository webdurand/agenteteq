import logging
import httpx
from typing import Optional

from src.memory.integrations import get_user_integrations
from src.auth.crypto import decrypt

logger = logging.getLogger(__name__)

SLACK_API = "https://slack.com/api"


def _get_all_slack_tokens(user_phone: str) -> list[dict]:
    """Retorna lista de {token, workspace} para todas as conexoes Slack do usuario."""
    integrations = get_user_integrations(user_phone, provider="slack", include_tokens=True)
    results = []
    for i in integrations:
        token = i.get("access_token")
        workspace = i.get("account_email", i.get("account_id", "?"))
        if token:
            results.append({"token": token, "workspace": workspace})
    return results


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
        Lista os canais do Slack que o usuário participa, em todos os workspaces conectados.

        Args:
            limit: Número máximo de canais por workspace (padrão 50).
            types: Tipos de canais separados por vírgula (public_channel, private_channel, mpim, im).

        Returns:
            Lista formatada de canais agrupados por workspace.
        """
        connections = _get_all_slack_tokens(user_phone)
        if not connections:
            return "Usuário não conectou o Slack. Peça para conectar em Configurações > Integrações."

        all_output = []
        for conn in connections:
            try:
                data = _slack_get(conn["token"], "conversations.list", {
                    "types": types,
                    "limit": min(limit, 200),
                    "exclude_archived": "true",
                })

                if not data.get("ok"):
                    all_output.append(f"[{conn['workspace']}] Erro: {data.get('error', 'desconhecido')}")
                    continue

                channels = data.get("channels", [])
                lines = [f"=== {conn['workspace']} ==="]
                if not channels:
                    lines.append("  Nenhum canal encontrado.")
                else:
                    for ch in channels:
                        name = ch.get("name", ch.get("id", "?"))
                        purpose = ch.get("purpose", {}).get("value", "")
                        is_member = "✓" if ch.get("is_member") else ""
                        members = ch.get("num_members", "?")
                        line = f"  #{name} ({members} membros) {is_member}"
                        if purpose:
                            line += f" — {purpose[:60]}"
                        lines.append(line)
                all_output.append("\n".join(lines))
            except Exception as e:
                logger.error("Erro ao listar canais Slack [%s] para %s: %s", conn['workspace'], user_phone, e)
                all_output.append(f"[{conn['workspace']}] Erro: {str(e)}")

        return "\n\n".join(all_output)

    def read_slack_messages(
        channel: str,
        limit: int = 20,
    ) -> str:
        """
        Lê as mensagens mais recentes de um canal do Slack.
        Use list_slack_channels primeiro para descobrir os nomes dos canais.
        Busca automaticamente em todos os workspaces conectados.

        Args:
            channel: Nome do canal (ex: "general") ou ID do canal (ex: "C01234ABCDE").
            limit: Número máximo de mensagens (padrão 20, máximo 100).

        Returns:
            Mensagens formatadas com autor, data e texto.
        """
        connections = _get_all_slack_tokens(user_phone)
        if not connections:
            return "Usuário não conectou o Slack. Peça para conectar em Configurações > Integrações."

        all_available: list[str] = []
        for conn in connections:
            try:
                token = conn["token"]
                channel_id = channel
                available_names: list[str] = []
                if not channel.startswith("C") and not channel.startswith("G") and not channel.startswith("D"):
                    channel_id, available_names = _resolve_channel_id(token, channel)
                    all_available.extend(available_names)
                    if not channel_id:
                        continue

                data = _slack_get(token, "conversations.history", {
                    "channel": channel_id,
                    "limit": min(limit, 100),
                })

                if not data.get("ok"):
                    continue

                messages = data.get("messages", [])
                if not messages:
                    return f"Nenhuma mensagem recente no canal #{channel} ({conn['workspace']})."

                user_cache = {}
                lines = [f"=== #{channel} em {conn['workspace']} ==="]
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
                logger.error("Erro ao ler mensagens Slack [%s] para %s: %s", conn['workspace'], user_phone, e)
                continue

        if all_available:
            names_list = ", ".join(f"#{n}" for n in sorted(set(all_available)) if n)
            return f"Canal '#{channel}' não encontrado. Canais disponíveis: {names_list}"
        return f"Canal '#{channel}' não encontrado em nenhum workspace conectado. Use list_slack_channels para ver os canais disponíveis."

    def search_slack(
        query: str,
        count: int = 10,
    ) -> str:
        """
        Pesquisa mensagens no Slack por palavra-chave em todos os workspaces conectados.

        Args:
            query: Termo de busca (suporta operadores do Slack como 'from:@user', 'in:#channel', 'before:2026-01-01').
            count: Número máximo de resultados por workspace (padrão 10).

        Returns:
            Mensagens encontradas com canal, autor, data e texto.
        """
        connections = _get_all_slack_tokens(user_phone)
        if not connections:
            return "Usuário não conectou o Slack. Peça para conectar em Configurações > Integrações."

        all_output = []
        for conn in connections:
            try:
                data = _slack_get(conn["token"], "search.messages", {
                    "query": query,
                    "count": min(count, 20),
                    "sort": "timestamp",
                    "sort_dir": "desc",
                })

                if not data.get("ok"):
                    all_output.append(f"[{conn['workspace']}] Erro: {data.get('error', 'desconhecido')}")
                    continue

                matches = data.get("messages", {}).get("matches", [])
                total = data.get("messages", {}).get("total", len(matches))

                if not matches:
                    all_output.append(f"[{conn['workspace']}] Nenhuma mensagem encontrada.")
                    continue

                lines = [f"=== {conn['workspace']} ({total} resultados) ==="]
                for m in matches:
                    channel_name = m.get("channel", {}).get("name", "?")
                    username = m.get("username", "?")
                    ts = _format_ts(m.get("ts", ""))
                    text = m.get("text", "")
                    if len(text) > 300:
                        text = text[:300] + "..."
                    lines.append(f"  [{ts}] #{channel_name} | {username}: {text}")
                all_output.append("\n".join(lines))
            except Exception as e:
                logger.error("Erro ao pesquisar Slack [%s] para %s: %s", conn['workspace'], user_phone, e)
                all_output.append(f"[{conn['workspace']}] Erro: {str(e)}")

        if not all_output:
            return f"Nenhuma mensagem encontrada para: '{query}'"
        return "\n\n".join(all_output)

    return list_slack_channels, read_slack_messages, search_slack


def _resolve_channel_id(token: str, channel_name: str) -> tuple[Optional[str], list[str]]:
    """Resolve channel name to ID with fuzzy fallback.
    Returns (channel_id, all_channel_names). channel_id is None if not found."""
    from difflib import get_close_matches

    clean_name = channel_name.lstrip("#").lower()
    all_channels: list[dict] = []

    for ch_type in ["public_channel,private_channel", "mpim,im"]:
        data = _slack_get(token, "conversations.list", {
            "types": ch_type,
            "limit": 200,
            "exclude_archived": "true",
        })
        all_channels.extend(data.get("channels", []))

    all_names = [ch.get("name", "") for ch in all_channels]

    # Nivel 1: match exato
    for ch in all_channels:
        if ch.get("name", "").lower() == clean_name:
            return ch["id"], all_names

    # Nivel 2: contains (prefere o nome mais curto = mais provavel)
    contains = [ch for ch in all_channels if clean_name in ch.get("name", "").lower()]
    if contains:
        best = min(contains, key=lambda c: len(c.get("name", "")))
        logger.info("Slack channel fuzzy match (contains): '%s' -> '#%s'", channel_name, best.get("name"))
        return best["id"], all_names

    # Nivel 3: similaridade (difflib)
    close = get_close_matches(clean_name, [n.lower() for n in all_names], n=1, cutoff=0.6)
    if close:
        for ch in all_channels:
            if ch.get("name", "").lower() == close[0]:
                logger.info("Slack channel fuzzy match (similarity): '%s' -> '#%s'", channel_name, ch.get("name"))
                return ch["id"], all_names

    return None, all_names


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

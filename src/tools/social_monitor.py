"""
Social media monitoring tools for the agent.

Factory function creates tools with user_id pre-injected via closure,
following the same pattern as scheduler_tool.py and task_manager.py.
"""

import asyncio
import concurrent.futures
import logging
from datetime import datetime, timezone, timedelta

from src.models.social import (
    track_account as db_track_account,
    untrack_account as db_untrack_account,
    untrack_account_by_username,
    list_tracked_accounts as db_list_tracked_accounts,
    get_tracked_account,
    get_tracked_account_by_username,
    save_content_batch,
    get_top_content,
    get_recent_content,
    update_account_metadata,
    set_alerts_enabled,
)

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run an async coroutine from sync context, even inside a running event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already in a running event loop — run in a new thread with its own loop
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _get_light_model():
    from agno.models.google import Gemini
    return Gemini(id="gemini-2.5-flash")


def _resolve_account(user_id: str, platform: str, username: str = "", account_id: int = 0) -> dict | None:
    """Find tracked account by id or username."""
    if account_id:
        return get_tracked_account(account_id)
    if username:
        username = username.lstrip("@").lower()
        return get_tracked_account_by_username(user_id, platform, username)
    return None


def _fetch_and_store(account: dict) -> list[dict]:
    """Fetch recent posts from provider and store in DB. Returns posts."""
    from src.social import get_social_provider

    provider = get_social_provider(account["platform"])
    platform = account["platform"]
    username = account["username"]
    account_id = account["id"]
    user_id = account["user_id"]

    # Fetch profile update
    profile = _run_async(
        provider.get_profile(platform, username)
    )
    update_account_metadata(
        account_id,
        display_name=profile.display_name,
        bio=profile.bio,
        followers_count=profile.followers_count,
        posts_count=profile.posts_count,
        profile_pic_url=profile.profile_pic_url,
    )

    # Fetch recent posts
    posts = _run_async(
        provider.get_recent_posts(platform, username, limit=20)
    )

    # Convert to dicts for DB
    posts_dicts = [
        {
            "platform_post_id": p.platform_post_id,
            "content_type": p.content_type,
            "caption": p.caption,
            "hashtags": p.hashtags,
            "media_urls": p.media_urls,
            "thumbnail_url": p.thumbnail_url,
            "likes_count": p.likes_count,
            "comments_count": p.comments_count,
            "views_count": p.views_count,
            "engagement_rate": "",
            "posted_at": p.posted_at,
        }
        for p in posts
    ]

    save_content_batch(account_id, user_id, platform, posts_dicts)
    return posts_dicts


def _is_stale(account: dict, hours: int = 6) -> bool:
    """Check if account data is older than `hours`."""
    last = account.get("last_fetched_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - last_dt > timedelta(hours=hours)
    except Exception:
        return True


def create_social_tools(user_id: str, channel: str = "unknown", notifier=None):
    """Factory that creates social monitoring tools with user_id pre-injected."""

    def _notify(msg: str) -> None:
        if notifier:
            notifier.notify(msg)

    def preview_account(platform: str, username: str) -> str:
        """
        Ver o perfil e conteudo recente de uma conta de rede social SEM salvar.
        Use esta ferramenta ANTES de track_account para mostrar o conteudo ao usuario.
        Depois de mostrar, pergunte se o usuario quer salvar para acompanhamento continuo.

        Args:
            platform: Plataforma da rede social (instagram, youtube).
            username: Nome de usuario na plataforma (ex: natgeo, @natgeo, @MrBeast).

        Returns:
            Dados do perfil e preview dos posts recentes com metricas.
        """
        from src.social import get_social_provider

        platform = platform.lower().strip()
        username = username.lstrip("@").strip()
        if platform != "youtube":
            username = username.lower()

        if not username:
            return "Informe o username da conta."

        SUPPORTED_PLATFORMS = ["instagram", "youtube"]
        if platform not in SUPPORTED_PLATFORMS:
            return f"Plataforma '{platform}' nao suportada. Opcoes: {', '.join(SUPPORTED_PLATFORMS)}"

        _notify(f"Buscando perfil @{username}...")

        try:
            provider = get_social_provider(platform)
        except Exception as e:
            return f"Plataforma '{platform}' nao esta configurada: {e}"

        # Check if already tracked
        existing = get_tracked_account_by_username(user_id, platform, username)
        if existing:
            return (
                f"A conta @{username} ja esta sendo monitorada! "
                f"Use get_account_insights ou get_trending_content para ver o conteudo."
            )

        try:
            profile = _run_async(
                provider.get_profile(platform, username)
            )
        except Exception as e:
            logger.error("preview_account erro ao buscar perfil @%s/%s: %s", platform, username, e)
            return f"Nao consegui encontrar o perfil @{username}: {str(e)}"

        if profile.metadata.get("is_private"):
            return f"A conta @{username} e privada. So consigo acessar contas publicas."

        # Fetch recent posts for preview (without saving)
        try:
            posts = _run_async(
                provider.get_recent_posts(platform, username, limit=10)
            )
        except Exception as e:
            logger.warning("Erro ao buscar posts de @%s: %s", username, e)
            posts = []

        followers_label = "Inscritos" if platform == "youtube" else "Seguidores"
        content_label = "Videos" if platform == "youtube" else "Posts"

        lines = [
            f"**@{profile.username}** ({platform})\n",
            f"**{profile.display_name}**",
            f"{profile.bio[:200] if profile.bio else 'Sem bio'}\n",
            f"{followers_label}: {profile.followers_count:,} · {content_label}: {profile.posts_count:,}\n",
        ]

        if posts:
            # Sort by engagement for preview (views for YouTube, likes for others)
            sort_key = (lambda p: p.views_count) if platform == "youtube" else (lambda p: p.likes_count)
            sorted_posts = sorted(posts, key=sort_key, reverse=True)
            lines.append(f"**Top {min(5, len(sorted_posts))} {content_label.lower()} por engajamento:**\n")
            for i, post in enumerate(sorted_posts[:5], 1):
                caption_preview = (post.caption or "")[:80]
                if len(post.caption or "") > 80:
                    caption_preview += "..."
                lines.append(
                    f"{i}. [{post.content_type}] "
                    f"❤️ {post.likes_count:,} · 💬 {post.comments_count:,}"
                    f"{' · 👁 ' + f'{post.views_count:,}' if post.views_count else ''}\n"
                    f"   {caption_preview}"
                )

        lines.append(
            "\n---\nDeseja que eu salve essa conta para monitoramento continuo? "
            "Assim posso acompanhar novos conteudos e gerar insights automaticamente."
        )

        return "\n".join(lines)

    def track_account(platform: str, username: str) -> str:
        """
        Salvar uma conta de rede social para monitoramento continuo.
        Use DEPOIS de preview_account, quando o usuario confirmar que quer acompanhar.

        Args:
            platform: Plataforma da rede social (instagram, youtube).
            username: Nome de usuario na plataforma (ex: natgeo, @natgeo, @MrBeast).

        Returns:
            Confirmacao com dados do perfil ou mensagem de erro.
        """
        from src.social import get_social_provider

        platform = platform.lower().strip()
        username = username.lstrip("@").strip()
        if platform != "youtube":
            username = username.lower()

        if not username:
            return "Informe o username da conta para monitorar."

        SUPPORTED_PLATFORMS = ["instagram", "youtube"]
        if platform not in SUPPORTED_PLATFORMS:
            return f"Plataforma '{platform}' nao suportada. Opcoes: {', '.join(SUPPORTED_PLATFORMS)}"

        # Check feature gate
        from src.config.feature_gates import is_feature_enabled, get_plan_limit
        if not is_feature_enabled(user_id, "social_monitoring_enabled"):
            return "O monitoramento de redes sociais nao esta disponivel no seu plano atual."

        # Check max tracked accounts limit
        current_accounts = db_list_tracked_accounts(user_id)
        max_accounts = get_plan_limit(user_id, "max_tracked_accounts", 3)
        if len(current_accounts) >= max_accounts:
            return (
                f"Voce atingiu o limite de {max_accounts} contas monitoradas no seu plano. "
                f"Remova uma conta existente ou faca upgrade."
            )

        try:
            provider = get_social_provider(platform)
        except Exception as e:
            return f"Plataforma '{platform}' nao esta configurada: {e}"

        # Check if already tracked
        existing = get_tracked_account_by_username(user_id, platform, username)
        if existing:
            return (
                f"A conta @{username} do {platform} ja esta sendo monitorada.\n"
                f"Seguidores: {existing.get('followers_count', 0):,}\n"
                f"Posts: {existing.get('posts_count', 0):,}"
            )

        try:
            profile = _run_async(
                provider.get_profile(platform, username)
            )
        except Exception as e:
            logger.error("track_account erro ao buscar perfil @%s/%s: %s", platform, username, e)
            return f"Nao consegui encontrar o perfil @{username}: {str(e)}"

        if profile.metadata.get("is_private"):
            return f"A conta @{username} e privada. So consigo monitorar contas publicas."

        account_id = db_track_account(
            user_id=user_id,
            platform=platform,
            username=profile.username,
            display_name=profile.display_name,
            profile_url=profile.profile_url,
            profile_pic_url=profile.profile_pic_url,
            bio=profile.bio,
            followers_count=profile.followers_count,
            posts_count=profile.posts_count,
            metadata=profile.metadata,
        )

        # Fetch initial posts in background
        try:
            account = get_tracked_account(account_id)
            if account:
                _fetch_and_store(account)
        except Exception as e:
            logger.warning("Erro ao buscar posts iniciais de @%s: %s", username, e)

        return (
            f"Pronto! Comecei a monitorar @{profile.username} no {platform}.\n\n"
            f"**{profile.display_name}**\n"
            f"{profile.bio[:200] if profile.bio else 'Sem bio'}\n\n"
            f"Seguidores: {profile.followers_count:,}\n"
            f"Posts: {profile.posts_count:,}\n\n"
            f"Voce pode pedir insights, conteudos em alta ou criar roteiros baseados nessa referencia."
        )

    def untrack_account(platform: str = "instagram", username: str = "") -> str:
        """
        Parar de monitorar uma conta de rede social.

        Args:
            platform: Plataforma (instagram, youtube).
            username: Username da conta para parar de monitorar.

        Returns:
            Confirmacao.
        """
        username = username.lstrip("@").lower().strip()
        if not username:
            return "Informe o username da conta que deseja parar de monitorar."

        ok = untrack_account_by_username(user_id, platform, username)
        if ok:
            return f"Parei de monitorar @{username} no {platform}."
        return f"Nao encontrei @{username} nas suas contas monitoradas do {platform}."

    def list_tracked_accounts(platform: str = "") -> str:
        """
        Listar todas as contas de redes sociais que estao sendo monitoradas.

        Args:
            platform: Filtrar por plataforma (opcional). Ex: instagram.

        Returns:
            Lista de contas monitoradas.
        """
        accounts = db_list_tracked_accounts(user_id, platform=platform or None)
        if not accounts:
            return "Voce nao esta monitorando nenhuma conta ainda. Use track_account para comecar."

        lines = [f"**{len(accounts)} conta(s) monitorada(s):**\n"]
        for acc in accounts:
            platform_icon = {"instagram": "📸", "youtube": "🎬"}.get(acc["platform"], "🌐")
            last = acc.get("last_fetched_at", "")
            last_str = _format_relative_time(last) if last else "nunca atualizado"
            lines.append(
                f"{platform_icon} **@{acc['username']}** ({acc['platform']})\n"
                f"   {acc.get('display_name', '')}\n"
                f"   {acc.get('followers_count', 0):,} seguidores · "
                f"Atualizado {last_str}"
            )
        return "\n\n".join(lines)

    def get_account_insights(platform: str = "instagram", username: str = "") -> str:
        """
        Analisa o conteudo recente de uma conta monitorada.
        Retorna insights sobre topicos, engajamento e tendencias.

        Args:
            platform: Plataforma (instagram, youtube).
            username: Username da conta para analisar.

        Returns:
            Analise detalhada com insights.
        """
        username = username.lstrip("@").lower().strip()
        if not username:
            return "Informe o username da conta para analisar."

        _notify(f"Analisando conteudo de @{username}...")

        account = get_tracked_account_by_username(user_id, platform, username)
        if not account:
            return f"@{username} nao esta sendo monitorada. Use track_account primeiro."

        # Refresh if stale
        if _is_stale(account):
            try:
                _fetch_and_store(account)
            except Exception as e:
                logger.warning("Erro ao atualizar @%s: %s", username, e)

        posts = get_recent_content(account["id"], limit=20)
        if not posts:
            return f"Nenhum conteudo encontrado para @{username}. Pode ser que a conta seja muito nova ou privada."

        # Build analysis prompt
        posts_text = _format_posts_for_analysis(posts)
        post_images = _download_post_images(posts, max_images=5)

        visual_items = ""
        visual_note = ""
        if post_images:
            visual_items = (
                "7. Analise visual: padroes de cores, composicao, estilo fotografico, "
                "uso de texto nas imagens, identidade visual consistente\n"
                "8. Elementos visuais que se correlacionam com maior engajamento\n\n"
            )
            visual_note = (
                f"\nAs {len(post_images)} imagens anexadas correspondem aos primeiros "
                f"{len(post_images)} posts listados acima, na mesma ordem."
            )

        platform_name = "YouTube" if platform == "youtube" else "Instagram"
        followers_label = "Inscritos" if platform == "youtube" else "Seguidores"

        if platform == "youtube":
            format_line = "2. Formatos que mais funcionam (video longo, shorts, lives)\n"
        else:
            format_line = "2. Formatos que mais funcionam (carrossel, foto, video, reels)\n"

        analysis = _run_analysis(
            f"Analise os posts recentes da conta @{username} do {platform_name}.\n\n"
            f"Perfil: {account.get('display_name', '')} - {account.get('bio', '')}\n"
            f"{followers_label}: {account.get('followers_count', 0):,}\n\n"
            f"Posts recentes:\n{posts_text}\n\n"
            f"Faca uma analise detalhada incluindo:\n"
            f"1. Principais topicos e tematicas abordadas\n"
            f"{format_line}"
            f"3. Padroes de engajamento (o que gera mais likes/comentarios)\n"
            f"4. Hashtags mais usadas e efetivas\n"
            f"5. Tom e estilo de comunicacao\n"
            f"6. Sugestoes para quem quer criar conteudo similar\n"
            f"{visual_items}"
            f"Responda em portugues de forma objetiva e acionavel."
            f"{visual_note}",
            images=post_images,
        )

        return (
            f"**Insights de @{username}** ({account.get('followers_count', 0):,} seguidores)\n\n"
            f"{analysis}"
        )

    def get_trending_content(platform: str = "instagram", username: str = "") -> str:
        """
        Mostra os conteudos com mais engajamento de uma conta monitorada.
        Mapeia o que funciona melhor em formato, tematica e abordagem.

        Args:
            platform: Plataforma (instagram, youtube).
            username: Username da conta.

        Returns:
            Top conteudos com metricas e analise.
        """
        username = username.lstrip("@").lower().strip()
        if not username:
            return "Informe o username da conta."

        account = get_tracked_account_by_username(user_id, platform, username)
        if not account:
            return f"@{username} nao esta sendo monitorada. Use track_account primeiro."

        if _is_stale(account):
            try:
                _fetch_and_store(account)
            except Exception as e:
                logger.warning("Erro ao atualizar @%s: %s", username, e)

        top_posts = get_top_content(account["id"], sort_by="likes_count", limit=5)
        if not top_posts:
            return f"Nenhum conteudo encontrado para @{username}."

        lines = [f"**Top {len(top_posts)} conteudos de @{username}:**\n"]
        for i, post in enumerate(top_posts, 1):
            caption_preview = (post.get("caption", "") or "")[:100]
            if len(post.get("caption", "") or "") > 100:
                caption_preview += "..."

            lines.append(
                f"**{i}.** [{post.get('content_type', 'post')}] "
                f"❤️ {post.get('likes_count', 0):,} · 💬 {post.get('comments_count', 0):,}"
                f"{' · 👁 ' + str(post.get('views_count', 0)) if post.get('views_count') else ''}\n"
                f"   {caption_preview}\n"
                f"   Hashtags: {', '.join('#' + h for h in (post.get('hashtags', []) or [])[:5])}"
            )

        return "\n\n".join(lines)

    def analyze_posts(
        platform: str = "instagram",
        username: str = "",
        sort: str = "recent",
        limit: int = 3,
        question: str = "",
    ) -> str:
        """
        Olha para os posts de uma conta (incluindo as IMAGENS) e responde perguntas
        ou descreve o conteudo. Funciona com qualquer conta publica, mesmo que NAO
        esteja sendo monitorada. Use quando o usuario quiser entender posts especificos,
        saber do que trata um post, ou pedir analise visual.

        Args:
            platform: Plataforma (instagram, youtube).
            username: Username da conta (nao precisa estar monitorada).
            sort: Ordenacao - 'recent' para mais recentes, 'top' para mais engajamento.
            limit: Quantidade de posts para analisar (1 a 5).
            question: Pergunta especifica sobre os posts (opcional).

        Returns:
            Descricao e analise dos posts com base nas imagens e texto.
        """
        from src.social import get_social_provider

        username = username.lstrip("@").lower().strip()
        if not username:
            return "Informe o username da conta."

        _notify(f"Analisando posts de @{username}...")

        limit = max(1, min(limit, 5))

        # Try from tracked account first (has cached data)
        account = get_tracked_account_by_username(user_id, platform, username)
        posts = None

        if account:
            # Refresh if stale
            if _is_stale(account):
                try:
                    _fetch_and_store(account)
                except Exception as e:
                    logger.warning("Erro ao atualizar @%s: %s", username, e)

            if sort == "top":
                posts = get_top_content(account["id"], sort_by="likes_count", limit=limit)
            else:
                posts = get_recent_content(account["id"], limit=limit)

        # Not tracked or no posts — fetch on-the-fly
        if not posts:
            try:
                provider = get_social_provider(platform)
                raw_posts = _run_async(
                    provider.get_recent_posts(platform, username, limit=limit)
                )
                posts = [
                    {
                        "platform_post_id": p.platform_post_id,
                        "content_type": p.content_type,
                        "caption": p.caption,
                        "hashtags": p.hashtags,
                        "media_urls": p.media_urls,
                        "thumbnail_url": p.thumbnail_url,
                        "likes_count": p.likes_count,
                        "comments_count": p.comments_count,
                        "views_count": p.views_count,
                        "posted_at": p.posted_at,
                    }
                    for p in raw_posts
                ]
            except Exception as e:
                logger.error("Erro ao buscar posts de @%s: %s", username, e)
                return f"Nao consegui buscar os posts de @{username}: {str(e)}"

        if not posts:
            return f"Nenhum post encontrado para @{username}."

        # Sort on-the-fly posts if needed
        if sort == "top" and not account:
            posts = sorted(posts, key=lambda p: p.get("likes_count", 0), reverse=True)
        posts = posts[:limit]

        posts_text = _format_posts_for_analysis(posts)
        post_images = _download_post_images(posts, max_images=limit)

        user_question = question.strip() if question else "Descreva o conteudo de cada post de forma detalhada."

        platform_name = "YouTube" if platform == "youtube" else "Instagram"
        prompt = (
            f"Voce esta olhando para {len(posts)} post(s) da conta @{username} no {platform_name}.\n\n"
            f"Dados dos posts:\n{posts_text}\n\n"
        )
        if post_images:
            prompt += (
                f"As {len(post_images)} imagens anexadas correspondem aos posts acima.\n"
                "Analise CADA imagem em detalhe: o que aparece, cores, texto na imagem, "
                "composicao, estilo visual, e qualquer elemento relevante.\n\n"
            )
        prompt += (
            f"Pergunta do usuario: {user_question}\n\n"
            "Responda em portugues de forma clara e detalhada."
        )

        analysis = _run_analysis(prompt, images=post_images)
        sort_label = "mais recente(s)" if sort == "recent" else "com mais engajamento"
        return (
            f"**Analise de {len(posts)} post(s) {sort_label} de @{username}:**\n\n"
            f"{analysis}"
        )

    def create_content_script(
        platform: str = "instagram",
        reference_username: str = "",
        content_type: str = "carousel",
        topic: str = "",
    ) -> str:
        """
        Cria um roteiro de conteudo (carousel, video, reels) inspirado nas melhores
        referencias de uma conta monitorada. Gera a estrutura pronta para uso.

        Args:
            platform: Plataforma da referencia (instagram, youtube).
            reference_username: Username da conta de referencia.
            content_type: Tipo de conteudo a criar (carousel, video, reels).
            topic: Tema especifico (opcional).

        Returns:
            Roteiro detalhado slide-a-slide ou cena-a-cena.
        """
        reference_username = reference_username.lstrip("@").lower().strip()
        if not reference_username:
            return "Informe o username da conta de referencia."

        _notify(f"Criando roteiro inspirado em @{reference_username}...")

        account = get_tracked_account_by_username(user_id, platform, reference_username)
        if not account:
            return f"@{reference_username} nao esta sendo monitorada. Use track_account primeiro."

        # Get top performing posts as reference
        top_posts = get_top_content(account["id"], sort_by="likes_count", limit=10)
        if not top_posts:
            return f"Nenhum conteudo de referencia encontrado para @{reference_username}."

        posts_text = _format_posts_for_analysis(top_posts)
        post_images = _download_post_images(top_posts, max_images=5)

        topic_instruction = ""
        if topic:
            topic_instruction = f"\nO tema especifico do conteudo deve ser: {topic}"

        prompt = (
            f"Voce e um estrategista de conteudo digital. Analise os posts de maior engajamento "
            f"da conta @{reference_username} e crie um roteiro de {content_type} inspirado nesses padroes.\n\n"
            f"Perfil de referencia: {account.get('display_name', '')} - {account.get('bio', '')}\n\n"
            f"Posts de maior engajamento:\n{posts_text}\n\n"
            f"{topic_instruction}\n\n"
            f"Crie um roteiro detalhado para um {content_type} com:\n"
        )

        if content_type == "carousel":
            prompt += (
                "- Titulo geral do carrossel\n"
                "- 5 a 7 slides com:\n"
                "  - Titulo do slide\n"
                "  - Texto/copy do slide\n"
                "  - Descricao visual (o que a imagem deve mostrar)\n"
                "- Sugestao de CTA (call to action) final\n"
                "- Sugestao de legenda para o post\n"
                "- Hashtags recomendadas\n\n"
                "IMPORTANTE: O roteiro deve ser inspirado nos padroes que funcionam "
                "na conta de referencia, mas com conteudo original."
            )
        elif content_type in ("video", "reels"):
            prompt += (
                "- Titulo/gancho inicial (primeiros 3 segundos)\n"
                "- Roteiro cena a cena com:\n"
                "  - Duracao aproximada da cena\n"
                "  - O que falar/mostrar\n"
                "  - Texto na tela (se aplicavel)\n"
                "- CTA final\n"
                "- Sugestao de legenda\n"
                "- Hashtags recomendadas\n\n"
                "IMPORTANTE: O roteiro deve capturar os padroes de engajamento "
                "da referencia, com conteudo original."
            )

        if post_images:
            prompt += (
                "\n\nIMPORTANTE: Estou anexando as imagens dos posts de maior engajamento. "
                "Analise os padroes visuais (cores, composicao, tipografia, estilo) e "
                "incorpore essas referencias visuais no roteiro. Na descricao visual de cada slide/cena, "
                "indique especificamente como replicar os padroes visuais que funcionam.\n"
                f"As {len(post_images)} imagens correspondem aos primeiros {len(post_images)} posts listados."
            )

        prompt += "\nResponda em portugues de forma pratica e detalhada."

        script = _run_analysis(prompt, images=post_images)

        return (
            f"**Roteiro de {content_type} inspirado em @{reference_username}**\n\n"
            f"{script}"
        )

    def toggle_alerts(platform: str = "instagram", username: str = "", enabled: bool = True) -> str:
        """
        Ativa ou desativa alertas proativos para uma conta monitorada.
        Quando ativado, voce recebe uma notificacao no WhatsApp sempre que a conta
        postar algo com engajamento muito acima da media.

        Args:
            platform: Plataforma (instagram, youtube).
            username: Username da conta monitorada.
            enabled: True para ativar alertas, False para desativar.

        Returns:
            Confirmacao.
        """
        username = username.lstrip("@").lower().strip()
        if not username:
            return "Informe o username da conta."

        account = get_tracked_account_by_username(user_id, platform, username)
        if not account:
            return f"@{username} nao esta sendo monitorada. Use track_account primeiro."

        ok = set_alerts_enabled(account["id"], user_id, enabled)
        if not ok:
            return "Nao consegui atualizar as configuracoes de alerta."

        if enabled:
            return (
                f"Alertas ativados para @{username}! "
                f"Vou te avisar no WhatsApp quando ela postar algo que bombar."
            )
        return f"Alertas desativados para @{username}."

    def generate_competitive_report(usernames: str = "", platforms: str = "instagram", format: str = "images", theme: str = "dark", delivery_channel: str = "") -> str:
        """
        Gera um relatorio comparando perfis monitorados.
        Pode gerar em diferentes formatos conforme a preferencia do usuario.
        O envio eh automatico pelo canal atual da conversa (WhatsApp ou web).
        Se o usuario pedir para enviar por um canal diferente, use delivery_channel para direcionar.

        Args:
            usernames: Usernames separados por virgula (ex: "natgeo,bbcnews,cnn").
                       Se vazio, usa todas as contas monitoradas.
            platforms: Plataformas separadas por virgula (ex: "instagram,youtube").
            format: Formato do relatorio:
                    - "text": apenas texto estruturado (rapido)
                    - "images": carrossel de imagens com graficos (padrao)
                    - "text_images": texto + imagens
                    - "pdf": documento PDF dashboard visual para download
            theme: Tema visual do PDF: "dark" (padrao, fundo escuro) ou "light" (fundo branco).
            delivery_channel: Canal de entrega opcional. Se vazio, usa o canal atual da conversa.
                              Valores: "whatsapp" (envia como documento/imagem no WhatsApp),
                              "web" (salva na galeria de midia do painel).

        Returns:
            Texto com resumo do relatorio e links se aplicavel.
        """
        from src.social.report_generator import (
            collect_report_data,
            generate_insights,
            render_report_slides,
            render_report_text,
            render_report_pdf,
            render_dashboard_pdf,
        )

        # Resolve delivery channel: explicit override or session channel
        effective_channel = delivery_channel.strip().lower() if delivery_channel.strip() else channel

        format = format.strip().lower()
        if format not in ("text", "images", "text_images", "pdf"):
            format = "images"

        theme = theme.strip().lower()
        if theme not in ("dark", "light"):
            theme = "dark"

        platform_list = [p.strip().lower() for p in platforms.split(",") if p.strip()]

        if usernames.strip():
            username_list = [u.strip().lstrip("@").lower() for u in usernames.split(",") if u.strip()]
        else:
            accounts = db_list_tracked_accounts(user_id)
            username_list = [a["username"] for a in accounts]

        if not username_list:
            return "Preciso de pelo menos 1 conta para gerar o relatorio. Informe os usernames."

        _notify("Coletando dados dos perfis...")
        report_data = collect_report_data(user_id, username_list, platform_list)
        if not report_data or not report_data.get("accounts"):
            return (
                "Nao consegui coletar dados para o relatorio. "
                "Verifique se as contas estao sendo monitoradas."
            )

        # Generate LLM insights
        _notify("Gerando insights comparativos...")
        insights = generate_insights(report_data)
        report_data["insights"] = insights

        # ── Text-only format ──
        if format == "text":
            return render_report_text(report_data, insights)

        # ── PDF format ──
        if format == "pdf":
            _notify("Gerando dashboard PDF...")
            pdf_bytes = render_dashboard_pdf(report_data, insights, theme=theme)
            if not pdf_bytes:
                return "Erro ao gerar o PDF do relatorio."
            try:
                import io as _io
                import cloudinary.uploader
                result = cloudinary.uploader.upload(
                    _io.BytesIO(pdf_bytes),
                    folder="teq/reports",
                    public_id=f"report_{user_id}",
                    overwrite=True,
                    resource_type="raw",
                    format="pdf",
                )
                pdf_url = result["secure_url"]

                # Save to gallery
                try:
                    from src.models.carousel import create_pdf_entry
                    from src.events import emit_event_sync
                    accounts_label = ", ".join(f"@{u}" for u in username_list[:3])
                    pdf_title = f"Relatorio - {accounts_label}"
                    create_pdf_entry(user_id, pdf_title, pdf_url)
                    emit_event_sync(user_id, "carousel_generated")
                except Exception as e:
                    logger.error("Erro ao salvar PDF na galeria: %s", e)

                # Send PDF as document on WhatsApp
                doc_sent = False
                if effective_channel in ("whatsapp", "whatsapp_text", "web_whatsapp"):
                    try:
                        import asyncio as _aio
                        from src.integrations.whatsapp import whatsapp_client as _wpp

                        accounts_label = ", ".join(f"@{u}" for u in username_list[:3])
                        coro = _wpp.send_document(
                            user_id,
                            pdf_url,
                            filename=f"relatorio_{'-'.join(username_list[:3])}.pdf",
                            caption=f"Relatorio Competitivo - {accounts_label}",
                        )
                        try:
                            loop = _aio.get_running_loop()
                            loop.create_task(coro)
                        except RuntimeError:
                            _aio.run(coro)
                        doc_sent = True
                    except Exception as e:
                        logger.error("Erro ao enviar PDF via WhatsApp: %s", e)

                text_summary = render_report_text(report_data, insights)
                if doc_sent:
                    return text_summary
                return f"{text_summary}\n\n**Download do PDF:** {pdf_url}"
            except Exception as e:
                logger.error("Erro upload PDF: %s", e)
                return "Relatorio gerado mas houve erro ao fazer upload do PDF."

        # ── Images or text_images format ──
        _notify("Renderizando slides do relatorio...")
        slides = render_report_slides(report_data, insights=insights)
        if not slides:
            return "Erro ao gerar as imagens do relatorio."

        # Upload to Cloudinary
        _notify("Fazendo upload das imagens...")
        try:
            import cloudinary
            import cloudinary.uploader

            image_urls = []
            for i, slide_bytes in enumerate(slides):
                result = cloudinary.uploader.upload(
                    slide_bytes,
                    folder="teq/reports",
                    public_id=f"report_{user_id}_{i}",
                    overwrite=True,
                    resource_type="image",
                )
                image_urls.append(result["secure_url"])
        except Exception as e:
            logger.error("Erro ao fazer upload do relatorio: %s", e)
            return "Relatorio gerado mas houve erro ao fazer upload das imagens."

        # Build response
        accounts_list = ", ".join(f"@{a['username']}" for a in report_data["accounts"])
        lines = []

        # Prepend text report for text_images format
        if format == "text_images":
            lines.append(render_report_text(report_data, insights))
            lines.append("")

        lines.extend([
            f"**Relatorio Competitivo gerado!**\n",
            f"Contas analisadas: {accounts_list}\n",
            f"**{len(slides)} slides** com graficos de seguidores, engajamento, "
            f"crescimento e insights.\n",
        ])

        # Summary metrics
        for acc in report_data["accounts"]:
            lines.append(
                f"• @{acc['username']}: {acc['followers']:,} seg. | "
                f"eng. {acc['engagement_rate']}% | crescimento +{acc['growth_pct']}%"
            )

        if format != "text_images":
            lines.append(f"\n**Insights:**\n{insights[:500]}")

        # Send images as media on WhatsApp
        if image_urls and effective_channel in ("whatsapp", "whatsapp_text", "web_whatsapp"):
            try:
                import asyncio as _aio
                from src.integrations.whatsapp import whatsapp_client as _wpp

                async def _send_report_images():
                    total = len(image_urls)
                    for i, url in enumerate(image_urls):
                        try:
                            await _wpp.send_image(
                                user_id, url,
                                caption=f"Slide {i + 1}/{total}",
                            )
                        except Exception as img_err:
                            logger.error("Erro ao enviar slide %s do relatorio via WhatsApp: %s", i + 1, img_err)

                try:
                    loop = _aio.get_running_loop()
                    loop.create_task(_send_report_images())
                except RuntimeError:
                    _aio.run(_send_report_images())
            except Exception as e:
                logger.error("Erro ao enviar imagens do relatorio via WhatsApp: %s", e)
        elif image_urls:
            lines.append("\n**Imagens do relatorio:**")
            for i, url in enumerate(image_urls):
                lines.append(f"Slide {i + 1}: {url}")

        return "\n".join(lines)

    def toggle_trend_alerts(enabled: bool = True) -> str:
        """
        Ativa ou desativa alertas de TENDENCIAS do nicho.
        Quando ativado, voce recebe uma notificacao no WhatsApp quando o TEQ
        detectar um tema em comum entre 2+ contas monitoradas — indicando
        uma tendencia no seu nicho que vale criar conteudo sobre.
        Maximo 1 alerta por dia.

        Args:
            enabled: True para ativar, False para desativar.

        Returns:
            Confirmacao.
        """
        from src.models.social import set_trend_alerts_enabled

        ok = set_trend_alerts_enabled(user_id, enabled)
        if not ok:
            return "Nao consegui atualizar as configuracoes."

        if enabled:
            return (
                "Alertas de tendencias ativados! "
                "Vou te avisar no WhatsApp quando detectar um tema em alta "
                "entre as contas que voce monitora (maximo 1 alerta por dia)."
            )
        return "Alertas de tendencias desativados."

    def view_post_by_url(url: str, question: str = "") -> str:
        """
        Acessa um post especifico do Instagram pelo link e descreve/analisa o conteudo
        visual e textual. Use quando o usuario enviar um link de post do Instagram
        (instagram.com/p/... ou instagram.com/reel/...).

        Args:
            url: URL do post do Instagram (ex: https://www.instagram.com/p/ABC123/).
            question: Pergunta especifica sobre o post (opcional).

        Returns:
            Analise detalhada do conteudo do post (visual + texto).
        """
        import re as _re
        from src.social import get_social_provider

        # Valida URL
        if not _re.search(r"instagram\.com/(p|reel|reels)/", url):
            return "URL invalida. Envie um link de post do Instagram (instagram.com/p/... ou /reel/...)."

        _notify(f"Acessando post...")

        try:
            provider = get_social_provider("instagram")
            post = _run_async(provider.get_post_by_url(url))
        except Exception as e:
            logger.error("Erro ao buscar post por URL %s: %s", url, e)
            return f"Nao consegui acessar esse post: {str(e)}"

        # Formata dados do post
        post_dict = {
            "platform_post_id": post.platform_post_id,
            "content_type": post.content_type,
            "caption": post.caption,
            "hashtags": post.hashtags,
            "media_urls": post.media_urls,
            "thumbnail_url": post.thumbnail_url,
            "likes_count": post.likes_count,
            "comments_count": post.comments_count,
            "views_count": post.views_count,
            "posted_at": post.posted_at,
            "owner_username": post.owner_username,
        }

        posts_text = _format_posts_for_analysis([post_dict])
        user_question = question.strip() if question else "Descreva o conteudo deste post de forma detalhada."
        shortcode = post.metadata.get("shortcode", "")
        post_url = post.metadata.get("url", url)
        owner_info = f"Autor: @{post.owner_username}\n" if post.owner_username else ""

        # Video/Reel: download and analyze full video
        if post.content_type in ("video", "reel") and post.video_url:
            from src.config.feature_gates import check_video_analysis_limit, log_video_analysis
            limit_msg = check_video_analysis_limit(user_id)
            if limit_msg:
                # Over limit — fallback to thumbnail only
                logger.info("Video limit reached for %s, using thumbnail", user_id)
            else:
                if channel == "web":
                    _notify("Processando conteudo do video...")
                else:
                    _notify("Vou dar uma olhada no video e ja te digo...")
                video_duration = post.metadata.get("duration", 0)
                video_bytes = _download_video(post.video_url)
                if video_bytes:
                    logger.info("Analyzing Reel video (%d bytes, ~%.0fs)", len(video_bytes), video_duration)
                    prompt = (
                        f"Voce esta assistindo a um Reel/video do Instagram.\n\n"
                        f"Dados do post:\n{posts_text}\n\n"
                        "Analise o video COMPLETO em detalhe:\n"
                        "- Descreva CADA cena/momento do video\n"
                        "- Transcreva qualquer fala ou texto que apareca\n"
                        "- Descreva elementos visuais, transicoes, musica/efeitos sonoros\n"
                        "- Identifique o estilo de edicao e formato do conteudo\n\n"
                        f"Pergunta do usuario: {user_question}\n\n"
                        "Responda em portugues de forma clara e detalhada."
                    )
                    analysis = _analyze_video(video_bytes, prompt)
                    log_video_analysis(user_id, video_duration or 30)
                    return (
                        f"**Analise do Reel** (video completo)\n"
                        f"{owner_info}"
                        f"Link: {post_url}\n\n"
                        f"{analysis}"
                    )
                # Fallback: analyze thumbnail if video download fails
                logger.warning("Video download failed for %s, falling back to thumbnail analysis", post_url)

        # Image/Carousel: download all slides
        post_images = _download_all_carousel_images(post_dict)

        is_video_fallback = post.content_type in ("video", "reel")
        if is_video_fallback:
            prompt = (
                f"Voce esta olhando a MINIATURA (thumbnail) de um Reel/video do Instagram.\n"
                "IMPORTANTE: Voce NAO esta vendo o video, apenas a imagem de capa.\n"
                "Descreva apenas o que ve na imagem. NAO invente o conteudo do video.\n\n"
                f"Dados do post:\n{posts_text}\n\n"
            )
        else:
            prompt = (
                f"Voce esta olhando para um post do Instagram.\n\n"
                f"Dados do post:\n{posts_text}\n\n"
            )
        if post_images:
            prompt += (
                f"{len(post_images)} imagem(ns) do post estao anexadas (em ordem dos slides).\n"
                "Analise CADA imagem em detalhe: o que aparece, cores, texto na imagem, "
                "composicao, estilo visual, e qualquer elemento relevante.\n\n"
            )
        prompt += (
            f"Pergunta do usuario: {user_question}\n\n"
            "Responda em portugues de forma clara e detalhada."
        )

        analysis = _run_analysis(prompt, images=post_images)
        if is_video_fallback:
            return (
                f"**Analise do Reel** (apenas thumbnail - nao foi possivel baixar o video)\n"
                f"{owner_info}"
                f"Link: {post_url}\n\n"
                f"⚠️ Atencao: esta analise e baseada apenas na miniatura do video, "
                f"nao no conteudo completo do Reel.\n\n"
                f"{analysis}"
            )
        return (
            f"**Analise do post** ({post.content_type})\n"
            f"{owner_info}"
            f"Link: {post_url}\n\n"
            f"{analysis}"
        )

    def view_youtube_video(url: str, question: str = "") -> str:
        """
        Analisa o conteudo de um video do YouTube pelo link. Baixa o video em baixa
        qualidade e usa IA para descrever cenas, transcrever falas e analisar o conteudo.
        Use quando o usuario enviar um link do YouTube (youtube.com/watch ou youtu.be/).

        Args:
            url: URL do video do YouTube.
            question: Pergunta especifica sobre o video (opcional).

        Returns:
            Analise detalhada do conteudo do video.
        """
        import re as _re

        if not _re.search(r"(youtube\.com/watch|youtu\.be/|youtube\.com/shorts/)", url):
            return "URL invalida. Envie um link do YouTube."

        from src.config.feature_gates import check_video_analysis_limit, log_video_analysis
        limit_msg = check_video_analysis_limit(user_id)
        if limit_msg:
            return limit_msg

        if channel == "web":
            _notify("Processando conteudo do video...")
        else:
            _notify("Vou dar uma olhada no video e ja te digo...")

        video_bytes, duration = _download_youtube_video(url)
        if not video_bytes:
            return "Nao consegui baixar esse video. Pode ser privado, restrito por idade ou muito longo."

        user_question = question.strip() if question else "Descreva o conteudo deste video de forma detalhada."

        prompt = (
            f"Voce esta assistindo a um video do YouTube.\n\n"
            f"Duracao: {duration:.0f} segundos\n"
            f"URL: {url}\n\n"
            "Analise o video COMPLETO em detalhe:\n"
            "- Descreva CADA cena/momento do video\n"
            "- Transcreva qualquer fala ou texto que apareca na tela\n"
            "- Descreva elementos visuais, transicoes, musica/efeitos sonoros\n"
            "- Identifique o estilo de edicao e formato do conteudo\n\n"
            f"Pergunta do usuario: {user_question}\n\n"
            "Responda em portugues de forma clara e detalhada."
        )

        logger.info("Analyzing YouTube video (%d bytes, ~%.0fs)", len(video_bytes), duration)
        analysis = _analyze_video(video_bytes, prompt)
        log_video_analysis(user_id, duration or 60)
        return (
            f"**Analise do video YouTube**\n"
            f"Link: {url}\n\n"
            f"{analysis}"
        )

    return (
        preview_account,
        track_account,
        untrack_account,
        list_tracked_accounts,
        get_account_insights,
        get_trending_content,
        analyze_posts,
        create_content_script,
        toggle_alerts,
        toggle_trend_alerts,
        generate_competitive_report,
        view_post_by_url,
        view_youtube_video,
    )


# ──────────────── helpers ────────────────


def _format_posts_for_analysis(posts: list[dict]) -> str:
    """Format posts into a text block for LLM analysis."""
    lines = []
    for i, post in enumerate(posts, 1):
        caption = (post.get("caption", "") or "")[:300]
        hashtags = post.get("hashtags", []) or []
        owner = post.get("owner_username", "")
        owner_line = f"  Autor: @{owner}\n" if owner else ""
        lines.append(
            f"Post {i} [{post.get('content_type', 'post')}]:\n"
            f"{owner_line}"
            f"  Likes: {post.get('likes_count', 0):,} | "
            f"Comentarios: {post.get('comments_count', 0):,} | "
            f"Views: {post.get('views_count', 0):,}\n"
            f"  Caption: {caption}\n"
            f"  Hashtags: {', '.join('#' + h for h in hashtags[:10])}\n"
            f"  Data: {post.get('posted_at', 'desconhecida')}\n"
            f"  Midia: {len(post.get('media_urls') or [])} imagem(ns)"
        )
    return "\n\n".join(lines)


def _get_best_image_url(post: dict) -> str:
    """Pick the best single image URL from a post dict."""
    content_type = (post.get("content_type") or "").lower()
    # For video/reel posts, prefer thumbnail
    if content_type in ("video", "reel"):
        thumb = post.get("thumbnail_url", "")
        if thumb:
            return thumb
    # For image/carousel, use first media_url
    media_urls = post.get("media_urls") or []
    if media_urls:
        return media_urls[0]
    return post.get("thumbnail_url", "") or ""


def _download_all_carousel_images(post: dict, max_slides: int = 10) -> list:
    """Download all carousel images from a single post. Returns agno Image objects."""
    import httpx
    from agno.media import Image

    media_urls = post.get("media_urls") or []
    content_type = (post.get("content_type") or "").lower()
    if content_type in ("video", "reel"):
        thumb = post.get("thumbnail_url", "")
        return _download_post_images([post], max_images=1) if thumb else []

    images = []
    for url in media_urls[:max_slides]:
        try:
            resp = httpx.get(url, timeout=10.0, follow_redirects=True)
            if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image"):
                images.append(Image(content=resp.content))
        except Exception as e:
            logger.debug("Failed to download carousel slide: %s", e)
    return images


def _download_post_images(posts: list[dict], max_images: int = 5) -> list:
    """Download the primary image from each post. Returns agno Image objects.

    Skips posts where download fails (expired CDN URL, timeout, etc).
    """
    import httpx
    from agno.media import Image

    images = []
    for post in posts[:max_images]:
        url = _get_best_image_url(post)
        if not url:
            continue
        try:
            resp = httpx.get(url, timeout=10.0, follow_redirects=True)
            if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image"):
                images.append(Image(content=resp.content))
            else:
                logger.debug("Skipping image for post %s: status=%s", post.get("platform_post_id"), resp.status_code)
        except Exception as e:
            logger.debug("Failed to download image for post %s: %s", post.get("platform_post_id"), e)
    return images


def _run_analysis(prompt: str, images: list | None = None) -> str:
    """Run LLM analysis using Gemini Flash."""
    try:
        from agno.agent import Agent
        agent = Agent(
            model=_get_light_model(),
            description="Voce e um analista de conteudo de redes sociais especializado em estrategia digital.",
        )
        kwargs = {}
        if images:
            kwargs["images"] = images
        result = agent.run(prompt, **kwargs)
        return result.content if hasattr(result, "content") else str(result)
    except Exception as e:
        logger.error("Erro na analise LLM: %s", e)
        return "Nao consegui gerar a analise neste momento. Tente novamente."


def _download_video(video_url: str, max_size_mb: int = 50) -> bytes | None:
    """Download a video from URL. Returns bytes or None if too large/failed."""
    import httpx

    try:
        with httpx.stream("GET", video_url, timeout=60.0, follow_redirects=True) as resp:
            if resp.status_code != 200:
                logger.warning("Video download failed: status=%s", resp.status_code)
                return None
            content_length = int(resp.headers.get("content-length", 0))
            if content_length > max_size_mb * 1024 * 1024:
                logger.warning("Video too large: %sMB (max %sMB)", content_length // (1024*1024), max_size_mb)
                return None
            chunks = []
            total = 0
            for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                total += len(chunk)
                if total > max_size_mb * 1024 * 1024:
                    logger.warning("Video exceeded max size during download")
                    return None
                chunks.append(chunk)
            return b"".join(chunks)
    except Exception as e:
        logger.error("Failed to download video: %s", e)
        return None


def _download_youtube_video(youtube_url: str) -> tuple[bytes | None, float]:
    """Download a YouTube video in low quality using yt-dlp. Returns (bytes, duration_seconds)."""
    import subprocess
    import tempfile
    import json

    try:
        # First get video info for duration
        info_cmd = ["yt-dlp", "--dump-json", "--no-download", youtube_url]
        info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=30)
        duration = 0.0
        if info_result.returncode == 0:
            info = json.loads(info_result.stdout)
            duration = float(info.get("duration", 0))

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        # Download lowest quality video
        cmd = [
            "yt-dlp",
            "-f", "worst[ext=mp4]/worst",
            "--max-filesize", "50M",
            "-o", tmp_path,
            "--no-playlist",
            "--quiet",
            youtube_url,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode != 0:
            logger.error("yt-dlp failed: %s", result.stderr.decode()[:500])
            return None, duration

        import os
        with open(tmp_path, "rb") as f:
            video_bytes = f.read()
        os.unlink(tmp_path)
        return video_bytes, duration
    except Exception as e:
        logger.error("Failed to download YouTube video: %s", e)
        return None, 0.0


def _analyze_video(video_bytes: bytes, prompt: str, mime_type: str = "video/mp4") -> str:
    """Analyze a video using Gemini File API (supports full video understanding)."""
    import os
    import tempfile
    import time
    from google import genai
    from google.genai import types

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return "Chave da API Gemini nao configurada."

    client = genai.Client(api_key=api_key)

    try:
        # Write to temp file for upload
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(video_bytes)
            tmp_path = tmp.name

        # Upload to Gemini File API
        logger.info("Uploading video to Gemini File API (%d bytes)...", len(video_bytes))
        video_file = client.files.upload(file=tmp_path, config={"mime_type": mime_type})
        os.unlink(tmp_path)

        # Wait for processing
        while video_file.state.name == "PROCESSING":
            time.sleep(2)
            video_file = client.files.get(name=video_file.name)

        if video_file.state.name == "FAILED":
            logger.error("Gemini video processing failed")
            return "Nao consegui processar o video. Tente novamente."

        # Generate content with video
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_uri(file_uri=video_file.uri, mime_type=mime_type),
                        types.Part.from_text(text=prompt),
                    ],
                )
            ],
        )

        # Cleanup uploaded file
        try:
            client.files.delete(name=video_file.name)
        except Exception:
            pass

        return response.text or "Nao consegui gerar a analise."

    except Exception as e:
        logger.error("Video analysis failed: %s", e)
        return f"Erro ao analisar video: {e}"


def _format_relative_time(iso_str: str) -> str:
    """Format ISO datetime string to relative time in Portuguese."""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = datetime.now(timezone.utc) - dt
        if diff.total_seconds() < 3600:
            mins = int(diff.total_seconds() / 60)
            return f"ha {mins} min"
        if diff.total_seconds() < 86400:
            hours = int(diff.total_seconds() / 3600)
            return f"ha {hours}h"
        days = diff.days
        return f"ha {days} dia(s)"
    except Exception:
        return iso_str

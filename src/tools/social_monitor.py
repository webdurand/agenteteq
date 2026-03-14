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

    def generate_competitive_report(usernames: str = "", platforms: str = "instagram", format: str = "images") -> str:
        """
        Gera um relatorio comparando perfis monitorados.
        Pode gerar em diferentes formatos conforme a preferencia do usuario.

        Args:
            usernames: Usernames separados por virgula (ex: "natgeo,bbcnews,cnn").
                       Se vazio, usa todas as contas monitoradas.
            platforms: Plataformas separadas por virgula (ex: "instagram,youtube").
            format: Formato do relatorio:
                    - "text": apenas texto estruturado (rapido)
                    - "images": carrossel de imagens com graficos (padrao)
                    - "text_images": texto + imagens
                    - "pdf": documento PDF para download

        Returns:
            Texto com resumo do relatorio e links se aplicavel.
        """
        from src.social.report_generator import (
            collect_report_data,
            generate_insights,
            render_report_slides,
            render_report_text,
            render_report_pdf,
        )

        format = format.strip().lower()
        if format not in ("text", "images", "text_images", "pdf"):
            format = "images"

        platform_list = [p.strip().lower() for p in platforms.split(",") if p.strip()]

        if usernames.strip():
            username_list = [u.strip().lstrip("@").lower() for u in usernames.split(",") if u.strip()]
        else:
            accounts = db_list_tracked_accounts(user_id)
            username_list = [a["username"] for a in accounts]

        if len(username_list) < 2:
            return "Preciso de pelo menos 2 contas para gerar um relatorio comparativo. Informe os usernames."

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
            _notify("Gerando PDF do relatorio...")
            pdf_bytes = render_report_pdf(report_data, insights)
            if not pdf_bytes:
                return "Erro ao gerar o PDF do relatorio."
            try:
                import cloudinary.uploader
                result = cloudinary.uploader.upload(
                    pdf_bytes,
                    folder="teq/reports",
                    public_id=f"report_{user_id}_pdf",
                    overwrite=True,
                    resource_type="raw",
                )
                pdf_url = result["secure_url"]
                text_summary = render_report_text(report_data, insights)
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

        if image_urls:
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
    )


# ──────────────── helpers ────────────────


def _format_posts_for_analysis(posts: list[dict]) -> str:
    """Format posts into a text block for LLM analysis."""
    lines = []
    for i, post in enumerate(posts, 1):
        caption = (post.get("caption", "") or "")[:300]
        hashtags = post.get("hashtags", []) or []
        lines.append(
            f"Post {i} [{post.get('content_type', 'post')}]:\n"
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

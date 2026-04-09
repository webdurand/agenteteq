"""
Video creation tools for the AI agent.
Factory pattern matching social_monitor.py.
"""

import json
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def create_video_tools(user_id: str, channel: str = "unknown", notifier=None):
    """Factory that creates video tools with user_id pre-injected."""

    def _notify(msg: str) -> None:
        if notifier:
            notifier.notify(msg)

    def create_video_script(
        topic: str = "",
        style: str = "tutorial",
        duration: int = 60,
        reference_account: str = "",
        source_type: str = "avatar",
        person_description: str = "",
    ) -> str:
        """
        Cria um roteiro estruturado para video viral (Reels/TikTok/Shorts).
        O roteiro inclui hook, cenas com movimentos, legendas, B-roll e loop optimization.

        Args:
            topic: Tema/ideia do video. Ex: "como aumentar vendas com Instagram".
            style: Formato do video. Opcoes: tutorial, storytelling, listicle, transformation, qa, behind_the_scenes.
            duration: Duracao alvo em segundos (30-80). Padrao: 60.
            reference_account: Username de conta monitorada para inspiracao (opcional).

        Returns:
            Roteiro formatado com preview completo.
        """
        if not topic:
            return "Informe o tema do video. Ex: create_video_script(topic='como aumentar vendas com Instagram')"

        duration = max(30, min(80, duration))

        _notify(f"Criando roteiro de video ({style}) sobre: {topic}...")

        # Get reference context if account provided
        reference_context = ""
        if reference_account:
            reference_account = reference_account.lstrip("@").lower().strip()
            try:
                from src.models.social import get_tracked_account_by_username, get_top_content
                account = get_tracked_account_by_username(user_id, "instagram", reference_account)
                if account:
                    top_posts = get_top_content(account["id"], sort_by="likes_count", limit=5)
                    if top_posts:
                        posts_summary = []
                        for p in top_posts[:5]:
                            caption = (p.get("caption") or "")[:200]
                            likes = p.get("likes_count", 0)
                            posts_summary.append(f"- {caption} (likes: {likes})")
                        reference_context = (
                            f"Conta de referencia: @{reference_account}\n"
                            f"Posts de maior engajamento:\n" + "\n".join(posts_summary)
                        )
            except Exception as e:
                logger.debug("Could not fetch reference account: %s", e)

        # Get brand voice if available
        brand_voice = ""
        try:
            from src.models.branding import get_brand_profile
            profile = get_brand_profile(user_id)
            if profile and profile.get("voice_tone"):
                brand_voice = profile["voice_tone"]
        except Exception:
            pass

        # Generate script
        from src.video.script_generator import generate_script, format_script_preview

        script = generate_script(
            topic=topic,
            style=style,
            duration=duration,
            reference_context=reference_context,
            brand_voice=brand_voice,
            source_type=source_type,
            person_description=person_description,
        )

        if "error" in script:
            return script["error"]

        # Save to DB
        from src.db.session import get_db, get_engine
        from src.db.models import VideoScript

        try:
            VideoScript.__table__.create(get_engine(), checkfirst=True)
        except Exception:
            pass

        script_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        script_json_str = json.dumps(script, ensure_ascii=False)

        try:
            with get_db() as session:
                db_script = VideoScript(
                    id=script_id,
                    user_id=user_id,
                    topic=topic,
                    style=style,
                    framework=script.get("config", {}).get("framework", ""),
                    duration_target=duration,
                    script_json=script_json_str,
                    reference_account=reference_account or None,
                    created_at=now,
                )
                session.add(db_script)
            logger.info("Video script saved: %s (topic: %s)", script_id[:8], topic[:50])
        except Exception as e:
            logger.error("FAILED to save video script %s to DB: %s", script_id[:8], e)
            # Store script in memory as fallback — will be passed via topic in payload
            script["_fallback_script_id"] = script_id

        preview = format_script_preview(script)

        return (
            f"**Roteiro criado!** (ID: {script_id[:8]})\n\n"
            f"{preview}\n\n"
            "---\n"
            "Para gerar o video, use generate_video com este roteiro.\n"
            "Para ajustar o roteiro, peca modificacoes e gero outro."
        )

    def generate_video(
        script_id: str = "",
        topic: str = "",
        source_type: str = "avatar",
        photo_url: str = "",
        video_url: str = "",
        voice: str = "",
        person_description: str = "",
    ) -> str:
        """
        Gera um video completo a partir de um roteiro ou topico.
        O video inclui voz, legendas dinamicas, zoom, B-roll e transicoes.

        Args:
            script_id: ID do roteiro (gerado por create_video_script). Se vazio, gera roteiro automaticamente.
            topic: Tema do video (usado se script_id nao fornecido).
            source_type: Modo de geracao:
                - "avatar": gera pessoa falando a partir de foto (D-ID talking head)
                - "real": usa video enviado pelo criador
                - "ai_motion": gera cenas realistas do usuario em cenarios diferentes (Kling I2V)
            photo_url: URL da foto para modo avatar.
            video_url: URL do video para modo real.
            voice: Voz para narracao (opcional).
            person_description: Descricao da aparencia da pessoa para ai_motion (opcional).

        Returns:
            Status da geracao (enfileirada, posicao, estimativa).
        """
        # Feature gate check
        from src.config.feature_gates import is_feature_enabled, get_plan_limit
        if not is_feature_enabled(user_id, "video_creation_enabled"):
            return (
                "Criacao de video nao esta disponivel no seu plano atual. "
                "Faca upgrade para o plano Premium para desbloquear!"
            )

        # Monthly limit check
        from src.db.session import get_db
        from src.db.models import VideoProject
        from sqlalchemy import func
        from datetime import timedelta

        month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        with get_db() as session:
            monthly_count = session.query(func.count(VideoProject.id)).filter(
                VideoProject.user_id == user_id,
                VideoProject.created_at >= month_start,
                VideoProject.status != "failed",
            ).scalar() or 0

        max_monthly = get_plan_limit(user_id, "max_videos_monthly", 10)
        if monthly_count >= max_monthly:
            return f"Limite de {max_monthly} videos por mes atingido. Aguarde o proximo mes ou faca upgrade."

        # AI Motion specific checks
        avatar_id = ""
        if source_type == "ai_motion":
            if not is_feature_enabled(user_id, "ai_motion_enabled"):
                return (
                    "O modo AI Motion (cenarios realistas) nao esta disponivel no seu plano. "
                    "Faca upgrade para desbloquear!"
                )
            # Get active avatar — validate it exists and has valid frames
            from src.db.models import UserAvatar
            with get_db() as session:
                avatar = (
                    session.query(UserAvatar)
                    .filter(UserAvatar.user_id == user_id, UserAvatar.is_active == True)
                    .order_by(UserAvatar.created_at.desc())
                    .first()
                )
                if not avatar:
                    return (
                        "Voce precisa configurar seu avatar primeiro! "
                        "Envie 1-4 fotos suas ou 1 video curto usando setup_avatar."
                    )
                frames = json.loads(avatar.reference_frames or "[]")
                if not frames:
                    return (
                        "Seu avatar nao tem frames de referencia validos. "
                        "Reconfigure com setup_avatar usando fotos reais."
                    )
                avatar_id = avatar.id
                _notify(f"Usando avatar: {avatar.media_type} com {len(frames)} frame(s) de referencia.")

        if not script_id:
            return (
                "ERRO: Voce precisa de um roteiro aprovado antes de gerar o video.\n\n"
                "Fluxo correto:\n"
                "1. Use create_video_script(topic='...') para gerar o roteiro\n"
                "2. Mostre o roteiro ao usuario e pergunte se quer ajustar\n"
                "3. So depois de aprovado, use generate_video(script_id='...')\n\n"
                "Nunca gere video sem roteiro aprovado pelo usuario."
            )

        # Block duplicate: check if user already has a video being processed
        from src.db.models import BackgroundTask
        with get_db() as session:
            active_video_tasks = session.query(func.count(BackgroundTask.id)).filter(
                BackgroundTask.user_id == user_id,
                BackgroundTask.task_type == "video",
                BackgroundTask.status.in_(["pending", "processing"]),
            ).scalar() or 0

        if active_video_tasks > 0:
            return (
                "Ja tem um video sendo processado! Aguarde ele terminar ou cancele pela aba Fila.\n"
                "Nao e possivel gerar dois videos ao mesmo tempo."
            )

        # Fetch script data for the worker (inline fallback) and progress title
        effective_topic = topic
        script_json_inline = None
        script_title = topic

        if script_id:
            try:
                from src.db.session import get_db
                from src.db.models import VideoScript
                with get_db() as session:
                    s = session.get(VideoScript, script_id)
                    # Fallback: partial ID match (first 8 chars)
                    if not s:
                        s = session.query(VideoScript).filter(
                            VideoScript.id.startswith(script_id)
                        ).first()
                    if s:
                        script_id = s.id  # Use full ID from here on
                        effective_topic = s.topic or topic
                        if s.script_json:
                            script_json_inline = s.script_json
                            _parsed = json.loads(s.script_json)
                            script_title = _parsed.get("title", effective_topic)
                    else:
                        logger.warning("Script %s not in DB at enqueue time — will include topic for fallback", script_id[:8])
            except Exception as e:
                logger.warning("Could not load script %s at enqueue: %s", script_id[:8], e)

        _notify("Enfileirando geracao de video...")

        # Enqueue task — include script_json inline as fallback if DB lookup may fail
        from src.queue.task_queue import enqueue_task

        payload = {
            "script_id": script_id,
            "topic": effective_topic,
            "script_json": script_json_inline,
            "source_type": source_type,
            "photo_url": photo_url,
            "video_url": video_url,
            "voice": voice,
            "person_description": person_description,
            "avatar_id": avatar_id,
        }

        result = enqueue_task(user_id, "video", channel, payload)

        if result["status"] == "budget_exceeded":
            return result.get("message", "Limite de uso mensal atingido.")
        if result["status"] == "limit_reached":
            return "Voce ja tem tarefas sendo processadas. Aguarde uma terminar."

        # Save generating placeholder message for progress tracking in chat
        if result["status"] == "queued":
            try:
                from src.models.chat_messages import save_message
                placeholder = "__VIDEO_GENERATING__" + json.dumps({
                    "current_step": "generating_voice",
                    "title": script_title,
                    "task_id": result.get("task_id", ""),
                })
                save_message(user_id, user_id, "agent", placeholder)
            except Exception:
                pass

        if result["status"] == "queued":
            return (
                f"Video enfileirado! Posicao: {result['position']}\n"
                f"Tempo estimado: {result['estimated_wait']}\n\n"
                "Vou te avisar quando ficar pronto. "
                "O video aparecera no chat com opcao de download."
            )
        return f"Erro ao enfileirar: {result}"

    def list_videos(
        limit: int = 10,
        status: str = "",
    ) -> str:
        """
        Lista os videos do usuario.

        Args:
            limit: Quantidade maxima de videos (1-50).
            status: Filtrar por status (done, generating, failed). Vazio = todos.

        Returns:
            Lista formatada dos videos.
        """
        from src.db.session import get_db
        from src.db.models import VideoProject

        limit = max(1, min(50, limit))

        with get_db() as session:
            query = session.query(VideoProject).filter(
                VideoProject.user_id == user_id,
            )
            if status:
                query = query.filter(VideoProject.status == status)
            videos = query.order_by(VideoProject.created_at.desc()).limit(limit).all()
            results = [v.to_dict() for v in videos]

        if not results:
            return "Nenhum video encontrado." + (" Filtro: " + status if status else "")

        lines = [f"**{len(results)} video(s):**\n"]
        for v in results:
            status_emoji = {
                "done": "[PRONTO]", "failed": "[FALHOU]", "draft": "[RASCUNHO]"
            }.get(v["status"], "[GERANDO]")

            line = f"- {status_emoji} {v['id'][:8]}"
            if v.get("duration"):
                line += f" | {v['duration']}s"
            if v.get("video_url"):
                line += f" | {v['video_url']}"
            if v.get("error_message"):
                line += f" | Erro: {v['error_message'][:50]}"
            lines.append(line)

        return "\n".join(lines)

    def list_video_templates() -> str:
        """
        Lista os templates de formato disponiveis para criacao de video.
        Cada template define a estrutura, framework de copywriting e estilo de edicao.

        Returns:
            Lista formatada dos templates.
        """
        from src.video.templates import list_templates

        templates = list_templates()
        lines = ["**Templates de video disponiveis:**\n"]
        for t in templates:
            lines.append(f"- **{t['id']}**: {t['name']} ({t['framework']}, {t['duration']})")
            lines.append(f"  {t['description']}")

        lines.append("\nUse create_video_script(topic='...', style='template_id') para criar um roteiro.")
        return "\n".join(lines)

    def adjust_video(
        video_id: str = "",
        instructions: str = "",
    ) -> str:
        """
        Pede ajustes em um video ja gerado. Modifica o roteiro com IA e re-enfileira a geracao.
        Reutiliza assets existentes (voz, B-roll) quando possivel.

        Args:
            video_id: ID do video (primeiros 8 chars bastam).
            instructions: Descricao dos ajustes. Ex: "mais zoom no hook", "troca a musica", "legendas maiores".

        Returns:
            Status do ajuste.
        """
        if not video_id or not instructions:
            return "Informe o video_id e as instructions de ajuste."

        from src.db.session import get_db
        from src.db.models import VideoProject, VideoScript

        # Find the video project
        with get_db() as session:
            project = session.get(VideoProject, video_id)
            if not project:
                # Try partial ID match
                project = session.query(VideoProject).filter(
                    VideoProject.id.startswith(video_id),
                    VideoProject.user_id == user_id,
                ).first()
            if not project:
                return f"Video '{video_id}' nao encontrado."
            if project.user_id != user_id:
                return "Voce nao tem acesso a este video."
            if project.status != "done":
                return f"Este video ainda esta em status '{project.status}'. Aguarde finalizar."

            project_dict = project.to_dict()
            script_id = project.script_id

            # Load original script
            original_script = None
            if script_id:
                db_script = session.get(VideoScript, script_id)
                if db_script and db_script.script_json:
                    original_script = json.loads(db_script.script_json)

        if not original_script:
            return "Nao encontrei o roteiro original deste video. Crie um novo roteiro com as mudancas."

        _notify("Aplicando ajustes ao roteiro...")

        # Use LLM to modify the script based on instructions
        from src.video.script_generator import _get_light_model

        prompt = (
            "Voce e um editor de video. Modifique o roteiro JSON abaixo conforme as instrucoes do usuario.\n\n"
            f"INSTRUCOES DO USUARIO: {instructions}\n\n"
            f"ROTEIRO ORIGINAL:\n{json.dumps(original_script, ensure_ascii=False, indent=2)}\n\n"
            "REGRAS:\n"
            "- Retorne APENAS o JSON modificado, sem markdown, sem comentarios.\n"
            "- Mantenha a mesma estrutura (hook, scenes, callback, config).\n"
            "- So modifique o que o usuario pediu. Mantenha o resto igual.\n"
            "- Se o usuario pedir 'mais zoom', aumente o zoom nos movimentos.\n"
            "- Se pedir 'troca musica', mude o music_style no config.\n"
            "- Se pedir 'legendas maiores', mude o caption_style.\n"
            "- Se pedir mudancas de texto, ajuste a narracao e on_screen_text.\n"
        )

        try:
            from agno.agent import Agent
            agent = Agent(
                model=_get_light_model(),
                description="Voce e um editor de roteiro. Responda APENAS com JSON valido.",
            )
            result = agent.run(prompt)
            raw = result.content if hasattr(result, "content") else str(result)

            # Clean up JSON
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()

            modified_script = json.loads(raw)
        except Exception as e:
            return f"Nao consegui aplicar os ajustes: {e}. Tente descrever de outra forma."

        # Save modified script
        new_script_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        with get_db() as session:
            db_script = VideoScript(
                id=new_script_id,
                user_id=user_id,
                topic=original_script.get("title", ""),
                style=modified_script.get("config", {}).get("style", ""),
                framework=modified_script.get("config", {}).get("framework", ""),
                duration_target=modified_script.get("config", {}).get("total_duration_s", 60),
                script_json=json.dumps(modified_script, ensure_ascii=False),
                created_at=now,
            )
            session.add(db_script)

        # Show preview of changes
        from src.video.script_generator import format_script_preview
        preview = format_script_preview(modified_script)

        # Enqueue re-generation
        from src.queue.task_queue import enqueue_task
        payload = {
            "script_id": new_script_id,
            "source_type": project_dict.get("source_type", "avatar"),
            "photo_url": project_dict.get("source_url", ""),
        }

        result = enqueue_task(user_id, "video", channel, payload)

        if result["status"] == "queued":
            return (
                f"**Roteiro ajustado!**\n\n{preview}\n\n"
                "---\n"
                f"Video re-enfileirado! Posicao: {result['position']}\n"
                f"Tempo estimado: {result['estimated_wait']}"
            )

        return f"Roteiro ajustado mas nao consegui enfileirar: {result}"

    def review_video(
        video_id: str = "",
    ) -> str:
        """
        Analisa um video gerado e sugere melhorias baseadas em melhores praticas
        de conteudo viral (hook, retencao, legendas, CTAs).

        Args:
            video_id: ID do video (primeiros 8 chars bastam).

        Returns:
            Analise com sugestoes de melhoria.
        """
        if not video_id:
            return "Informe o video_id para revisar."

        from src.db.session import get_db
        from src.db.models import VideoProject, VideoScript

        with get_db() as session:
            project = session.get(VideoProject, video_id)
            if not project:
                project = session.query(VideoProject).filter(
                    VideoProject.id.startswith(video_id),
                    VideoProject.user_id == user_id,
                ).first()
            if not project or project.user_id != user_id:
                return "Video nao encontrado."
            if project.status != "done":
                return "Este video ainda nao foi finalizado."

            script = None
            if project.script_id:
                db_script = session.get(VideoScript, project.script_id)
                if db_script and db_script.script_json:
                    script = json.loads(db_script.script_json)

        if not script:
            return "Nao encontrei o roteiro deste video para analisar."

        _notify("Analisando video com IA...")

        from src.video.script_generator import _get_light_model

        prompt = (
            "Voce e um estrategista de conteudo viral especializado em Instagram Reels, TikTok e Shorts.\n"
            "Analise o roteiro abaixo e de uma avaliacao HONESTA com sugestoes de melhoria.\n\n"
            f"ROTEIRO:\n{json.dumps(script, ensure_ascii=False, indent=2)}\n\n"
            "ANALISE estes aspectos (nota de 1 a 10 para cada):\n"
            "1. HOOK (primeiros 3 segundos): Forte o suficiente? Tipo de hook adequado?\n"
            "2. OPEN LOOPS: Tem loops que prendem a atencao? Sao fechados corretamente?\n"
            "3. ARCO EMOCIONAL: A progressao emocional funciona? Tem wound/agitation/hope/value/payoff?\n"
            "4. PACING: Ritmo adequado? Cortes a cada 2-4s? Pattern interrupts a cada 15-25s?\n"
            "5. LEGENDAS/OVERLAYS: Texto na tela suficiente? Dentro das safe zones?\n"
            "6. LOOP OPTIMIZATION: O final reconecta com o inicio? Incentiva rewatch?\n"
            "7. COMPARTILHABILIDADE: Conteudo polarizante ou opinativo que gera shares?\n\n"
            "Para cada aspecto: nota, o que esta bom, o que melhorar.\n"
            "No final: NOTA GERAL e TOP 3 ajustes que mais impactariam o engajamento.\n"
            "Responda em portugues, de forma pratica e direta."
        )

        try:
            from agno.agent import Agent
            agent = Agent(
                model=_get_light_model(),
                description="Voce e um analista de conteudo viral.",
            )
            result = agent.run(prompt)
            review = result.content if hasattr(result, "content") else str(result)
            return f"**Analise do video {video_id[:8]}:**\n\n{review}"
        except Exception as e:
            return f"Nao consegui analisar: {e}"

    def add_video_to_calendar(
        video_id: str = "",
        scheduled_at: str = "",
        platform: str = "instagram",
    ) -> str:
        """
        Adiciona um video gerado ao calendario de conteudo.

        Args:
            video_id: ID do video (primeiros 8 chars bastam).
            scheduled_at: Data/hora planejada (ISO 8601). Opcional.
            platform: Plataforma alvo: instagram, youtube, tiktok.

        Returns:
            Confirmacao de adicao ao calendario.
        """
        if not video_id:
            return "Informe o video_id para adicionar ao calendario."

        from src.db.session import get_db
        from src.db.models import VideoProject, VideoScript

        with get_db() as session:
            project = session.get(VideoProject, video_id)
            if not project:
                project = session.query(VideoProject).filter(
                    VideoProject.id.startswith(video_id),
                    VideoProject.user_id == user_id,
                ).first()
            if not project or project.user_id != user_id:
                return "Video nao encontrado."

            # Get title from script
            title = "Video"
            if project.script_id:
                db_script = session.get(VideoScript, project.script_id)
                if db_script and db_script.script_json:
                    script = json.loads(db_script.script_json)
                    title = script.get("title", "Video")

        # Create content plan entry
        from src.models.content_plans import create_content_plan

        plan = create_content_plan(
            user_id=user_id,
            title=title,
            content_type="video",
            platforms=[p.strip() for p in platform.split(",")],
            scheduled_at=scheduled_at,
            description=f"Video ID: {project.id}",
        )

        # Link video to content plan
        now = datetime.now(timezone.utc).isoformat()
        with get_db() as session:
            proj = session.get(VideoProject, project.id)
            if proj:
                proj.content_plan_id = plan.get("id")
                proj.updated_at = now

        date_str = scheduled_at if scheduled_at else "sem data definida"
        return (
            f"Video '{title}' adicionado ao calendario!\n"
            f"Plataforma: {platform}\n"
            f"Data: {date_str}\n"
            f"Status: ready\n\n"
            "Use list_content_plan para ver seu calendario completo."
        )

    def setup_avatar(
        media_urls: str = "",
        media_type: str = "photo",
        voice_audio_url: str = "",
        voice_name: str = "",
        label: str = "",
    ) -> str:
        """
        Configura o avatar do usuario para videos AI Motion.
        O avatar fica salvo no perfil e e reutilizado em todos os videos ai_motion.
        Pode incluir fotos + voz clonada em uma unica chamada.

        Args:
            media_urls: URLs das fotos (1-4, separadas por virgula) ou URL de 1 video.
            media_type: "photo" ou "video".
            voice_audio_url: URL de audio para clonar a voz do usuario (opcional, Cloudinary).
            voice_name: Nome para a voz clonada (opcional).
            label: Nome/label do avatar (opcional, ex: "Eu profissional").

        Returns:
            Confirmacao com detalhes do avatar criado.
        """
        if not media_urls:
            return (
                "Envie as URLs das suas fotos ou video para configurar o avatar.\n"
                "Exemplo: setup_avatar(media_urls='url1,url2', media_type='photo')\n"
                "Para melhor resultado, envie 2-4 fotos em angulos diferentes (frente, perfil, 45 graus)."
            )

        urls = [u.strip() for u in media_urls.split(",") if u.strip()]
        if not urls:
            return "Nenhuma URL valida fornecida."

        if media_type == "video" and len(urls) > 1:
            return "Para video, envie apenas 1 URL."

        if media_type == "photo" and len(urls) > 4:
            return "Maximo de 4 fotos."

        # Validate ALL URLs are accessible before saving
        import httpx as _httpx
        _notify("Validando URLs...")
        for url in urls:
            # Only accept Cloudinary URLs or known valid hosts
            if "cloudinary.com" not in url and "res.cloudinary.com" not in url:
                return (
                    f"URL rejeitada: {url[:80]}...\n"
                    "Apenas URLs do Cloudinary sao aceitas. "
                    "O usuario deve fazer upload das fotos/video pelo chat ou pelo frontend, "
                    "e o sistema faz upload automatico pro Cloudinary. "
                    "NAO invente URLs — use apenas URLs reais de uploads feitos pelo usuario."
                )
            try:
                resp = _httpx.head(url, timeout=10, follow_redirects=True)
                if resp.status_code >= 400:
                    return (
                        f"URL inacessivel (HTTP {resp.status_code}): {url[:80]}...\n"
                        "Verifique se a URL e valida e acessivel."
                    )
            except Exception as e:
                return f"Nao consegui acessar a URL: {url[:80]}... Erro: {e}"

        _notify("Configurando avatar...")

        from src.db.session import get_db
        from src.db.models import UserAvatar
        import json as _json

        if media_type == "video":
            # Extract frames from video
            _notify("Extraindo frames do video...")
            import asyncio
            from src.video.frame_extractor import extract_key_frames

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        reference_frames = pool.submit(
                            asyncio.run,
                            extract_key_frames(urls[0], user_id, 4)
                        ).result()
                else:
                    reference_frames = asyncio.run(
                        extract_key_frames(urls[0], user_id, 4)
                    )
            except Exception as e:
                return f"Erro ao extrair frames do video: {e}"
        else:
            reference_frames = urls[:]

        # Deactivate previous and create new
        with get_db() as session:
            session.query(UserAvatar).filter(
                UserAvatar.user_id == user_id,
                UserAvatar.is_active == True,
            ).update({"is_active": False})

            avatar = UserAvatar(
                user_id=user_id,
                media_type=media_type,
                media_urls=_json.dumps(urls),
                reference_frames=_json.dumps(reference_frames),
                is_active=True,
                label=label or None,
            )
            session.add(avatar)
            session.flush()
            avatar_id = avatar.id
            avatar_dict = avatar.to_dict()

        # Clone voice if audio provided
        voice_status = "Sem voz clonada (padrao sera usada)"
        if voice_audio_url:
            if "cloudinary.com" not in voice_audio_url:
                voice_status = "URL de audio rejeitada (apenas Cloudinary). Use o frontend para upload."
            else:
                try:
                    _notify("Clonando voz...")
                    import httpx as _hx
                    resp = _hx.get(voice_audio_url, timeout=30)
                    resp.raise_for_status()
                    audio_bytes = resp.content

                    from src.video.voice_cloner import clone_voice
                    import asyncio as _aio
                    try:
                        loop = _aio.get_event_loop()
                        if loop.is_running():
                            import concurrent.futures
                            with concurrent.futures.ThreadPoolExecutor() as pool:
                                clone_result = pool.submit(
                                    _aio.run,
                                    clone_voice(audio_bytes, voice_name or "Minha voz", user_id)
                                ).result()
                        else:
                            clone_result = _aio.run(
                                clone_voice(audio_bytes, voice_name or "Minha voz", user_id)
                            )
                    except RuntimeError:
                        clone_result = _aio.run(
                            clone_voice(audio_bytes, voice_name or "Minha voz", user_id)
                        )

                    # Save voice to avatar
                    with get_db() as session:
                        av = session.get(UserAvatar, avatar_id)
                        if av:
                            av.voice_id = clone_result["voice_id"]
                            av.voice_name = clone_result["voice_name"]
                            av.voice_sample_url = voice_audio_url
                    voice_status = f"Voz clonada: {clone_result['voice_name']}"
                except Exception as e:
                    voice_status = f"Erro ao clonar voz: {e}. Avatar criado sem voz."

        num_frames = len(reference_frames)
        return (
            f"Avatar configurado com sucesso!\n"
            f"Nome: {label or 'Avatar'}\n"
            f"Tipo: {media_type}\n"
            f"Frames de referencia: {num_frames}\n"
            f"Voz: {voice_status}\n"
            f"ID: {avatar_dict['id'][:8]}\n\n"
            "Agora voce pode gerar videos com source_type='ai_motion'."
        )

    def edit_script(
        script_id: str = "",
        instructions: str = "",
    ) -> str:
        """
        Edita um roteiro existente com instrucoes em linguagem natural.
        O roteiro e modificado pela IA mantendo a estrutura, e o preview atualizado e exibido.

        Args:
            script_id: ID do roteiro (primeiros 8 chars bastam).
            instructions: O que modificar. Ex: "muda o hook pra curiosidade", "troca a cena 3 por storytelling".

        Returns:
            Preview do roteiro modificado.
        """
        if not script_id or not instructions:
            return "Informe o script_id e as instructions de edicao."

        from src.db.session import get_db
        from src.db.models import VideoScript

        # Load script
        with get_db() as session:
            db_script = session.get(VideoScript, script_id)
            if not db_script:
                db_script = session.query(VideoScript).filter(
                    VideoScript.id.startswith(script_id),
                    VideoScript.user_id == user_id,
                ).first()
            if not db_script or db_script.user_id != user_id:
                return f"Roteiro '{script_id}' nao encontrado."

            original_script = json.loads(db_script.script_json)
            real_script_id = db_script.id

        _notify("Editando roteiro...")

        from src.video.script_generator import _get_light_model

        prompt = (
            "Voce e um roteirista de videos virais. Modifique o roteiro JSON abaixo conforme as instrucoes.\n\n"
            f"INSTRUCOES: {instructions}\n\n"
            f"ROTEIRO:\n{json.dumps(original_script, ensure_ascii=False, indent=2)}\n\n"
            "REGRAS:\n"
            "- Retorne APENAS o JSON modificado, sem markdown.\n"
            "- Mantenha a mesma estrutura (hook, scenes, callback, config).\n"
            "- So modifique o que foi pedido.\n"
            "- Mantenha person_description e i2v_prompt consistentes se existirem.\n"
        )

        try:
            from agno.agent import Agent
            agent = Agent(
                model=_get_light_model(),
                description="Editor de roteiro. Responda APENAS com JSON valido.",
            )
            result = agent.run(prompt)
            raw = result.content if hasattr(result, "content") else str(result)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()

            modified_script = json.loads(raw)
        except Exception as e:
            return f"Nao consegui editar o roteiro: {e}. Tente descrever de outra forma."

        # Save updated script
        now = datetime.now(timezone.utc).isoformat()
        with get_db() as session:
            db_script = session.get(VideoScript, real_script_id)
            if db_script:
                db_script.script_json = json.dumps(modified_script, ensure_ascii=False)

        from src.video.script_generator import format_script_preview
        preview = format_script_preview(modified_script)

        return (
            f"**Roteiro editado!** (ID: {real_script_id[:8]})\n\n"
            f"{preview}\n\n"
            "---\n"
            "Quer mais ajustes? Ou pode gerar o video com generate_video."
        )

    return (
        create_video_script,
        generate_video,
        list_videos,
        list_video_templates,
        adjust_video,
        review_video,
        add_video_to_calendar,
        setup_avatar,
        edit_script,
    )

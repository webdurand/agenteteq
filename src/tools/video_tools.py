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
    ) -> str:
        """
        Gera um video completo a partir de um roteiro ou topico.
        O video inclui voz, legendas dinamicas, zoom, B-roll e transicoes.

        Args:
            script_id: ID do roteiro (gerado por create_video_script). Se vazio, gera roteiro automaticamente.
            topic: Tema do video (usado se script_id nao fornecido).
            source_type: "avatar" (gera pessoa falando a partir de foto) ou "real" (usa video enviado pelo criador).
            photo_url: URL da foto para modo avatar.
            video_url: URL do video para modo real.
            voice: Voz para narracao (opcional).

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

        if not script_id and not topic:
            return "Informe o script_id de um roteiro existente ou um topic para gerar automaticamente."

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
                    if s:
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

    return (
        create_video_script,
        generate_video,
        list_videos,
        list_video_templates,
        adjust_video,
        review_video,
        add_video_to_calendar,
    )

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
            duration: Duracao alvo em segundos (10-120). Padrao: 60.
            reference_account: Username de conta monitorada para inspiracao (opcional).

        Returns:
            Roteiro formatado com preview completo.
        """
        if not topic:
            return "Informe o tema do video. Ex: create_video_script(topic='como aumentar vendas com Instagram')"

        duration = max(10, min(120, duration))

        # Force heygen (only supported mode)
        source_type = "heygen"
        try:
            from src.db.models import UserAvatar
            from src.db.session import get_db
            with get_db() as session:
                has_avatar = (
                    session.query(UserAvatar)
                    .filter(UserAvatar.user_id == user_id, UserAvatar.is_active == True)
                    .first()
                )
                if has_avatar and not person_description and has_avatar.label:
                    person_description = has_avatar.label
        except Exception:
            pass

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

    def create_video_script_direct(
        scenes_text: str = "",
        title: str = "",
        style: str = "storytelling",
        person_description: str = "",
    ) -> str:
        """
        Cria roteiro direto a partir de falas JA APROVADAS pelo usuario no chat.
        NAO usa LLM — apenas converte as falas em JSON estruturado pro HeyGen.

        Use esta tool quando o usuario combinou as falas no chat e aprovou.
        Use create_video_script quando precisa gerar roteiro do zero.

        Args:
            scenes_text: Falas separadas por '|'. Ex: "fala cena 1|fala cena 2|fala cena 3".
            title: Titulo do video (opcional).
            style: Formato (tutorial, storytelling, etc). Padrao: storytelling.
            person_description: Descricao do avatar (opcional).

        Returns:
            Preview do roteiro com ID para gerar o video.
        """
        if not scenes_text:
            return "Informe as falas aprovadas separadas por '|'. Ex: create_video_script_direct(scenes_text='fala 1|fala 2|fala 3')"

        scenes_list = [s.strip() for s in scenes_text.split("|") if s.strip()]
        if not scenes_list:
            return "Nenhuma fala encontrada. Separe as falas com '|'."

        # Load person_description from avatar if not provided
        if not person_description:
            try:
                from src.db.models import UserAvatar
                from src.db.session import get_db
                with get_db() as session:
                    avatar = (
                        session.query(UserAvatar)
                        .filter(UserAvatar.user_id == user_id, UserAvatar.is_active == True)
                        .first()
                    )
                    if avatar and avatar.label:
                        person_description = avatar.label
            except Exception:
                pass

        person_desc = person_description or "o creator"

        # Background color palette — alternates per scene
        BG_PALETTE = [
            "#0D1117", "#1a1a2e", "#2d6a4f", "#7209b7",
            "#f77f00", "#e63946", "#264653", "#023e8a",
        ]

        # Build script JSON mechanically — no LLM
        from src.video.templates import get_template

        template = get_template(style)
        total_words = 0

        # First scene = hook, last scene = callback (if 3+), middle = scenes
        hook_text = scenes_list[0]
        total_words += len(hook_text.split())

        hook = {
            "type": "bold_statement",
            "narration": hook_text,
            "on_screen_text": "",
            "duration_s": max(3, int(len(hook_text.split()) / 2.3)),
            "open_loop": "",
            "heygen_background": {"type": "color", "value": BG_PALETTE[0]},
            "heygen_emotion": "Excited",
            "heygen_speed": 1.0,
        }

        middle_scenes = []
        callback = None

        if len(scenes_list) >= 3:
            # Last scene = callback
            cb_text = scenes_list[-1]
            total_words += len(cb_text.split())
            callback = {
                "narration": cb_text,
                "on_screen_text": "",
                "duration_s": max(3, int(len(cb_text.split()) / 2.3)),
                "heygen_background": {"type": "color", "value": BG_PALETTE[(len(scenes_list) - 1) % len(BG_PALETTE)]},
                "heygen_emotion": "Soothing",
                "heygen_speed": 1.0,
            }
            scene_texts = scenes_list[1:-1]
        elif len(scenes_list) == 2:
            scene_texts = scenes_list[1:]
        else:
            scene_texts = []

        for i, text in enumerate(scene_texts):
            total_words += len(text.split())
            middle_scenes.append({
                "name": f"cena_{i + 1}",
                "narration": text,
                "on_screen_text": "",
                "duration_s": max(3, int(len(text.split()) / 2.3)),
                "heygen_background": {"type": "color", "value": BG_PALETTE[(i + 1) % len(BG_PALETTE)]},
                "heygen_emotion": "Friendly",
                "heygen_speed": 1.0,
                "loop_note": "",
            })

        estimated_duration = max(10, int(total_words / 2.3))

        script = {
            "title": title or "Video",
            "person_description": person_desc,
            "hook": hook,
            "scenes": middle_scenes,
            "callback": callback or {},
            "config": {
                "framework": template["framework"],
                "style": style,
                "total_duration_s": estimated_duration,
                "total_words": total_words,
                "suggested_hashtags": [],
                "suggested_caption": "",
            },
        }

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
                    topic=title or scenes_list[0][:100],
                    style=style,
                    framework=template["framework"],
                    duration_target=estimated_duration,
                    script_json=script_json_str,
                    created_at=now,
                )
                session.add(db_script)
            logger.info("Direct video script saved: %s (%d scenes, ~%ds)", script_id[:8], len(scenes_list), estimated_duration)
        except Exception as e:
            logger.error("FAILED to save direct video script %s: %s", script_id[:8], e)
            script["_fallback_script_id"] = script_id

        from src.video.script_generator import format_script_preview
        preview = format_script_preview(script)

        return (
            f"**Roteiro criado (direto)!** (ID: {script_id[:8]})\n\n"
            f"{preview}\n\n"
            f"Duracao estimada: ~{estimated_duration}s ({total_words} palavras)\n\n"
            "---\n"
            "Para gerar o video, use generate_video com este roteiro.\n"
            "Para ajustar, peca as mudancas no chat."
        )

    def generate_video(
        script_id: str = "",
        topic: str = "",
        source_type: str = "heygen",
        photo_url: str = "",
        video_url: str = "",
        voice: str = "",
        person_description: str = "",
    ) -> str:
        """
        Gera um video com avatar HeyGen + voz do Digital Twin.

        Args:
            script_id: ID do roteiro aprovado (gerado por create_video_script).
            topic: Tema do video (fallback se script_id nao fornecido).

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

        # Force heygen
        source_type = "heygen"
        avatar_id = ""

        from src.db.models import UserAvatar
        with get_db() as session:
            avatar = (
                session.query(UserAvatar)
                .filter(UserAvatar.user_id == user_id, UserAvatar.is_active == True)
                .order_by(UserAvatar.created_at.desc())
                .first()
            )
            if not avatar or not avatar.heygen_avatar_id:
                return (
                    "Avatar HeyGen nao configurado. "
                    "Use setup_avatar para criar seu avatar no HeyGen primeiro."
                )
            avatar_id = avatar.id
            _notify(f"Usando avatar HeyGen: {avatar.label or 'Meu Avatar'}")

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
            logger.info("generate_video blocked: user %s already has %d active video task(s)", user_id, active_video_tasks)
            return (
                "Seu video ja esta sendo gerado! Aguarde ele terminar. "
                "Vou te avisar no chat quando ficar pronto."
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
                    "current_step": "generating_scenes",
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
            lines.append(f"- **{t['id']}**: {t['name']} ({t['framework']})")
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

        # Show preview of changes — do NOT auto-enqueue, let user approve first
        from src.video.script_generator import format_script_preview
        preview = format_script_preview(modified_script)

        return (
            f"**Roteiro ajustado!** (ID: {new_script_id[:8]})\n\n{preview}\n\n"
            "---\n"
            "Quer gerar o video com esse roteiro? Use generate_video para produzir."
        )

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

        # ── HeyGen Avatar Setup ──
        heygen_status = "Nao configurado"
        import os as _os
        if _os.getenv("HEYGEN_API_KEY"):
            try:
                _notify("Configurando avatar no HeyGen (upload + treinamento)...")
                import asyncio as _aio2
                from src.video.providers.heygen import setup_full_avatar

                async def _do_heygen_setup():
                    return await setup_full_avatar(
                        photo_urls=reference_frames,
                        avatar_name=label or f"Avatar {user_id[:8]}",
                        train=True,
                    )

                try:
                    loop2 = _aio2.get_event_loop()
                    if loop2.is_running():
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            heygen_result = pool.submit(_aio2.run, _do_heygen_setup()).result()
                    else:
                        heygen_result = _aio2.run(_do_heygen_setup())
                except RuntimeError:
                    heygen_result = _aio2.run(_do_heygen_setup())

                heygen_group_id = heygen_result.get("group_id", "")
                heygen_avatar_look_id = heygen_result.get("avatar_id", "")
                heygen_flow_id = heygen_result.get("flow_id", "")

                # Save HeyGen IDs to avatar
                with get_db() as session:
                    av = session.get(UserAvatar, avatar_id)
                    if av:
                        av.heygen_group_id = heygen_group_id
                        av.heygen_avatar_id = heygen_avatar_look_id
                        av.heygen_training_status = "training" if heygen_flow_id else "pending"

                heygen_status = (
                    f"HeyGen configurado! Group: {heygen_group_id[:8]}... "
                    f"Treinamento iniciado."
                )

                # Try to wait for training (up to 3 min in background)
                _notify("HeyGen treinando avatar... isso pode levar alguns minutos.")

            except Exception as e:
                logger.error("HeyGen setup failed: %s", e)
                heygen_status = f"Erro no HeyGen: {e}. Avatar local criado normalmente."

        num_frames = len(reference_frames)
        result_msg = (
            f"Avatar configurado com sucesso!\n"
            f"Nome: {label or 'Avatar'}\n"
            f"Tipo: {media_type}\n"
            f"Frames de referencia: {num_frames}\n"
            f"Voz: {voice_status}\n"
            f"HeyGen: {heygen_status}\n"
            f"ID: {avatar_dict['id'][:8]}\n\n"
        )

        if "HeyGen configurado" in heygen_status:
            result_msg += (
                "IMPORTANTE: O HeyGen esta treinando seu avatar. "
                "Isso pode levar 2-10 minutos. "
                "Quando o treinamento terminar, voce precisa configurar sua voz. "
                "Me envie um audio de 30 segundos a 5 minutos falando naturalmente "
                "para eu clonar sua voz no HeyGen.\n\n"
                "Depois disso, voce podera gerar videos com source_type='heygen'."
            )
        else:
            result_msg += "Voce pode gerar videos com source_type='ai_motion'."

        return result_msg

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

    def setup_heygen_voice(
        voice_id: str = "",
    ) -> str:
        """
        Configura a voz do HeyGen para o avatar ativo.
        O Teq lista as vozes disponiveis no HeyGen e o usuario escolhe.
        Tambem pode ser uma voz ja clonada no HeyGen.

        Args:
            voice_id: ID da voz HeyGen (de list_voices ou voz clonada).

        Returns:
            Confirmacao da voz configurada.
        """
        if not voice_id:
            # List available voices
            _notify("Buscando vozes disponiveis no HeyGen...")
            import asyncio as _aio3
            from src.video.providers.heygen import list_voices

            try:
                loop3 = _aio3.get_event_loop()
                if loop3.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        voices = pool.submit(_aio3.run, list_voices()).result()
                else:
                    voices = _aio3.run(list_voices())
            except RuntimeError:
                voices = _aio3.run(list_voices())

            if not voices:
                return "Nenhuma voz encontrada no HeyGen."

            # Show Portuguese voices first, then multilingual
            pt_voices = [v for v in voices if "portuguese" in v.get("language", "").lower() or "multilingual" in v.get("language", "").lower()]
            voice_list = pt_voices[:15] if pt_voices else voices[:15]

            lines = ["Vozes disponiveis no HeyGen (portugues/multilingual):\n"]
            for v in voice_list:
                preview = f" | Preview: {v['preview_audio']}" if v.get("preview_audio") else ""
                lines.append(f"- **{v.get('name', '?')}** ({v.get('gender', '?')}) | ID: `{v.get('voice_id', '')}`{preview}")

            lines.append("\nEscolha uma voz e chame: setup_heygen_voice(voice_id='ID_ESCOLHIDO')")
            lines.append("Ou, se o usuario quiser clonar a propria voz, ele precisa fazer isso direto no HeyGen por enquanto.")
            return "\n".join(lines)

        # Save voice_id to active avatar
        from src.db.session import get_db
        from src.db.models import UserAvatar

        with get_db() as session:
            avatar = (
                session.query(UserAvatar)
                .filter(UserAvatar.user_id == user_id, UserAvatar.is_active == True)
                .order_by(UserAvatar.created_at.desc())
                .first()
            )
            if not avatar:
                return "Nenhum avatar ativo encontrado. Configure um avatar primeiro com setup_avatar."

            avatar.heygen_voice_id = voice_id

        _notify("Voz HeyGen configurada!")
        return (
            f"Voz HeyGen configurada com sucesso!\n"
            f"Voice ID: {voice_id}\n"
            f"Avatar: {avatar.label or avatar.id[:8]}\n\n"
            "Agora voce pode gerar videos com o HeyGen! "
            "Use create_video_script e generate_video normalmente."
        )

    def update_voice(
        audio_urls: str = "",
        voice_name: str = "",
        action: str = "add",
    ) -> str:
        """
        Gerencia a voz clonada do avatar.
        Aceita audios PICOTADOS (1 por vez ou varios de uma vez). Acumula tudo.
        Quando quiser, clona a voz no ElevenLabs com todas as amostras.

        Args:
            audio_urls: URLs de audio separadas por virgula (Cloudinary).
            voice_name: Nome para a voz (opcional).
            action: "add" salva amostras, "clone" clona no ElevenLabs com tudo que tem,
                    "status" mostra amostras salvas, "clear" limpa amostras.

        Returns:
            Status ou confirmacao.
        """
        from src.db.session import get_db
        from src.db.models import UserAvatar
        import json as _json

        with get_db() as session:
            avatar = (
                session.query(UserAvatar)
                .filter(UserAvatar.user_id == user_id, UserAvatar.is_active == True)
                .first()
            )
            if not avatar:
                return "Nenhum avatar ativo. Configure um com setup_avatar."
            avatar_id_local = avatar.id
            existing_samples = _json.loads(avatar.voice_samples or "[]")
            current_voice = avatar.voice_id

        if action == "status":
            return (
                f"**Amostras de voz salvas:** {len(existing_samples)}\n"
                + ("\n".join(f"  {i+1}. {u[:60]}..." for i, u in enumerate(existing_samples)) if existing_samples else "  Nenhuma amostra salva.\n")
                + f"\n\n**Voz clonada atual:** {'sim (ID: ' + current_voice + ')' if current_voice else 'nao'}\n"
                + f"\nDica: quanto mais amostras (ideal 5-25), melhor a qualidade.\n"
                + "Pode mandar audios picotados — eu acumulo tudo.\n"
                + "Quando quiser clonar, use update_voice(action='clone')."
            )

        if action == "clear":
            with get_db() as session:
                av = session.get(UserAvatar, avatar_id_local)
                if av:
                    av.voice_samples = "[]"
            return "Amostras de voz limpas! Envie novos audios quando quiser."

        if not audio_urls and action == "add":
            return (
                f"Voce tem {len(existing_samples)} amostra(s) salva(s).\n\n"
                "**Como melhorar sua voz clonada:**\n"
                "1. Me envie audios pelo chat (pode ser picotado, varios curtos)\n"
                "2. Cada audio deve ter 30s a 5 min de fala limpa\n"
                "3. Fale naturalmente, como numa conversa\n"
                "4. Ambiente silencioso, sem musica de fundo\n"
                "5. Varie emocoes: fale animado, serio, calmo, surpreso\n"
                "6. Quanto mais amostras (5-25), melhor a qualidade\n\n"
                "Quando tiver amostras suficientes, diga 'clona minha voz' que eu processo tudo!"
            )

        # Add new samples
        if audio_urls and action in ("add", "clone"):
            urls = [u.strip() for u in audio_urls.split(",") if u.strip()]
            for url in urls:
                if "cloudinary.com" not in url:
                    return f"URL rejeitada: {url[:80]}... (apenas Cloudinary)"

            all_samples = existing_samples + urls
            with get_db() as session:
                av = session.get(UserAvatar, avatar_id_local)
                if av:
                    av.voice_samples = _json.dumps(all_samples)
                    av.voice_sample_url = urls[0]

            _notify(f"{len(urls)} amostra(s) salva(s)! Total: {len(all_samples)}")

            if action == "add":
                return (
                    f"Audio(s) salvo(s)! Total de amostras: **{len(all_samples)}**\n\n"
                    "Pode enviar mais audios ou dizer 'clona minha voz' quando quiser processar."
                )

        # Clone on ElevenLabs
        if action == "clone":
            with get_db() as session:
                av = session.get(UserAvatar, avatar_id_local)
                all_samples = _json.loads(av.voice_samples or "[]") if av else []

            if not all_samples:
                return "Nenhuma amostra salva. Envie audios primeiro."

            _notify(f"Clonando voz no ElevenLabs com {len(all_samples)} amostra(s)...")

            import asyncio as _aio_clone
            import httpx as _hx_clone
            from src.video.voice_cloner import clone_voice

            try:
                # Download all audio samples
                async def _do_clone():
                    audio_bytes_list = []
                    async with _hx_clone.AsyncClient(timeout=60) as client:
                        for url in all_samples:
                            resp = await client.get(url)
                            resp.raise_for_status()
                            audio_bytes_list.append(resp.content)

                    # Clone with all samples
                    result = await clone_voice(
                        audio_samples=audio_bytes_list,
                        voice_name=voice_name or "Minha voz",
                        user_id=user_id,
                    )
                    return result

                try:
                    loop = _aio_clone.get_event_loop()
                    if loop.is_running():
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            clone_result = pool.submit(_aio_clone.run, _do_clone()).result()
                    else:
                        clone_result = _aio_clone.run(_do_clone())
                except RuntimeError:
                    clone_result = _aio_clone.run(_do_clone())

                new_voice_id = clone_result["voice_id"]

                # Save to avatar
                with get_db() as session:
                    av = session.get(UserAvatar, avatar_id_local)
                    if av:
                        av.voice_id = new_voice_id
                        av.voice_name = voice_name or "Minha voz"

                return (
                    f"Voz clonada com sucesso no ElevenLabs!\n"
                    f"Voice ID: {new_voice_id}\n"
                    f"Amostras usadas: {len(all_samples)}\n\n"
                    "A proxima geracao de video ja vai usar essa voz!\n"
                    "Se quiser melhorar ainda mais, envie mais audios e clone de novo."
                )

            except Exception as e:
                logger.error("ElevenLabs clone failed: %s", e)
                return f"Erro ao clonar voz: {e}\nAs amostras estao salvas. Tente novamente."

        return f"Acao '{action}' nao reconhecida. Use: add, clone, status, clear."

    def setup_digital_twin(
        video_url: str = "",
        video_type: str = "",
        avatar_name: str = "",
    ) -> str:
        """
        Configura um Digital Twin no HeyGen para usar Avatar Shots (Seedance 2.0).
        Aceita videos UM DE CADA VEZ. Primeiro o de treinamento, depois o de consentimento.

        Se chamada SEM argumentos, retorna instrucoes de gravacao.
        Se chamada COM video_url + video_type, salva o video e avanca no fluxo.
        Quando os dois videos (treinamento + consentimento) estiverem salvos, envia pro HeyGen.

        Args:
            video_url: URL do video (Cloudinary).
            video_type: "training" (video de treinamento, 2-5 min) ou "consent" (video de consentimento, 10-30s).
            avatar_name: Nome para o Digital Twin (opcional).

        Returns:
            Instrucoes, confirmacao de recebimento, ou status do envio pro HeyGen.
        """
        from src.db.session import get_db
        from src.db.models import UserAvatar

        # Check current state
        with get_db() as session:
            avatar = (
                session.query(UserAvatar)
                .filter(UserAvatar.user_id == user_id, UserAvatar.is_active == True)
                .first()
            )
            if not avatar:
                return "Nenhum avatar ativo. Configure um com setup_avatar primeiro."
            avatar_id_local = avatar.id
            has_training = bool(avatar.heygen_training_video_url)
            has_consent = bool(avatar.heygen_consent_video_url)

        if not video_url:
            # Show instructions + current state
            state_msg = ""
            if has_training and not has_consent:
                state_msg = (
                    "\n**Estado atual:** Video de treinamento ja recebido! "
                    "Falta apenas o video de consentimento.\n\n"
                )
            elif has_consent and not has_training:
                state_msg = (
                    "\n**Estado atual:** Video de consentimento ja recebido! "
                    "Falta apenas o video de treinamento.\n\n"
                )
            elif has_training and has_consent:
                state_msg = (
                    "\n**Estado atual:** Ambos os videos ja foram recebidos! "
                    "Chame setup_digital_twin(video_type='send') para enviar pro HeyGen.\n\n"
                )

            return (
                "**Como criar seu Digital Twin (Seedance 2.0)**\n\n"
                "O Digital Twin permite criar videos CINEMATOGRAFICOS "
                "com cenarios dinamicos, movimentos de camera e voce como ator.\n\n"
                f"{state_msg}"
                "**OPCAO 1 — Pelo site do HeyGen (RECOMENDADO, mais rapido):**\n"
                "1. Acesse **app.heygen.com** → Avatars → Create Avatar\n"
                "2. Escolha **Avatar V** (cria Digital Twin em 15 segundos!)\n"
                "3. Grave 15 segundos de webcam olhando pra camera\n"
                "4. Pronto! Copie o **Avatar ID** do Digital Twin criado\n"
                "5. Me diga o ID aqui que eu vinculo ao seu avatar!\n\n"
                "**OPCAO 2 — Me envie os videos (se API permitir):**\n"
                "1. Video de treinamento (minimo 15s, ideal 1-2 min)\n"
                "2. Video de consentimento (10-30s dizendo: "
                '"Eu, [seu nome], autorizo a criacao do meu avatar digital na plataforma HeyGen.")\n'
                "3. Pode enviar um de cada vez!\n\n"
                "**Dicas pro video ficar bom:**\n"
                "- Olhe pra camera, iluminacao boa\n"
                "- Use gestos naturais com as maos\n"
                "- Varie expressoes: sorria, fique serio, demonstre surpresa\n"
                "- Fundo limpo\n\n"
                "**Se ja criou pelo site**, me diz o ID do avatar que eu configuro tudo!"
            )

        # Handle "send" — trigger HeyGen when both videos are ready
        if video_type == "send":
            with get_db() as session:
                av = session.get(UserAvatar, avatar_id_local)
                training_url = av.heygen_training_video_url if av else ""
                consent_url = av.heygen_consent_video_url if av else ""

            if not training_url:
                return "Falta o video de treinamento. Envie primeiro."
            if not consent_url:
                return "Falta o video de consentimento. Envie primeiro."

            # Both ready — send to HeyGen
            _notify("Enviando videos para treinamento do Digital Twin no HeyGen...")

            import asyncio as _aio_dt
            from src.video.providers.heygen import create_digital_twin

            try:
                async def _do_create():
                    return await create_digital_twin(
                        video_url=training_url,
                        consent_video_url=consent_url,
                        avatar_name=avatar_name or f"Digital Twin {user_id[:8]}",
                    )

                try:
                    loop = _aio_dt.get_event_loop()
                    if loop.is_running():
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            dt_avatar_id = pool.submit(_aio_dt.run, _do_create()).result()
                    else:
                        dt_avatar_id = _aio_dt.run(_do_create())
                except RuntimeError:
                    dt_avatar_id = _aio_dt.run(_do_create())

                with get_db() as session:
                    av = session.get(UserAvatar, avatar_id_local)
                    if av:
                        av.heygen_avatar_id = dt_avatar_id
                        av.heygen_avatar_type = "digital_twin"
                        av.heygen_training_status = "training"

                return (
                    f"Digital Twin enviado para treinamento!\n"
                    f"Avatar ID: {dt_avatar_id}\n"
                    f"Tempo estimado: 10-20 minutos\n\n"
                    "Quando terminar, voce podera gerar videos cinematograficos "
                    "com Seedance 2.0!\n\n"
                    "Use manage_digital_twin(action='status') pra checar o progresso."
                )

            except Exception as e:
                logger.error("Digital Twin creation failed: %s", e)
                if "PLANO_SEM_ACESSO" in str(e):
                    return (
                        "A criacao de Digital Twin pela API requer um plano superior no HeyGen.\n\n"
                        "**Mas voce pode criar pelo site!** E bem simples:\n"
                        "1. Acesse **app.heygen.com** → Avatars → Create Avatar\n"
                        "2. Escolha **Digital Twin / Video Avatar**\n"
                        "3. Suba o video de treinamento (o mesmo que voce me mandou)\n"
                        "4. Suba o video de consentimento\n"
                        "5. Espere o treinamento (10-20 min)\n"
                        "6. Copie o **Avatar ID** do Digital Twin criado\n"
                        "7. Me diga o ID e eu vinculo ao seu avatar aqui!\n\n"
                        "Seus videos estao salvos no Cloudinary:\n"
                        f"- Treinamento: {training_url}\n"
                        f"- Consentimento: {consent_url}\n\n"
                        "Depois de criar la, me diz o ID que eu configuro tudo!"
                    )
                return f"Erro ao criar Digital Twin: {e}"

        # Save a video (training or consent)
        if "cloudinary.com" not in video_url:
            return "URL do video deve ser do Cloudinary."

        if not video_type:
            # Try to auto-detect based on what's missing
            if not has_training:
                video_type = "training"
            elif not has_consent:
                video_type = "consent"
            else:
                return (
                    "Ambos os videos ja foram recebidos! "
                    "Chame setup_digital_twin(video_type='send') para enviar pro HeyGen."
                )

        if video_type == "training":
            with get_db() as session:
                av = session.get(UserAvatar, avatar_id_local)
                if av:
                    av.heygen_training_video_url = video_url
            _notify("Video de treinamento salvo!")

            # Check if consent already exists
            with get_db() as session:
                av = session.get(UserAvatar, avatar_id_local)
                has_consent_now = bool(av.heygen_consent_video_url) if av else False

            if has_consent_now:
                return (
                    "Video de treinamento recebido!\n"
                    "O video de consentimento ja estava salvo.\n\n"
                    "**Os dois videos estao prontos!** "
                    "Posso enviar pro HeyGen agora para iniciar o treinamento do seu Digital Twin?"
                )
            else:
                return (
                    "Video de treinamento recebido e salvo!\n\n"
                    "Agora falta o **video de consentimento**. "
                    "Grave um video curto (10-30s) dizendo:\n"
                    '"Eu, [seu nome], autorizo a criacao do meu avatar digital na plataforma HeyGen."\n\n'
                    "Me envia quando tiver!"
                )

        elif video_type == "consent":
            with get_db() as session:
                av = session.get(UserAvatar, avatar_id_local)
                if av:
                    av.heygen_consent_video_url = video_url
            _notify("Video de consentimento salvo!")

            # Check if training already exists
            with get_db() as session:
                av = session.get(UserAvatar, avatar_id_local)
                has_training_now = bool(av.heygen_training_video_url) if av else False

            if has_training_now:
                return (
                    "Video de consentimento recebido!\n"
                    "O video de treinamento ja estava salvo.\n\n"
                    "**Os dois videos estao prontos!** "
                    "Posso enviar pro HeyGen agora para iniciar o treinamento do seu Digital Twin?"
                )
            else:
                return (
                    "Video de consentimento recebido e salvo!\n\n"
                    "Agora falta o **video de treinamento** (2-5 min). "
                    "Grave voce falando naturalmente olhando pra camera, "
                    "com gestos e expressoes variadas.\n\n"
                    "Me envia quando tiver!"
                )

        return f"Tipo de video '{video_type}' nao reconhecido. Use 'training' ou 'consent'."

    def manage_digital_twin(
        action: str = "status",
        twin_id: str = "",
    ) -> str:
        """
        Gerencia o Digital Twin do avatar ativo.

        Args:
            action: Acao a realizar:
                - "status": Checa o status do treinamento no HeyGen.
                - "set": Vincula um Digital Twin ID manual ao avatar (verifica no HeyGen antes).
                - "remove": Remove o Digital Twin do avatar (volta pra photo avatar).
                - "info": Mostra info completa do avatar e twin.
                - "list_heygen": Lista todos os avatars no HeyGen (pra encontrar IDs de Digital Twins criados pelo site).
            twin_id: ID do Digital Twin (obrigatorio para action='set').

        Returns:
            Status ou confirmacao da acao.
        """
        from src.db.session import get_db
        from src.db.models import UserAvatar

        with get_db() as session:
            avatar = (
                session.query(UserAvatar)
                .filter(UserAvatar.user_id == user_id, UserAvatar.is_active == True)
                .first()
            )
            if not avatar:
                return "Nenhum avatar ativo. Configure um com setup_avatar primeiro."
            avatar_id_local = avatar.id
            current_twin_id = avatar.heygen_avatar_id
            current_type = avatar.heygen_avatar_type or "photo_avatar"
            current_status = avatar.heygen_training_status
            current_voice = avatar.heygen_voice_id
            label = avatar.label or "Meu Avatar"

        if action == "info":
            has_twin = current_type == "digital_twin"
            return (
                f"**Avatar: {label}**\n"
                f"ID local: {avatar_id_local[:8]}...\n"
                f"HeyGen Avatar ID: {current_twin_id or 'nao configurado'}\n"
                f"Tipo: {current_type}\n"
                f"Status treinamento: {current_status or 'n/a'}\n"
                f"Voz HeyGen: {'configurada' if current_voice else 'nao configurada'}\n"
                f"Digital Twin: {'sim' if has_twin else 'nao'}\n"
                f"Pode usar Seedance: {'sim' if has_twin and current_status == 'completed' else 'nao'}"
            )

        if action == "status":
            if not current_twin_id:
                return "Nenhum Digital Twin configurado. Use setup_digital_twin para criar."

            if current_type != "digital_twin":
                return (
                    f"O avatar atual usa photo avatar (ID: {current_twin_id[:12]}...). "
                    "Nao e um Digital Twin. Use setup_digital_twin para criar."
                )

            # Check status on HeyGen
            _notify("Verificando status do Digital Twin no HeyGen...")
            import asyncio as _aio_mgmt
            from src.video.providers.heygen import check_digital_twin_status

            try:
                async def _check():
                    return await check_digital_twin_status(current_twin_id)

                try:
                    loop = _aio_mgmt.get_event_loop()
                    if loop.is_running():
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            status_data = pool.submit(_aio_mgmt.run, _check()).result()
                    else:
                        status_data = _aio_mgmt.run(_check())
                except RuntimeError:
                    status_data = _aio_mgmt.run(_check())

                heygen_status = status_data.get("status", "unknown")

                # Update local status
                with get_db() as session:
                    av = session.get(UserAvatar, avatar_id_local)
                    if av and heygen_status in ("completed", "complete", "done"):
                        av.heygen_training_status = "completed"
                    elif av and heygen_status in ("failed", "error"):
                        av.heygen_training_status = "failed"

                if heygen_status in ("completed", "complete", "done"):
                    return (
                        f"Digital Twin PRONTO!\n"
                        f"ID: {current_twin_id[:12]}...\n"
                        f"Status: {heygen_status}\n\n"
                        "Digital Twin pronto! "
                        "Use generate_video para gerar videos."
                    )
                elif heygen_status in ("failed", "error"):
                    return (
                        f"Digital Twin FALHOU no treinamento.\n"
                        f"Status: {heygen_status}\n"
                        "Tente criar novamente com setup_digital_twin."
                    )
                else:
                    return (
                        f"Digital Twin ainda treinando...\n"
                        f"ID: {current_twin_id[:12]}...\n"
                        f"Status: {heygen_status}\n"
                        "Aguarde mais alguns minutos e cheque novamente."
                    )

            except Exception as e:
                return f"Erro ao checar status no HeyGen: {e}"

        if action == "set":
            if not twin_id:
                return "Informe o twin_id. Ex: manage_digital_twin(action='set', twin_id='abc123')"

            # Verify on HeyGen — check if avatar_id exists in the avatars list
            _notify(f"Verificando avatar {twin_id[:12]}... no HeyGen...")
            import asyncio as _aio_set
            from src.video.providers.heygen import list_avatars

            try:
                async def _verify():
                    all_avatars = await list_avatars()
                    for a in all_avatars:
                        if a.get("avatar_id") == twin_id:
                            return a
                    return None

                try:
                    loop = _aio_set.get_event_loop()
                    if loop.is_running():
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            found = pool.submit(_aio_set.run, _verify()).result()
                    else:
                        found = _aio_set.run(_verify())
                except RuntimeError:
                    found = _aio_set.run(_verify())

                if not found:
                    return f"Avatar ID '{twin_id}' nao encontrado no HeyGen. Verifique o ID."

                avatar_name_heygen = found.get("avatar_name", "?")

                with get_db() as session:
                    av = session.get(UserAvatar, avatar_id_local)
                    if av:
                        av.heygen_avatar_id = twin_id
                        av.heygen_avatar_type = "digital_twin"
                        av.heygen_training_status = "completed"

                return (
                    f"Avatar vinculado com sucesso!\n"
                    f"Nome no HeyGen: {avatar_name_heygen}\n"
                    f"ID: {twin_id}\n"
                    f"Avatar local: {label}\n\n"
                    "Pronto pra gerar videos!"
                )

            except Exception as e:
                logger.error("Failed to verify avatar on HeyGen: %s", e)
                return f"Erro ao verificar no HeyGen: {e}"

        if action == "remove":
            with get_db() as session:
                av = session.get(UserAvatar, avatar_id_local)
                if av:
                    old_id = av.heygen_avatar_id
                    # Keep heygen_group_id (photo avatar still works)
                    av.heygen_avatar_id = av.heygen_group_id or ""
                    av.heygen_avatar_type = "photo_avatar"
                    av.heygen_training_status = "completed" if av.heygen_group_id else None

            return (
                f"Digital Twin removido do avatar.\n"
                f"Twin ID removido: {old_id}\n"
                f"Avatar voltou pra modo photo avatar (HeyGen padrao).\n"
                "Para usar Seedance novamente, configure um novo Digital Twin."
            )

        if action == "list_heygen":
            _notify("Buscando avatars no HeyGen...")
            import asyncio as _aio_list
            from src.video.providers.heygen import list_avatars

            try:
                try:
                    loop = _aio_list.get_event_loop()
                    if loop.is_running():
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            all_avatars = pool.submit(_aio_list.run, list_avatars()).result()
                    else:
                        all_avatars = _aio_list.run(list_avatars())
                except RuntimeError:
                    all_avatars = _aio_list.run(list_avatars())

                if not all_avatars:
                    return "Nenhum avatar encontrado no HeyGen."

                lines = [f"**Avatars no HeyGen ({len(all_avatars)} total):**\n"]
                # Show video avatars (Digital Twins) first
                twins = [a for a in all_avatars if "video" in str(a.get("avatar_type", "")).lower() or "digital" in str(a.get("avatar_type", "")).lower()]
                photos = [a for a in all_avatars if a.get("avatar_type") == "talking_photo"]

                if twins:
                    lines.append("**Digital Twins:**")
                    for a in twins[:10]:
                        aid = a.get("avatar_id", a.get("id", "?"))
                        name = a.get("avatar_name", a.get("name", "sem nome"))
                        lines.append(f"- **{name}** | ID: `{aid}`")
                    lines.append("")

                if photos:
                    lines.append("**Photo Avatars (seus):**")
                    for a in photos[:5]:
                        aid = a.get("avatar_id", a.get("id", "?"))
                        name = a.get("avatar_name", a.get("name", "sem nome"))
                        lines.append(f"- {name} | ID: `{aid}`")
                    lines.append("")

                if not twins and not photos:
                    # Show first few of any type
                    lines.append("**Primeiros avatars:**")
                    for a in all_avatars[:10]:
                        aid = a.get("avatar_id", a.get("id", "?"))
                        name = a.get("avatar_name", a.get("name", "sem nome"))
                        atype = a.get("avatar_type", "?")
                        lines.append(f"- {name} ({atype}) | ID: `{aid}`")

                lines.append("\nPra vincular um Digital Twin, use: manage_digital_twin(action='set', twin_id='ID')")
                return "\n".join(lines)

            except Exception as e:
                return f"Erro ao listar avatars do HeyGen: {e}"

        return f"Acao '{action}' nao reconhecida. Use: status, set, remove, info, list_heygen."

    return (
        create_video_script,
        create_video_script_direct,
        generate_video,
        list_videos,
        list_video_templates,
        adjust_video,
        review_video,
        add_video_to_calendar,
        setup_avatar,
        edit_script,
        setup_heygen_voice,
        update_voice,
        setup_digital_twin,
        manage_digital_twin,
    )

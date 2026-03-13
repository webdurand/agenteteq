"""Agent tools for brand profile management."""

import json
import logging
import os

from src.models.branding import (
    create_brand_profile,
    update_brand_profile,
    list_brand_profiles,
    get_default_brand_profile,
    get_brand_profile_by_name,
)
from src.models.carousel_presets import (
    save_preset as db_save_preset,
    list_presets as db_list_presets,
    get_preset_by_name,
    delete_preset as db_delete_preset,
)

logger = logging.getLogger(__name__)


def create_branding_tools(user_id: str):
    """Factory that creates branding tools with user_id pre-injected."""

    def get_brand_profile(profile_name: str = "") -> str:
        """
        Busca o perfil de marca/identidade visual do usuario.

        Se profile_name for vazio, retorna o perfil padrao.
        Se profile_name for fornecido, busca pelo nome.

        Retorna as cores, fontes, logo, tom de voz e publico-alvo configurados.
        Use antes de gerar carrosseis para aplicar o branding do usuario.

        Args:
            profile_name: Nome do perfil de marca. Vazio = perfil padrao.

        Returns:
            Dados do branding ou mensagem se nao existir.
        """
        if profile_name:
            profile = get_brand_profile_by_name(user_id, profile_name)
            if not profile:
                return f"Perfil de marca '{profile_name}' nao encontrado. Use list para ver os disponiveis."
        else:
            profile = get_default_brand_profile(user_id)
            if not profile:
                return (
                    "O usuario ainda nao configurou uma identidade visual. "
                    "Sugira criar uma com update_brand_profile — pergunte: nome da marca, "
                    "cores principais, tipo de fonte (moderna, elegante, bold), e tom de voz."
                )

        lines = [
            f"Marca: {profile['name']}",
            f"Cores: primaria={profile['primary_color']}, accent={profile['accent_color']}, "
            f"fundo={profile['bg_color']}, texto={profile['text_primary_color']}, "
            f"texto2={profile['text_secondary_color']}",
            f"Fontes: titulos={profile['font_heading']}, corpo={profile['font_body']}",
        ]
        if profile.get("logo_url"):
            lines.append(f"Logo: {profile['logo_url']}")
        if profile.get("style_description"):
            lines.append(f"Estilo: {profile['style_description']}")
        if profile.get("tone_of_voice"):
            lines.append(f"Tom de voz: {profile['tone_of_voice']}")
        if profile.get("target_audience"):
            lines.append(f"Publico: {profile['target_audience']}")
        if profile.get("is_default"):
            lines.append("(perfil padrao)")

        return "\n".join(lines)

    def update_brand_profile_tool(
        name: str,
        primary_color: str = "",
        secondary_color: str = "",
        accent_color: str = "",
        bg_color: str = "",
        text_primary_color: str = "",
        text_secondary_color: str = "",
        font_heading: str = "",
        font_body: str = "",
        logo_url: str = "",
        style_description: str = "",
        tone_of_voice: str = "",
        target_audience: str = "",
        is_default: bool = True,
    ) -> str:
        """
        Cria ou atualiza o perfil de marca/identidade visual do usuario.

        Se ja existir um perfil com o mesmo nome, atualiza.
        Se nao existir, cria um novo.

        Use esta tool quando o usuario quiser configurar sua identidade visual:
        cores, fontes, logo, estilo, tom de voz, publico-alvo.

        Args:
            name: Nome da marca/perfil (ex: "Foto com Proposito", "Meu Estilo Escuro").
            primary_color: Cor primaria hex (ex: "#1B2A4A"). Usada como fundo de overlays.
            secondary_color: Cor secundaria hex.
            accent_color: Cor de destaque hex (ex: "#E8720C"). Usada em CTAs e destaques.
            bg_color: Cor de fundo hex.
            text_primary_color: Cor do texto principal hex (geralmente branco ou preto).
            text_secondary_color: Cor do texto secundario hex.
            font_heading: Fonte para titulos (ex: "Inter Bold", "Montserrat", "Playfair Display").
            font_body: Fonte para corpo (ex: "Inter", "Montserrat Light").
            logo_url: URL do logo (se o usuario enviar imagem, salve no Cloudinary primeiro).
            style_description: Descricao do estilo visual (ex: "Minimalista e clean, muito espaco negativo").
            tone_of_voice: Tom de comunicacao (ex: "Profissional e direto", "Casual e amigavel").
            target_audience: Publico-alvo (ex: "Fotografos iniciantes", "Empreendedores digitais").
            is_default: Se True, define como perfil padrao para geracoes automaticas.

        Returns:
            Confirmacao com resumo do branding salvo.
        """
        # Check if profile with this name already exists
        existing = get_brand_profile_by_name(user_id, name)

        if existing:
            # Update existing
            updates = {}
            if primary_color:
                updates["primary_color"] = primary_color
            if secondary_color:
                updates["secondary_color"] = secondary_color
            if accent_color:
                updates["accent_color"] = accent_color
            if bg_color:
                updates["bg_color"] = bg_color
            if text_primary_color:
                updates["text_primary_color"] = text_primary_color
            if text_secondary_color:
                updates["text_secondary_color"] = text_secondary_color
            if font_heading:
                updates["font_heading"] = font_heading
            if font_body:
                updates["font_body"] = font_body
            if logo_url:
                updates["logo_url"] = logo_url
            if style_description:
                updates["style_description"] = style_description
            if tone_of_voice:
                updates["tone_of_voice"] = tone_of_voice
            if target_audience:
                updates["target_audience"] = target_audience
            updates["is_default"] = is_default

            profile = update_brand_profile(existing["id"], user_id, **updates)
            if not profile:
                return "Erro ao atualizar perfil de marca."
            action = "atualizado"
        else:
            # Create new
            profile = create_brand_profile(
                user_id=user_id,
                name=name,
                is_default=is_default,
                primary_color=primary_color or "#1A1A2E",
                secondary_color=secondary_color or "#16213E",
                accent_color=accent_color or "#E94560",
                bg_color=bg_color or "#0F0F0F",
                text_primary_color=text_primary_color or "#FFFFFF",
                text_secondary_color=text_secondary_color or "#D0D0D0",
                font_heading=font_heading or "Inter Bold",
                font_body=font_body or "Inter",
                logo_url=logo_url,
                style_description=style_description,
                tone_of_voice=tone_of_voice,
                target_audience=target_audience,
            )
            action = "criado"

        return (
            f"Perfil de marca '{profile['name']}' {action}!\n"
            f"Cores: {profile['primary_color']} + {profile['accent_color']}\n"
            f"Fontes: {profile['font_heading']} / {profile['font_body']}\n"
            f"{'Padrao: sim' if profile['is_default'] else ''}"
        )

    def list_brand_profiles_tool() -> str:
        """
        Lista todos os perfis de marca/identidade visual do usuario.

        Returns:
            Lista dos perfis com nome e se e o padrao.
        """
        profiles = list_brand_profiles(user_id)
        if not profiles:
            return (
                "O usuario nao tem nenhum perfil de marca configurado. "
                "Sugira criar um — pergunte sobre nome da marca, cores, fontes e estilo."
            )

        lines = []
        for p in profiles:
            default_marker = " (padrao)" if p["is_default"] else ""
            lines.append(
                f"- {p['name']}{default_marker}: "
                f"{p['primary_color']} + {p['accent_color']} | "
                f"{p['font_heading']}"
            )
        return "\n".join(lines)

    def extract_branding_from_image(image_description: str) -> str:
        """
        Analisa a descricao visual de uma imagem enviada pelo usuario e
        sugere um branding baseado nas cores, estilo e tipografia identificados.

        Use quando o usuario enviar artes existentes e pedir para extrair
        o branding/identidade visual delas.

        IMPORTANTE: Antes de chamar esta tool, use a capacidade de visao do modelo
        para descrever a imagem em detalhes (cores dominantes, estilo, tipografia,
        composicao). Passe essa descricao aqui.

        Args:
            image_description: Descricao detalhada da imagem incluindo cores hex estimadas,
                              estilo visual, tipo de fonte observado, e composicao.

        Returns:
            Sugestao de branding baseada na analise, pronta para o usuario confirmar.
        """
        try:
            from google import genai

            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            if not api_key:
                return "Erro: API key do Gemini nao configurada."

            client = genai.Client(api_key=api_key)

            prompt = (
                "Voce e um designer grafico. Analise esta descricao de uma arte visual e extraia "
                "uma identidade de marca estruturada.\n\n"
                f"Descricao da arte:\n{image_description}\n\n"
                "Retorne APENAS um JSON (sem markdown) com:\n"
                '{"primary_color": "#hex", "secondary_color": "#hex", "accent_color": "#hex", '
                '"bg_color": "#hex", "text_primary_color": "#hex", "text_secondary_color": "#hex", '
                '"font_heading": "nome da fonte", "font_body": "nome da fonte", '
                '"style_description": "descricao do estilo em portugues"}'
            )

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[{"role": "user", "parts": [{"text": prompt}]}],
                config={"temperature": 0.3},
            )
            raw = response.text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                raw = raw.rsplit("```", 1)[0]

            data = json.loads(raw)

            lines = [
                "Identidade visual extraida da arte:",
                f"  Primaria: {data.get('primary_color', '?')}",
                f"  Secundaria: {data.get('secondary_color', '?')}",
                f"  Accent: {data.get('accent_color', '?')}",
                f"  Fundo: {data.get('bg_color', '?')}",
                f"  Texto: {data.get('text_primary_color', '?')} / {data.get('text_secondary_color', '?')}",
                f"  Fonte titulos: {data.get('font_heading', '?')}",
                f"  Fonte corpo: {data.get('font_body', '?')}",
                f"  Estilo: {data.get('style_description', '?')}",
                "",
                "Pergunte ao usuario se quer salvar como perfil de marca e qual nome dar.",
            ]
            return "\n".join(lines)
        except Exception as e:
            logger.error("Erro ao extrair branding de imagem: %s", e)
            return f"Erro ao analisar a imagem: {e}"

    def save_carousel_preset(
        name: str,
        style_anchor: str = "",
        primary_color: str = "",
        accent_color: str = "",
        text_primary_color: str = "",
        text_secondary_color: str = "",
        default_format: str = "1350x1080",
        default_slide_count: int = 5,
        sequential_slides: bool = True,
    ) -> str:
        """
        Salva um preset/template de estilo para carrosseis.
        Se ja existir um preset com o mesmo nome, atualiza.

        Use quando o usuario gostar de um estilo de carrossel e quiser reutilizar.
        Ex: 'salva esse estilo como Meu Estilo Escuro'.

        Args:
            name: Nome do preset (ex: "Meu Estilo Escuro", "Clean Minimal", "Bold Colorido").
            style_anchor: Descricao do estilo visual compartilhado entre slides
                         (ex: "Fundo escuro gradiente, tipografia moderna, espaco negativo generoso").
            primary_color: Cor de fundo principal hex (ex: "#1a1a1a").
            accent_color: Cor de destaque hex (ex: "#00d4ff").
            text_primary_color: Cor do texto principal hex (ex: "#ffffff").
            text_secondary_color: Cor do texto secundario hex (ex: "#cccccc").
            default_format: Formato padrao (ex: "1350x1080", "1080x1080", "1080x1920").
            default_slide_count: Numero padrao de slides (ex: 5).
            sequential_slides: Se True, slides sequenciais com coerencia visual.

        Returns:
            Confirmacao com resumo do preset salvo.
        """
        if not name.strip():
            return "Informe um nome para o preset."

        palette = {}
        if primary_color:
            palette["primary"] = primary_color
        if accent_color:
            palette["accent"] = accent_color
        if text_primary_color:
            palette["text_primary"] = text_primary_color
        if text_secondary_color:
            palette["text_secondary"] = text_secondary_color

        # Try to link to default brand profile if no explicit palette
        brand_id = None
        if not palette:
            brand = get_default_brand_profile(user_id)
            if brand:
                brand_id = brand["id"]
                palette = {
                    "primary": brand["bg_color"] or brand["primary_color"],
                    "accent": brand["accent_color"],
                    "text_primary": brand["text_primary_color"],
                    "text_secondary": brand["text_secondary_color"],
                }

        preset = db_save_preset(
            user_id=user_id,
            name=name.strip(),
            style_anchor=style_anchor,
            color_palette=palette,
            default_format=default_format,
            default_slide_count=default_slide_count,
            sequential_slides=sequential_slides,
            brand_profile_id=brand_id,
        )

        parts = [f"Preset '{preset['name']}' salvo!"]
        if preset["color_palette"]:
            p = preset["color_palette"]
            parts.append(f"Paleta: fundo {p.get('primary', '?')}, accent {p.get('accent', '?')}")
        if preset.get("style_anchor"):
            parts.append(f"Estilo: {preset['style_anchor'][:80]}")
        parts.append(f"Formato: {preset['default_format']}, {preset['default_slide_count']} slides")
        parts.append("Proxima vez e so falar 'usa meu preset X'.")

        return "\n".join(parts)

    def list_carousel_presets() -> str:
        """
        Lista todos os presets/templates de carrossel salvos pelo usuario.

        Returns:
            Lista dos presets com nome, cores e formato.
        """
        presets = db_list_presets(user_id)
        if not presets:
            return (
                "O usuario nao tem nenhum preset de carrossel salvo. "
                "Apos gerar um carrossel que o usuario gostar, ofereca salvar o estilo como preset."
            )

        lines = []
        for p in presets:
            palette = p.get("color_palette", {})
            colors = ""
            if palette:
                colors = f" | {palette.get('primary', '?')} + {palette.get('accent', '?')}"
            lines.append(
                f"- {p['name']}{colors} | {p['default_format']}, {p['default_slide_count']} slides"
            )
        return "\n".join(lines)

    return (
        get_brand_profile, update_brand_profile_tool, list_brand_profiles_tool,
        extract_branding_from_image, save_carousel_preset, list_carousel_presets,
    )

"""
Content Strategist — refina conteúdo antes da geração visual.

Usa Gemini Flash para otimizar copy, hooks, CTAs e prompts visuais
com visão de marketing digital. Mostra o resultado ao usuário para
aprovação antes de prosseguir com a geração.
"""

import json
import os
import logging
from typing import Any

logger = logging.getLogger(__name__)

_STRATEGIST_SYSTEM_PROMPT = """
Você é um estrategista de conteúdo digital e diretor de arte de alto nível.
Recebe um briefing de conteúdo visual + contexto coletado e otimiza para máximo impacto.

PARA CAROUSEL:
- Slide 1 (role='capa'): HOOK que para o scroll. Pergunta provocativa, dado chocante ou promessa irresistível.
  O titulo deve ser curto (max 8 palavras), impactante e gerar curiosidade.
- Slides do meio (role='conteudo'): 1 ideia por slide. Titulo claro + body explicativo.
  Progressão lógica: cada slide complementa o anterior.
- Último slide (role='fechamento'): CTA direto e acionável.
  Exemplos: "Salve pra consultar depois", "Comente qual foi sua favorita", "Manda pra alguém que precisa ver isso".
- Max 15 palavras por título, max 40 por body.
- Tom alinhado com brand profile quando fornecido.
- Se referência visual fornecida: respeitar o estilo identificado.

PARA IMAGEM IA (single ou batch):
- Gere prompts visuais ultra-detalhados para cada imagem:
  • Composição (framing, ângulo, rule of thirds, leading lines)
  • Iluminação (tipo, direção, temperatura de cor)
  • Estilo artístico (fotorealista, ilustração, 3D, etc)
  • Paleta de cores (baseada no brand ou referência)
  • Ambiente/contexto (cenário, texturas, elementos de cena)
  • Mood/atmosfera (profissional, acolhedor, futurista, energético)
- Se referência visual fornecida: extrair e incorporar estilo similar
- Se batch (N imagens): gerar variações inteligentes
  (ângulos diferentes, iluminações alternativas, composições variadas)
- TEXTO NAS IMAGENS IA:
  • Se generation_mode='ai' no contexto: o texto será renderizado PELA IA diretamente na imagem.
    Inclua os textos (title, body, cta_text) no prompt visual e instrua tipografia profissional,
    hierarquia visual clara, fontes modernas e legíveis, integradas ao design.
  • Caso contrário: NUNCA inclua texto/tipografia/letras no prompt visual. As imagens devem ser fundos limpos.

REGRAS DE OUTPUT:
- Responda SOMENTE com JSON válido, sem markdown, sem explicação.
- O JSON deve ter exatamente esta estrutura:
{
  "slides": [
    {
      "slide_number": 1,
      "role": "capa|conteudo|fechamento",
      "prompt": "descrição visual detalhada da imagem",
      "title": "titulo do slide (se carousel)",
      "body": "texto complementar (se carousel)",
      "cta_text": "texto do CTA (apenas no fechamento)",
      "style": "estilo artístico"
    }
  ],
  "style_anchor": "identidade visual compartilhada detalhada",
  "color_palette": {
    "primary": "#hex",
    "accent": "#hex",
    "text_primary": "#hex",
    "text_secondary": "#hex"
  },
  "changes_summary": "resumo em PT-BR do que foi refinado, max 3 frases",
  "recommended_flow": "ai|html"
}
"""


async def refine_content(
    request_type: str,
    description: str,
    slides: list[dict[str, Any]] | None = None,
    brand_profile: dict[str, Any] | None = None,
    reference_analysis: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    count: int = 1,
) -> dict[str, Any]:
    """
    Refina conteúdo visual usando Gemini Flash.

    Args:
        request_type: "single_image", "batch_images" ou "carousel"
        description: O que o usuário pediu
        slides: Lista de slides pré-definidos (opcional, para carousel)
        brand_profile: BrandProfile do usuário (cores, fontes, tom)
        reference_analysis: Análise de referência visual já feita
        context: Contexto extra coletado {purpose, style, platform, audience}
        count: Quantas imagens gerar (para batch)

    Returns:
        {
            "slides": [...refined slides...],
            "style_anchor": "...",
            "color_palette": {...},
            "changes_summary": "Resumo do que mudou",
            "recommended_flow": "ai" | "html"
        }
    """
    from google import genai

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        logger.warning("Content Strategist: sem API key, retornando conteúdo sem refinamento")
        return _passthrough(request_type, description, slides, count)

    client = genai.Client(api_key=api_key)

    user_prompt = _build_user_prompt(
        request_type, description, slides, brand_profile,
        reference_analysis, context, count,
    )

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[{"role": "user", "parts": [{"text": user_prompt}]}],
            config={
                "system_instruction": _STRATEGIST_SYSTEM_PROMPT,
                "temperature": 0.6,
            },
        )
        raw = response.text.strip()

        # Limpa markdown wrapping se houver
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit("```", 1)[0]

        result = json.loads(raw)

        # Validação básica
        if "slides" not in result or not isinstance(result["slides"], list):
            logger.warning("Content Strategist: resposta sem slides válidos, usando passthrough")
            return _passthrough(request_type, description, slides, count)

        # Garante campos obrigatórios em cada slide
        for i, slide in enumerate(result["slides"]):
            slide.setdefault("slide_number", i + 1)
            slide.setdefault("role", "conteudo")
            slide.setdefault("prompt", description)
            slide.setdefault("style", "")
            slide.setdefault("title", "")
            slide.setdefault("body", "")
            slide.setdefault("cta_text", "")

        # Injeta style_anchor e color_palette nos slides
        style_anchor = result.get("style_anchor", "")
        color_palette = result.get("color_palette", {})
        for slide in result["slides"]:
            if style_anchor and not slide.get("style_anchor"):
                slide["style_anchor"] = style_anchor
            if color_palette and not slide.get("color_palette"):
                slide["color_palette"] = color_palette

        result.setdefault("changes_summary", "Conteúdo refinado pelo Content Strategist.")
        result.setdefault("recommended_flow", "html" if request_type == "carousel" else "ai")

        logger.info(
            "Content Strategist: %s slides refinados | flow=%s | summary=%s",
            len(result["slides"]),
            result["recommended_flow"],
            result["changes_summary"][:80],
        )
        return result

    except Exception as e:
        logger.warning("Content Strategist falhou, usando passthrough: %s", e)
        return _passthrough(request_type, description, slides, count)


def _build_user_prompt(
    request_type: str,
    description: str,
    slides: list[dict] | None,
    brand_profile: dict | None,
    reference_analysis: dict | None,
    context: dict | None,
    count: int,
) -> str:
    """Monta o prompt do usuário para o Strategist."""
    parts = [f"TIPO DE PEDIDO: {request_type}"]
    parts.append(f"DESCRIÇÃO: {description}")

    if count > 1:
        parts.append(f"QUANTIDADE: {count} imagens")

    if slides:
        parts.append(f"SLIDES PRÉ-DEFINIDOS:\n{json.dumps(slides, ensure_ascii=False, indent=2)}")

    if brand_profile:
        brand_info = {
            "cores": {
                "primary": brand_profile.get("primary_color"),
                "secondary": brand_profile.get("secondary_color"),
                "accent": brand_profile.get("accent_color"),
                "bg": brand_profile.get("bg_color"),
                "text_primary": brand_profile.get("text_primary_color"),
                "text_secondary": brand_profile.get("text_secondary_color"),
            },
            "fontes": {
                "heading": brand_profile.get("font_heading"),
                "body": brand_profile.get("font_body"),
            },
            "tom_de_voz": brand_profile.get("tone_of_voice"),
            "publico_alvo": brand_profile.get("target_audience"),
            "estilo": brand_profile.get("style_description"),
        }
        parts.append(f"BRAND PROFILE:\n{json.dumps(brand_info, ensure_ascii=False, indent=2)}")

    if reference_analysis:
        parts.append(f"ANÁLISE DA REFERÊNCIA VISUAL:\n{json.dumps(reference_analysis, ensure_ascii=False, indent=2)}")

    if context:
        parts.append(f"CONTEXTO ADICIONAL:\n{json.dumps(context, ensure_ascii=False, indent=2)}")

    return "\n\n".join(parts)


def _passthrough(
    request_type: str,
    description: str,
    slides: list[dict] | None,
    count: int,
) -> dict[str, Any]:
    """Retorna conteúdo sem refinamento quando o Strategist não está disponível."""
    if slides:
        for i, s in enumerate(slides):
            s.setdefault("slide_number", i + 1)
            s.setdefault("role", "conteudo")
            s.setdefault("prompt", description)
        return {
            "slides": slides,
            "style_anchor": "",
            "color_palette": {},
            "changes_summary": "",
            "recommended_flow": "html" if request_type == "carousel" else "ai",
        }

    generated_slides = [
        {
            "slide_number": i + 1,
            "role": "conteudo",
            "prompt": description,
            "title": "",
            "body": "",
            "cta_text": "",
            "style": "",
        }
        for i in range(count)
    ]
    return {
        "slides": generated_slides,
        "style_anchor": "",
        "color_palette": {},
        "changes_summary": "",
        "recommended_flow": "ai",
    }

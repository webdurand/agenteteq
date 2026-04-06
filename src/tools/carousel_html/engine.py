"""
Carousel HTML Engine — gera carrosséis com HTML/CSS via LLM + Playwright.

Em vez de templates pré-prontos, o LLM gera HTML/CSS do zero baseado em:
- Referência visual do usuário (screenshot/link)
- Brand profile (cores, fontes, logo)
- Conteúdo dos slides (refinado pelo Content Strategist)
"""

import base64
import json
import os
import logging
from typing import Any, Optional

from .renderer import PlaywrightRenderer

logger = logging.getLogger(__name__)

# ──────────────────────────── Prompts ────────────────────────────

_ANALYZE_REFERENCE_PROMPT = """
Analise esta referência visual de carrossel/post de redes sociais.
Extraia DETALHADAMENTE:

1. CORES: paleta exata (hex) — background, texto principal, texto secundário, cor de destaque
2. LAYOUT: estrutura (centralizado, grid, split, etc), espaçamento, margens
3. TIPOGRAFIA: estilo das fontes (serif, sans-serif, display, mono), pesos, tamanhos relativos
4. ELEMENTOS: quais elementos visuais estão presentes (numeração de slide, logo, divisores, ícones, formas decorativas)
5. ESTILO GERAL: mood, tom visual, se é minimalista/bold/elegante/tech

Responda SOMENTE com JSON válido:
{
    "style_description": "descrição textual completa do estilo para replicação",
    "colors": {
        "bg": "#hex",
        "text_primary": "#hex",
        "text_secondary": "#hex",
        "accent": "#hex"
    },
    "layout": "centered|grid|split|fullbleed",
    "font_style": "serif|sans-serif|display|mono",
    "font_weight_heading": "bold|black|medium",
    "spacing": "tight|normal|generous",
    "elements": ["slide_number", "logo", "divider", "icon", "shape", ...],
    "mood": "minimalista|bold|elegante|tech|playful|corporativo"
}
"""

_GENERATE_SLIDE_HTML_PROMPT = """
Você é um designer web de elite, especialista em criar slides VISUALMENTE RICOS e IMPRESSIONANTES para redes sociais.
Seus designs ganham prêmios. Cada slide deve parecer feito por um estúdio de design premium.

{style_section}

DADOS DO SLIDE:
- Role: {role} ({role_description})
- Título: {title}
- Body: {body}
- CTA: {cta_text}
- Slide {slide_number} de {total}

{brand_section}

FORMATO: {width}x{height}px

RECURSOS VISUAIS (use PELO MENOS 4 por slide — seja CRIATIVO e OUSADO):
- Gradientes complexos (linear-gradient, radial-gradient, conic-gradient)
- Glassmorphism (backdrop-filter: blur + background rgba semi-transparente + border sutil)
- Formas decorativas com CSS (círculos, blobs, linhas) usando ::before/::after
- Sombras sofisticadas (box-shadow multi-camada, text-shadow sutil)
- Badges/tags com border-radius e background contrastante
- Numeração de slide estilizada (grande, semi-transparente, decorativa)
- Barras coloridas, dots decorativos, linhas de destaque
- Ícones inline via SVG relevantes ao conteúdo — CRIE os SVGs inline
- Efeitos de glow (box-shadow com cor vibrante e blur grande)
- Tipografia expressiva: mix de pesos (300, 400, 700, 900), tamanhos variados
- Cards internos com bordas arredondadas e sombra
- Destaque de palavras-chave com <span> — use background highlight (badge) com texto branco

═══ REGRA DE OURO: CONTRASTE ═══
PROIBIDO texto escuro (azul, roxo, cinza escuro) sobre fundo escuro.
- Títulos: SEMPRE color #ffffff (branco puro)
- Body text: SEMPRE color #d0d0e0 ou mais claro
- Cor accent/destaque: use APENAS em bordas, botões, ícones, glow de fundo, ou como background de badges (com texto branco por cima)
- Para destacar palavras: <span style="background:COR_ACCENT;color:#fff;padding:2px 8px;border-radius:4px">palavra</span>
- NUNCA: <span style="color:azul_escuro">texto</span> sobre fundo escuro

ROLES:
- capa: Título ENORME e cinematográfico (64-80px). Visual que PARA o scroll. Gradientes dramáticos, tipografia bold massiva, elementos decorativos que criam profundidade.
- conteudo: Layout rico com cards, separadores, ícones SVG inline. Título bold + body bem formatado.
- fechamento: CTA em destaque máximo — botão grande com gradiente e sombra glow.

REGRAS TÉCNICAS:
1. HTML completo (<!DOCTYPE html> até </html>), auto-contido com CSS em <style>
2. body: width:{width}px, height:{height}px EXATOS, overflow:hidden, margin:0
3. Google Fonts via @import (fontes impactantes: Inter, Outfit, Space Grotesk, Sora, Plus Jakarta Sans)
4. Safe margins: mínimo 60px das bordas
5. NÃO use JavaScript

Output: APENAS o HTML completo, sem markdown (```), sem explicação.
"""


class CarouselHTMLEngine:
    """
    Engine que gera carrosséis via LLM (HTML/CSS) + Playwright (rendering).
    """

    def __init__(self):
        self.renderer = PlaywrightRenderer()

    async def analyze_reference(
        self,
        image_bytes: Optional[bytes] = None,
        image_url: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Analisa referência visual (screenshot/imagem) usando Gemini Flash com visão.

        Args:
            image_bytes: Bytes da imagem de referência
            image_url: URL da imagem (será baixada se image_bytes não fornecido)

        Returns:
            Dicionário com análise de estilo (cores, layout, tipografia, etc)
        """
        from google import genai
        from google.genai.types import Part

        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            logger.warning("analyze_reference: sem API key")
            return _default_style()

        # Baixa imagem da URL se necessário
        if image_bytes is None and image_url:
            image_bytes = await _download_image(image_url)
            if image_bytes is None:
                return _default_style()

        if image_bytes is None:
            return _default_style()

        client = genai.Client(api_key=api_key)

        try:
            image_part = Part.from_bytes(data=image_bytes, mime_type="image/png")
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[image_part, _ANALYZE_REFERENCE_PROMPT],
                config={"temperature": 0.3},
            )

            raw = response.text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                raw = raw.rsplit("```", 1)[0]

            result = json.loads(raw)
            logger.info("Referência analisada: mood=%s, layout=%s", result.get("mood"), result.get("layout"))
            return result

        except Exception as e:
            logger.warning("Erro ao analisar referência: %s", e)
            return _default_style()

    async def generate_slide_html(
        self,
        slide: dict[str, Any],
        style_analysis: dict[str, Any],
        brand: Optional[dict[str, Any]] = None,
        width: int = 1080,
        height: int = 1080,
    ) -> str:
        """
        Gera HTML/CSS completo de UM slide usando LLM.

        Args:
            slide: {role, title, body, cta_text, slide_number, total}
            style_analysis: Resultado de analyze_reference
            brand: BrandProfile dict (override cores/fontes se existir)
            width: Largura do slide
            height: Altura do slide

        Returns:
            HTML string completa
        """
        from google import genai

        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY não configurada")

        client = genai.Client(api_key=api_key)

        role = slide.get("role", "conteudo")
        role_descriptions = {
            "capa": "Slide de abertura — hook visual forte, título impactante",
            "conteudo": "Slide de conteúdo — informação de valor, layout legível",
            "fechamento": "Slide final — call-to-action destacado",
        }

        # Build style section
        style_section = _build_style_section(style_analysis)

        # Build brand section
        brand_section = _build_brand_section(brand)

        prompt = _GENERATE_SLIDE_HTML_PROMPT.format(
            style_section=style_section,
            role=role,
            role_description=role_descriptions.get(role, "Slide de conteúdo"),
            title=slide.get("title", ""),
            body=slide.get("body", ""),
            cta_text=slide.get("cta_text", ""),
            slide_number=slide.get("slide_number", 1),
            total=slide.get("total", 1),
            brand_section=brand_section,
            width=width,
            height=height,
        )

        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[{"role": "user", "parts": [{"text": prompt}]}],
                config={"temperature": 0.4},
            )

            html = response.text.strip()

            # Remove markdown wrapping se houver
            if html.startswith("```"):
                html = html.split("\n", 1)[1] if "\n" in html else html[3:]
                html = html.rsplit("```", 1)[0].strip()

            # Valida que é HTML minimamente
            if not html.startswith("<!DOCTYPE") and not html.startswith("<html"):
                # Tenta encontrar o HTML dentro do texto
                idx = html.find("<!DOCTYPE")
                if idx == -1:
                    idx = html.find("<html")
                if idx >= 0:
                    html = html[idx:]
                else:
                    logger.warning("LLM não gerou HTML válido para slide %s", slide.get("slide_number"))
                    html = _fallback_slide_html(slide, brand, width, height)

            return html

        except Exception as e:
            logger.error("Erro ao gerar HTML do slide %s: %s", slide.get("slide_number"), e)
            return _fallback_slide_html(slide, brand, width, height)

    async def generate_carousel(
        self,
        slides: list[dict[str, Any]],
        reference_image: Optional[bytes] = None,
        reference_url: Optional[str] = None,
        brand: Optional[dict[str, Any]] = None,
        format: str = "1080x1080",
    ) -> list[bytes]:
        """
        Pipeline completo: análise de referência → geração HTML → renderização PNG.

        Args:
            slides: Lista de slides [{role, title, body, cta_text}]
            reference_image: Bytes da imagem de referência
            reference_url: URL da referência (alternativa a bytes)
            brand: BrandProfile dict
            format: "1080x1080", "1080x1350", "1080x1920"

        Returns:
            Lista de PNG bytes renderizados
        """
        # 1. Parse formato
        width, height = _parse_format(format)

        # 2. Analisa referência visual
        style = await self.analyze_reference(reference_image, reference_url)

        # 3. Gera HTML para TODOS os slides em paralelo (muito mais rápido)
        import asyncio

        async def _gen_slide(i, slide):
            slide_data = {**slide, "slide_number": i + 1, "total": len(slides)}
            html = await self.generate_slide_html(slide_data, style, brand, width, height)
            logger.info("HTML gerado para slide %d/%d", i + 1, len(slides))
            return i, html

        results = await asyncio.gather(*[_gen_slide(i, s) for i, s in enumerate(slides)])
        # Reordena pelos índices (gather mantém ordem, mas por segurança)
        results.sort(key=lambda x: x[0])
        html_slides = [html for _, html in results]

        # 4. Renderiza via Playwright (sequencial — reutiliza browser)
        png_slides = await self.renderer.render_carousel(html_slides, width, height)

        logger.info("Carrossel completo: %d slides renderizados", len(png_slides))
        return png_slides

    async def generate_preview(
        self,
        slide: dict[str, Any],
        style_analysis: dict[str, Any],
        brand: Optional[dict[str, Any]] = None,
        format: str = "1080x1080",
    ) -> bytes:
        """
        Gera preview de um único slide (usado antes da aprovação do usuário).
        """
        width, height = _parse_format(format)
        slide_data = {**slide, "slide_number": 1, "total": 1}
        html = await self.generate_slide_html(slide_data, style_analysis, brand, width, height)
        return await self.renderer.render_preview(html, width, height)

    async def close(self):
        """Fecha o renderer."""
        await self.renderer.close()


# ──────────────────────────── Helpers ────────────────────────────

def _build_style_section(style_analysis: dict[str, Any]) -> str:
    """Constrói seção de estilo para o prompt."""
    if not style_analysis or not style_analysis.get("style_description"):
        return (
            "ESTILO: Design premium e sofisticado. Fundo escuro com gradientes sutis. "
            "Use glassmorphism, formas decorativas, glow effects, ícones SVG inline. "
            "Tipografia expressiva com mix de pesos. Crie profundidade visual com camadas. "
            "Seja CRIATIVO e OUSADO — cada slide pode ter personalidade própria."
        )

    parts = ["REFERÊNCIA VISUAL ANALISADA:"]
    parts.append(f"Descrição: {style_analysis.get('style_description', '')}")

    colors = style_analysis.get("colors", {})
    if colors:
        parts.append(f"Cores: bg={colors.get('bg')}, texto={colors.get('text_primary')}, "
                      f"secundário={colors.get('text_secondary')}, destaque={colors.get('accent')}")

    parts.append(f"Layout: {style_analysis.get('layout', 'centered')}")
    parts.append(f"Tipografia: {style_analysis.get('font_style', 'sans-serif')} "
                 f"(heading: {style_analysis.get('font_weight_heading', 'bold')})")
    parts.append(f"Mood: {style_analysis.get('mood', 'moderno')}")

    elements = style_analysis.get("elements", [])
    if elements:
        parts.append(f"Elementos: {', '.join(elements)}")

    parts.append("\nREPLIQUE este estilo visual. Use cores, espaçamentos e tipografia similares.")
    return "\n".join(parts)


def _build_brand_section(brand: Optional[dict[str, Any]]) -> str:
    """Constrói seção de brand para o prompt."""
    if not brand:
        return "BRAND: Nenhum brand profile definido. Use suas melhores decisões de design."

    parts = ["BRAND PROFILE (TEM PRIORIDADE sobre a referência):"]
    parts.append(f"Cores: primary={brand.get('primary_color')}, accent={brand.get('accent_color')}, "
                 f"bg={brand.get('bg_color')}, text={brand.get('text_primary_color')}")

    heading = brand.get("font_heading", "")
    body = brand.get("font_body", "")
    if heading:
        parts.append(f"Fonte heading: {heading}")
    if body:
        parts.append(f"Fonte body: {body}")

    logo = brand.get("logo_url")
    if logo:
        parts.append(f"Logo URL: {logo} (incluir como <img> pequeno no canto inferior)")

    return "\n".join(parts)


def _default_style() -> dict[str, Any]:
    """Estilo padrão quando não há referência."""
    return {
        "style_description": (
            "Design premium e sofisticado. Fundo escuro com gradientes sutis (do preto para tons de azul/roxo muito escuro). "
            "Tipografia sans-serif moderna e bold (Space Grotesk ou Outfit). Elementos decorativos: "
            "formas geométricas semi-transparentes, linhas de accent em gradiente, glassmorphism em cards, "
            "glow effects sutis na cor de destaque. Numeração de slide grande e semi-transparente como elemento decorativo. "
            "Badges com border-radius para categorias. Ícones SVG inline para complementar o conteúdo."
        ),
        "colors": {
            "bg": "#0a0a0f",
            "text_primary": "#FFFFFF",
            "text_secondary": "#a0a0b8",
            "accent": "#7c5cfc",
        },
        "layout": "centered",
        "font_style": "sans-serif",
        "font_weight_heading": "900",
        "spacing": "generous",
        "elements": ["slide_number", "decorative_shapes", "gradient_accents", "glow_effects"],
        "mood": "premium tech",
    }


def _parse_format(format: str) -> tuple[int, int]:
    """Converte string de formato para (width, height)."""
    format_map = {
        "1080x1080": (1080, 1080),
        "1080x1350": (1080, 1350),
        "1080x1920": (1080, 1920),
        "1350x1080": (1350, 1080),
    }
    normalized = format.strip().lower().replace(" ", "")
    return format_map.get(normalized, (1080, 1080))


def _fallback_slide_html(
    slide: dict[str, Any],
    brand: Optional[dict[str, Any]],
    width: int,
    height: int,
) -> str:
    """HTML fallback simples quando o LLM falha."""
    bg = "#0F0F0F"
    text_color = "#FFFFFF"
    accent = "#6C63FF"
    font = "Inter"

    if brand:
        bg = brand.get("bg_color", bg)
        text_color = brand.get("text_primary_color", text_color)
        accent = brand.get("accent_color", accent)
        font = brand.get("font_heading", font)

    title = slide.get("title", "")
    body = slide.get("body", "")
    cta = slide.get("cta_text", "")

    title_html = f'<h1 style="font-size:48px;font-weight:700;margin-bottom:24px;">{title}</h1>' if title else ""
    body_html = f'<p style="font-size:22px;line-height:1.6;color:#B0B0B0;max-width:85%;">{body}</p>' if body else ""
    cta_html = (
        f'<div style="margin-top:40px;padding:16px 40px;background:{accent};'
        f'color:white;font-weight:600;font-size:20px;border-radius:8px;display:inline-block;">{cta}</div>'
    ) if cta else ""

    return f"""<!DOCTYPE html>
<html>
<head>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700;800&display=swap');
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    width:{width}px; height:{height}px; overflow:hidden;
    background: linear-gradient(160deg, {bg} 0%, #1a1a2e 50%, {bg} 100%);
    color:{text_color};
    font-family:'Space Grotesk',sans-serif;
    display:flex; flex-direction:column;
    justify-content:center; align-items:center;
    padding:80px; text-align:center;
    position:relative;
  }}
  body::before {{
    content:''; position:absolute; top:-20%; right:-10%;
    width:400px; height:400px; border-radius:50%;
    background: radial-gradient(circle, {accent}22 0%, transparent 70%);
    pointer-events:none;
  }}
  h1 {{ font-size:56px; font-weight:800; line-height:1.15; margin-bottom:24px; position:relative; }}
  p {{ font-size:22px; line-height:1.6; color:#a0a0b8; max-width:85%; position:relative; }}
  .cta {{ margin-top:40px; padding:18px 44px; background:linear-gradient(135deg, {accent}, {accent}cc);
    color:white; font-weight:700; font-size:20px; border-radius:12px; display:inline-block;
    box-shadow: 0 4px 24px {accent}44; position:relative; }}
</style>
</head>
<body>
  {title_html}
  {body_html}
  {cta_html.replace('class="cta-button"', 'class="cta"') if cta_html else ''}
</body>
</html>"""


async def _download_image(url: str) -> Optional[bytes]:
    """Baixa imagem de URL."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
    except Exception as e:
        logger.warning("Erro ao baixar imagem %s: %s", url[:80], e)
        return None

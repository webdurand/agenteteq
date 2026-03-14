"""
Competitive report generator — decoupled architecture.

Flow:
  collect_report_data(user_id, usernames, platforms) → ReportData (dict)
  render_report_slides(report_data)                  → list[bytes] (PNG images)
  upload + deliver                                   → Cloudinary URLs

Swap the renderer in the future (PDF, single image, etc.) without touching data collection.
"""

import io
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Slide dimensions: 1080x1350 (4:5 ratio, Instagram-friendly)
W, H = 1080, 1350

# Color palette (dark theme)
BG = (15, 15, 20)
BG_CARD = (25, 25, 35)
ACCENT = (99, 102, 241)  # indigo
ACCENT2 = (139, 92, 246)  # purple
TEXT_PRIMARY = (240, 240, 245)
TEXT_SECONDARY = (160, 160, 175)
TEXT_MUTED = (100, 100, 115)
CHART_COLORS = [
    (99, 102, 241),
    (139, 92, 246),
    (236, 72, 153),
    (245, 158, 11),
    (16, 185, 129),
    (59, 130, 246),
]


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load a font, falling back to default if not available."""
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _format_number(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


# ──────────────── Step 1: Collect Data ────────────────


def collect_report_data(
    user_id: str,
    usernames: list[str],
    platforms: list[str] | None = None,
) -> dict:
    """Collect data for competitive report from tracked accounts."""
    from src.models.social import (
        get_tracked_account_by_username,
        get_top_content,
        get_recent_content,
        get_account_snapshots,
        get_growth_summary,
        get_avg_engagement,
    )

    if not platforms:
        platforms = ["instagram"]

    accounts_data = []

    for username in usernames:
        username_clean = username.lstrip("@").lower().strip()
        account = None

        for platform in platforms:
            account = get_tracked_account_by_username(user_id, platform, username_clean)
            if account:
                break

        if not account:
            continue

        top_posts = get_top_content(account["id"], sort_by="likes_count", limit=10)
        recent_posts = get_recent_content(account["id"], limit=20)

        # Calculate avg engagement
        avg_likes = 0
        avg_comments = 0
        if recent_posts:
            avg_likes = sum(p.get("likes_count", 0) for p in recent_posts) // len(recent_posts)
            avg_comments = sum(p.get("comments_count", 0) for p in recent_posts) // len(recent_posts)

        followers = account.get("followers_count", 0)
        engagement_rate = (avg_likes + avg_comments) / followers * 100 if followers > 0 else 0.0

        top_post = top_posts[0] if top_posts else {}

        growth = get_growth_summary(account["id"], days=30)

        accounts_data.append({
            "username": account["username"],
            "platform": account["platform"],
            "display_name": account.get("display_name", ""),
            "followers": followers,
            "posts": account.get("posts_count", 0),
            "avg_likes": avg_likes,
            "avg_comments": avg_comments,
            "engagement_rate": round(engagement_rate, 2),
            "top_post_caption": (top_post.get("caption", "") or "")[:150],
            "top_post_likes": top_post.get("likes_count", 0),
            "growth_pct": growth.get("pct", 0.0),
            "growth_delta": growth.get("followers_delta", 0),
        })

    if not accounts_data:
        return {}

    # Build comparison rankings
    followers_ranking = sorted(accounts_data, key=lambda a: a["followers"], reverse=True)
    engagement_ranking = sorted(accounts_data, key=lambda a: a["engagement_rate"], reverse=True)
    growth_ranking = sorted(accounts_data, key=lambda a: a["growth_pct"], reverse=True)

    return {
        "title": "Relatorio Competitivo",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "accounts": accounts_data,
        "comparison": {
            "followers_ranking": [(a["username"], a["followers"]) for a in followers_ranking],
            "engagement_ranking": [(a["username"], a["engagement_rate"]) for a in engagement_ranking],
            "growth_ranking": [(a["username"], f"+{a['growth_pct']}%") for a in growth_ranking],
        },
    }


# ──────────────── Step 2: Generate Insights (LLM) ────────────────


def generate_insights(report_data: dict) -> str:
    """Generate comparative insights using Gemini Flash."""
    try:
        from agno.agent import Agent
        from agno.models.google import Gemini

        accounts_summary = []
        for a in report_data.get("accounts", []):
            accounts_summary.append(
                f"@{a['username']} ({a['platform']}): "
                f"{_format_number(a['followers'])} seguidores, "
                f"eng. rate {a['engagement_rate']}%, "
                f"crescimento 30d: +{a['growth_pct']}%, "
                f"media {_format_number(a['avg_likes'])} likes/post"
            )

        prompt = (
            "Analise comparativa dos seguintes perfis de redes sociais:\n\n"
            + "\n".join(accounts_summary)
            + "\n\nGere 3-4 insights curtos e acionaveis comparando os perfis. "
            "Foque em: quem esta crescendo mais rapido, quem tem melhor engajamento, "
            "o que o usuario pode aprender de cada perfil. "
            "Responda em portugues, formato bullet points, maximo 4 linhas por insight."
        )

        agent = Agent(
            model=Gemini(id="gemini-2.5-flash"),
            description="Analista de redes sociais.",
        )
        result = agent.run(prompt)
        return result.content if hasattr(result, "content") else str(result)
    except Exception as e:
        logger.error("Erro ao gerar insights: %s", e)
        return "Insights indisponiveis no momento."


# ──────────────── Step 3: Render Slides ────────────────


def render_report_slides(report_data: dict, insights: str = "") -> list[bytes]:
    """Render report as a carousel of PNG images."""
    if not report_data or not report_data.get("accounts"):
        return []

    slides = []
    slides.append(_render_cover(report_data))
    slides.append(_render_overview(report_data))
    slides.append(_render_followers_chart(report_data))
    slides.append(_render_engagement_chart(report_data))

    # Growth slide only if we have growth data
    has_growth = any(a.get("growth_pct", 0) != 0 for a in report_data["accounts"])
    if has_growth:
        slides.append(_render_growth_chart(report_data))

    slides.append(_render_top_posts(report_data))

    if insights:
        slides.append(_render_insights(report_data, insights))

    return slides


# ──────────────── Step 3b: Render Text ────────────────


def render_report_text(report_data: dict, insights: str = "") -> str:
    """Render report as structured markdown text."""
    if not report_data or not report_data.get("accounts"):
        return ""

    lines = []
    date_str = datetime.fromisoformat(report_data["generated_at"]).strftime("%d/%m/%Y")
    lines.append(f"## Relatorio Competitivo\n*Gerado em {date_str}*\n")

    # Overview per account
    lines.append("### Visao Geral\n")
    for acc in report_data["accounts"]:
        lines.append(
            f"**@{acc['username']}** ({acc['platform']})\n"
            f"- Seguidores: {acc['followers']:,}\n"
            f"- Posts: {acc['posts']:,}\n"
            f"- Taxa de engajamento: {acc['engagement_rate']}%\n"
            f"- Media de likes: {acc['avg_likes']:,}\n"
            f"- Crescimento 30d: +{acc['growth_pct']}%\n"
        )

    # Rankings
    comp = report_data.get("comparison", {})
    if comp.get("followers_ranking"):
        lines.append("### Ranking de Seguidores\n")
        for i, (username, followers) in enumerate(comp["followers_ranking"], 1):
            lines.append(f"{i}. @{username}: {followers:,}")

    if comp.get("engagement_ranking"):
        lines.append("\n### Ranking de Engajamento\n")
        for i, (username, rate) in enumerate(comp["engagement_ranking"], 1):
            lines.append(f"{i}. @{username}: {rate}%")

    if comp.get("growth_ranking"):
        lines.append("\n### Ranking de Crescimento (30d)\n")
        for i, (username, growth) in enumerate(comp["growth_ranking"], 1):
            lines.append(f"{i}. @{username}: {growth}")

    # Top posts
    lines.append("\n### Top Posts\n")
    for acc in report_data["accounts"]:
        caption = (acc.get("top_post_caption", "") or "")[:100]
        if len(acc.get("top_post_caption", "") or "") > 100:
            caption += "..."
        lines.append(f"**@{acc['username']}**: {acc.get('top_post_likes', 0):,} likes — {caption}")

    # Insights
    if insights:
        lines.append(f"\n### Insights\n\n{insights}")

    return "\n".join(lines)


# ──────────────── Step 3c: Render PDF ────────────────


def render_report_pdf(report_data: dict, insights: str = "") -> bytes:
    """Render report as a PDF document. Returns PDF bytes."""
    try:
        from fpdf import FPDF
    except ImportError:
        logger.error("fpdf2 nao instalado. Execute: pip install fpdf2")
        return b""

    if not report_data or not report_data.get("accounts"):
        return b""

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    # ── Cover page ──
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 24)
    pdf.cell(0, 15, "Relatorio Competitivo", ln=True, align="C")
    pdf.ln(5)
    date_str = datetime.fromisoformat(report_data["generated_at"]).strftime("%d/%m/%Y")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, f"Gerado em {date_str}", ln=True, align="C")
    pdf.ln(5)

    usernames = ", ".join(f"@{a['username']}" for a in report_data["accounts"])
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 8, f"Contas: {usernames}", ln=True, align="C")
    pdf.ln(15)

    # ── Overview table ──
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Visao Geral", ln=True)
    pdf.ln(3)

    for acc in report_data["accounts"]:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, f"@{acc['username']} ({acc['platform']})", ln=True)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, f"Seguidores: {acc['followers']:,}  |  Posts: {acc['posts']:,}", ln=True)
        pdf.cell(0, 6, f"Engajamento: {acc['engagement_rate']}%  |  Media likes: {acc['avg_likes']:,}", ln=True)
        pdf.cell(0, 6, f"Crescimento 30d: +{acc['growth_pct']}%", ln=True)
        if acc.get("top_post_caption"):
            caption = acc["top_post_caption"][:100]
            pdf.set_font("Helvetica", "I", 9)
            pdf.cell(0, 6, f"Top post ({acc.get('top_post_likes', 0):,} likes): {caption}", ln=True)
        pdf.ln(5)

    # ── Rankings ──
    comp = report_data.get("comparison", {})

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 12, "Rankings", ln=True)
    pdf.ln(2)

    for title, ranking in [
        ("Seguidores", comp.get("followers_ranking", [])),
        ("Engajamento", comp.get("engagement_ranking", [])),
        ("Crescimento 30d", comp.get("growth_ranking", [])),
    ]:
        if not ranking:
            continue
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 8, title, ln=True)
        pdf.set_font("Helvetica", "", 10)
        for i, (username, value) in enumerate(ranking, 1):
            if isinstance(value, float):
                val_str = f"{value}%"
            elif isinstance(value, int) and value >= 1000:
                val_str = f"{value:,}"
            else:
                val_str = str(value)
            pdf.cell(0, 6, f"  {i}. @{username}: {val_str}", ln=True)
        pdf.ln(3)

    # ── Insights ──
    if insights:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 12, "Insights e Oportunidades", ln=True)
        pdf.ln(3)
        pdf.set_font("Helvetica", "", 10)
        # fpdf2 multi_cell handles line wrapping
        safe_insights = insights.encode("latin-1", errors="replace").decode("latin-1")
        pdf.multi_cell(0, 6, safe_insights)

    return bytes(pdf.output())


def _new_slide() -> tuple[Image.Image, ImageDraw.Draw]:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    return img, draw


def _to_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _draw_header(draw: ImageDraw.Draw, title: str):
    font = _get_font(28, bold=True)
    draw.text((60, 50), "TEQ", fill=ACCENT, font=_get_font(20, bold=True))
    draw.text((60, 90), title, fill=TEXT_PRIMARY, font=font)
    draw.line([(60, 135), (W - 60, 135)], fill=(40, 40, 55), width=2)


def _draw_horizontal_bars(draw: ImageDraw.Draw, data: list[tuple[str, float]], y_start: int, max_width: int = 700, color_idx: int = 0):
    """Draw horizontal bar chart."""
    if not data:
        return
    max_val = max(v for _, v in data) or 1
    font_label = _get_font(24)
    font_val = _get_font(22, bold=True)
    bar_height = 50
    gap = 25
    x_start = 200

    for i, (label, value) in enumerate(data):
        y = y_start + i * (bar_height + gap)
        color = CHART_COLORS[i % len(CHART_COLORS)]

        # Label
        draw.text((60, y + 12), f"@{label}", fill=TEXT_SECONDARY, font=font_label)

        # Bar
        bar_w = int((value / max_val) * max_width) if max_val > 0 else 0
        bar_w = max(bar_w, 4)
        draw.rounded_rectangle(
            [(x_start, y + 5), (x_start + bar_w, y + bar_height - 5)],
            radius=8,
            fill=color,
        )

        # Value
        val_str = _format_number(int(value)) if value > 100 else f"{value:.1f}%"
        draw.text((x_start + bar_w + 15, y + 12), val_str, fill=TEXT_PRIMARY, font=font_val)


def _render_cover(data: dict) -> bytes:
    img, draw = _new_slide()

    # Brand
    font_brand = _get_font(36, bold=True)
    font_title = _get_font(48, bold=True)
    font_sub = _get_font(24)
    font_accounts = _get_font(28)

    # Decorative gradient rectangle
    for i in range(300):
        alpha = max(0, 255 - i)
        r = int(ACCENT[0] * alpha / 255)
        g = int(ACCENT[1] * alpha / 255)
        b = int(ACCENT[2] * alpha / 255)
        draw.line([(0, 200 + i), (W, 200 + i)], fill=(r // 4, g // 4, b // 4))

    draw.text((60, 80), "TEQ", fill=ACCENT, font=font_brand)

    draw.text((60, 450), data.get("title", "Relatorio Competitivo"), fill=TEXT_PRIMARY, font=font_title)

    date_str = datetime.fromisoformat(data["generated_at"]).strftime("%d/%m/%Y")
    draw.text((60, 520), f"Gerado em {date_str}", fill=TEXT_SECONDARY, font=font_sub)

    # Account names
    usernames = [f"@{a['username']}" for a in data["accounts"]]
    y = 620
    for uname in usernames:
        draw.text((80, y), uname, fill=TEXT_MUTED, font=font_accounts)
        y += 45

    return _to_bytes(img)


def _render_overview(data: dict) -> bytes:
    img, draw = _new_slide()
    _draw_header(draw, "Visao Geral")

    accounts = data["accounts"]
    font_name = _get_font(26, bold=True)
    font_val = _get_font(22)
    font_label = _get_font(16)

    y = 180
    col_w = (W - 120) // min(len(accounts), 3)

    for i, acc in enumerate(accounts[:6]):
        col = i % 3
        row = i // 3
        x = 60 + col * col_w
        cy = y + row * 380

        # Card background
        draw.rounded_rectangle(
            [(x, cy), (x + col_w - 20, cy + 350)],
            radius=16,
            fill=BG_CARD,
        )

        color = CHART_COLORS[i % len(CHART_COLORS)]

        # Username
        draw.text((x + 20, cy + 20), f"@{acc['username']}", fill=color, font=font_name)
        draw.text((x + 20, cy + 55), acc.get("platform", "instagram"), fill=TEXT_MUTED, font=font_label)

        # Metrics
        metrics = [
            ("Seguidores", _format_number(acc["followers"])),
            ("Posts", _format_number(acc["posts"])),
            ("Eng. Rate", f"{acc['engagement_rate']}%"),
            ("Media Likes", _format_number(acc["avg_likes"])),
            ("Crescimento", f"+{acc['growth_pct']}%"),
        ]
        my = cy + 95
        for label, val in metrics:
            draw.text((x + 20, my), label, fill=TEXT_MUTED, font=font_label)
            draw.text((x + 20, my + 22), val, fill=TEXT_PRIMARY, font=font_val)
            my += 50

    return _to_bytes(img)


def _render_followers_chart(data: dict) -> bytes:
    img, draw = _new_slide()
    _draw_header(draw, "Comparacao de Seguidores")

    bars = [(a["username"], a["followers"]) for a in data["accounts"]]
    _draw_horizontal_bars(draw, bars, y_start=200)

    return _to_bytes(img)


def _render_engagement_chart(data: dict) -> bytes:
    img, draw = _new_slide()
    _draw_header(draw, "Taxa de Engajamento (%)")

    bars = [(a["username"], a["engagement_rate"]) for a in data["accounts"]]
    bars.sort(key=lambda x: x[1], reverse=True)
    _draw_horizontal_bars(draw, bars, y_start=200)

    return _to_bytes(img)


def _render_growth_chart(data: dict) -> bytes:
    img, draw = _new_slide()
    _draw_header(draw, "Crescimento 30 dias (%)")

    bars = [(a["username"], a["growth_pct"]) for a in data["accounts"]]
    bars.sort(key=lambda x: x[1], reverse=True)
    _draw_horizontal_bars(draw, bars, y_start=200)

    return _to_bytes(img)


def _render_top_posts(data: dict) -> bytes:
    img, draw = _new_slide()
    _draw_header(draw, "Top Posts por Conta")

    font_name = _get_font(24, bold=True)
    font_text = _get_font(18)
    font_metric = _get_font(20, bold=True)

    y = 180
    for i, acc in enumerate(data["accounts"][:5]):
        color = CHART_COLORS[i % len(CHART_COLORS)]

        draw.rounded_rectangle(
            [(60, y), (W - 60, y + 160)],
            radius=12,
            fill=BG_CARD,
        )

        draw.text((80, y + 15), f"@{acc['username']}", fill=color, font=font_name)
        draw.text((80, y + 50), f"Top post: {acc['top_post_likes']:,} likes", fill=TEXT_PRIMARY, font=font_metric)

        caption = acc.get("top_post_caption", "")[:100]
        if len(acc.get("top_post_caption", "")) > 100:
            caption += "..."
        draw.text((80, y + 85), caption, fill=TEXT_SECONDARY, font=font_text)

        y += 185

    return _to_bytes(img)


def _render_insights(data: dict, insights: str) -> bytes:
    img, draw = _new_slide()
    _draw_header(draw, "Insights e Oportunidades")

    font = _get_font(22)
    y = 180

    # Word-wrap insights text
    lines = []
    for line in insights.split("\n"):
        line = line.strip()
        if not line:
            lines.append("")
            continue
        # Simple word wrap at ~55 chars
        while len(line) > 55:
            split_at = line[:55].rfind(" ")
            if split_at == -1:
                split_at = 55
            lines.append(line[:split_at])
            line = line[split_at:].strip()
        lines.append(line)

    for line in lines[:35]:
        color = ACCENT if line.startswith(("-", "*", "•")) else TEXT_SECONDARY
        draw.text((80, y), line, fill=color, font=font)
        y += 32

    return _to_bytes(img)

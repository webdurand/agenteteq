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
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from fpdf import FPDF
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

# Hex versions for matplotlib
CHART_COLORS_HEX = ["#6366F1", "#8B5CF6", "#EC4899", "#F59E0B", "#10B981", "#3B82F6"]

# Dashboard theme palettes
THEMES = {
    "dark": {
        "bg": "#0F0F14",
        "card_bg": "#191923",
        "text": "#F0F0F5",
        "text_secondary": "#A0A0AF",
        "text_muted": "#64648A",
        "grid": "#2A2A3A",
        "accent": "#6366F1",
        "accent_rgb": (99, 102, 241),
        "bg_rgb": (15, 15, 20),
        "card_bg_rgb": (25, 25, 35),
        "text_rgb": (240, 240, 245),
        "text_secondary_rgb": (160, 160, 175),
        "text_muted_rgb": (100, 100, 138),
        "divider_rgb": (40, 40, 55),
    },
    "light": {
        "bg": "#FFFFFF",
        "card_bg": "#F3F4F6",
        "text": "#1A1A2E",
        "text_secondary": "#6B7280",
        "text_muted": "#9CA3AF",
        "grid": "#E5E7EB",
        "accent": "#4F46E5",
        "accent_rgb": (79, 70, 229),
        "bg_rgb": (255, 255, 255),
        "card_bg_rgb": (243, 244, 246),
        "text_rgb": (26, 26, 46),
        "text_secondary_rgb": (107, 114, 128),
        "text_muted_rgb": (156, 163, 175),
        "divider_rgb": (229, 231, 235),
    },
}

FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


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
    period_days: int = 30,
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
        recent_posts = get_recent_content(account["id"], limit=50)

        # Calculate avg engagement
        avg_likes = 0
        avg_comments = 0
        avg_views = 0
        if recent_posts:
            avg_likes = sum(p.get("likes_count", 0) for p in recent_posts) // len(recent_posts)
            avg_comments = sum(p.get("comments_count", 0) for p in recent_posts) // len(recent_posts)
            views = [p.get("views_count", 0) for p in recent_posts if p.get("views_count")]
            avg_views = sum(views) // len(views) if views else 0

        followers = account.get("followers_count", 0)
        engagement_rate = (avg_likes + avg_comments) / followers * 100 if followers > 0 else 0.0

        top_post = top_posts[0] if top_posts else {}

        growth = get_growth_summary(account["id"], days=period_days)
        snapshots = get_account_snapshots(account["id"], days=period_days)

        # Content type distribution
        content_types: dict[str, int] = Counter()
        for p in recent_posts:
            ct = p.get("content_type", "image") or "image"
            content_types[ct] += 1

        # Posting frequency (posts per week)
        posting_frequency = 0.0
        if len(recent_posts) >= 2:
            try:
                dates = sorted(
                    datetime.fromisoformat(p["posted_at"]) for p in recent_posts if p.get("posted_at")
                )
                if len(dates) >= 2:
                    span_days = max((dates[-1] - dates[0]).days, 1)
                    posting_frequency = round(len(dates) / span_days * 7, 1)
            except (ValueError, TypeError):
                pass

        accounts_data.append({
            "username": account["username"],
            "platform": account["platform"],
            "display_name": account.get("display_name", ""),
            "followers": followers,
            "posts": account.get("posts_count", 0),
            "avg_likes": avg_likes,
            "avg_comments": avg_comments,
            "avg_views": avg_views,
            "engagement_rate": round(engagement_rate, 2),
            "top_post_caption": (top_post.get("caption", "") or "")[:150],
            "top_post_likes": top_post.get("likes_count", 0),
            "growth_pct": growth.get("pct", 0.0),
            "growth_delta": growth.get("followers_delta", 0),
            # New enriched fields for dashboard
            "snapshots": snapshots,
            "content_types": dict(content_types),
            "top_posts": top_posts[:5],
            "posting_frequency": posting_frequency,
        })

    if not accounts_data:
        return {}

    # Build comparison rankings
    followers_ranking = sorted(accounts_data, key=lambda a: a["followers"], reverse=True)
    engagement_ranking = sorted(accounts_data, key=lambda a: a["engagement_rate"], reverse=True)
    growth_ranking = sorted(accounts_data, key=lambda a: a["growth_pct"], reverse=True)

    title = "Relatorio Competitivo" if len(accounts_data) > 1 else f"Relatorio @{accounts_data[0]['username']}"

    return {
        "title": title,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period_days": period_days,
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


# ──────────────── Step 3d: Dashboard PDF (matplotlib + fpdf2) ────────────────


def _chart_to_bytes(fig: plt.Figure) -> io.BytesIO:
    """Save a matplotlib figure to a BytesIO buffer and close it."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor(), edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf


def _apply_chart_style(ax: plt.Axes, theme: str = "dark"):
    """Apply theme styling to a matplotlib axes."""
    t = THEMES[theme]
    ax.set_facecolor(t["card_bg"])
    ax.tick_params(colors=t["text_secondary"], labelsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color(t["grid"])
    ax.spines["left"].set_color(t["grid"])
    ax.grid(axis="y", color=t["grid"], alpha=0.4, linewidth=0.5)


def _chart_followers_comparison(accounts: list[dict], theme: str = "dark") -> io.BytesIO | None:
    """Horizontal bar chart comparing followers across accounts."""
    if not accounts:
        return None
    t = THEMES[theme]
    fig, ax = plt.subplots(figsize=(7, max(2.5, len(accounts) * 0.8)))
    fig.set_facecolor(t["bg"])
    _apply_chart_style(ax, theme)

    usernames = [f"@{a['username']}" for a in accounts]
    values = [a["followers"] for a in accounts]
    colors = [CHART_COLORS_HEX[i % len(CHART_COLORS_HEX)] for i in range(len(accounts))]

    bars = ax.barh(usernames, values, color=colors, height=0.6, edgecolor="none")
    ax.invert_yaxis()
    ax.set_xlabel("Seguidores", color=t["text_secondary"], fontsize=10)

    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + max(values) * 0.02, bar.get_y() + bar.get_height() / 2,
                _format_number(val), va="center", color=t["text"], fontsize=10, fontweight="bold")

    ax.set_xlim(0, max(values) * 1.2)
    for label in ax.get_yticklabels():
        label.set_color(t["text"])
        label.set_fontsize(11)
    fig.tight_layout()
    return _chart_to_bytes(fig)


def _chart_engagement_comparison(accounts: list[dict], theme: str = "dark") -> io.BytesIO | None:
    """Horizontal bar chart comparing engagement rates."""
    if not accounts:
        return None
    t = THEMES[theme]
    sorted_accs = sorted(accounts, key=lambda a: a["engagement_rate"], reverse=True)
    fig, ax = plt.subplots(figsize=(7, max(2.5, len(accounts) * 0.8)))
    fig.set_facecolor(t["bg"])
    _apply_chart_style(ax, theme)

    usernames = [f"@{a['username']}" for a in sorted_accs]
    values = [a["engagement_rate"] for a in sorted_accs]
    colors = [CHART_COLORS_HEX[i % len(CHART_COLORS_HEX)] for i in range(len(sorted_accs))]

    bars = ax.barh(usernames, values, color=colors, height=0.6, edgecolor="none")
    ax.invert_yaxis()
    ax.set_xlabel("Taxa de Engajamento (%)", color=t["text_secondary"], fontsize=10)

    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + max(values) * 0.05, bar.get_y() + bar.get_height() / 2,
                f"{val:.2f}%", va="center", color=t["text"], fontsize=10, fontweight="bold")

    ax.set_xlim(0, max(values) * 1.3 if max(values) > 0 else 1)
    for label in ax.get_yticklabels():
        label.set_color(t["text"])
        label.set_fontsize(11)
    fig.tight_layout()
    return _chart_to_bytes(fig)


def _chart_followers_timeline(accounts: list[dict], theme: str = "dark") -> io.BytesIO | None:
    """Line chart showing follower growth over time."""
    # Check if any account has enough snapshots
    has_data = any(len(a.get("snapshots", [])) >= 2 for a in accounts)
    if not has_data:
        return None

    t = THEMES[theme]
    fig, ax = plt.subplots(figsize=(7, 4))
    fig.set_facecolor(t["bg"])
    _apply_chart_style(ax, theme)

    for i, acc in enumerate(accounts):
        snapshots = acc.get("snapshots", [])
        if len(snapshots) < 2:
            continue
        dates = []
        followers = []
        for s in snapshots:
            try:
                dt = datetime.fromisoformat(s["fetched_at"])
                dates.append(dt)
                followers.append(s["followers_count"])
            except (ValueError, KeyError):
                continue
        if len(dates) < 2:
            continue
        color = CHART_COLORS_HEX[i % len(CHART_COLORS_HEX)]
        ax.plot(dates, followers, color=color, linewidth=2, marker="o", markersize=3,
                label=f"@{acc['username']}")

    ax.legend(facecolor=t["card_bg"], edgecolor=t["grid"], labelcolor=t["text"],
              fontsize=9, loc="upper left")
    ax.set_ylabel("Seguidores", color=t["text_secondary"], fontsize=10)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate(rotation=30)
    for label in ax.get_xticklabels():
        label.set_color(t["text_secondary"])
    fig.tight_layout()
    return _chart_to_bytes(fig)


def _chart_engagement_timeline(accounts: list[dict], theme: str = "dark") -> io.BytesIO | None:
    """Line chart showing engagement evolution over time."""
    has_data = any(
        len(a.get("snapshots", [])) >= 2 and any(s.get("avg_engagement") for s in a.get("snapshots", []))
        for a in accounts
    )
    if not has_data:
        return None

    t = THEMES[theme]
    fig, ax = plt.subplots(figsize=(7, 4))
    fig.set_facecolor(t["bg"])
    _apply_chart_style(ax, theme)

    for i, acc in enumerate(accounts):
        snapshots = acc.get("snapshots", [])
        dates = []
        engagement = []
        for s in snapshots:
            try:
                avg_eng = s.get("avg_engagement")
                if avg_eng is not None and avg_eng > 0:
                    dates.append(datetime.fromisoformat(s["fetched_at"]))
                    engagement.append(avg_eng)
            except (ValueError, KeyError):
                continue
        if len(dates) < 2:
            continue
        color = CHART_COLORS_HEX[i % len(CHART_COLORS_HEX)]
        ax.plot(dates, engagement, color=color, linewidth=2, marker="o", markersize=3,
                label=f"@{acc['username']}")

    ax.legend(facecolor=t["card_bg"], edgecolor=t["grid"], labelcolor=t["text"],
              fontsize=9, loc="upper left")
    ax.set_ylabel("Engajamento Medio", color=t["text_secondary"], fontsize=10)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate(rotation=30)
    for label in ax.get_xticklabels():
        label.set_color(t["text_secondary"])
    fig.tight_layout()
    return _chart_to_bytes(fig)


def _chart_content_types(accounts: list[dict], theme: str = "dark") -> io.BytesIO | None:
    """Grouped bar chart showing content type distribution per account."""
    has_data = any(a.get("content_types") for a in accounts)
    if not has_data:
        return None

    t = THEMES[theme]
    all_types = set()
    for acc in accounts:
        all_types.update(acc.get("content_types", {}).keys())
    if not all_types:
        return None

    all_types = sorted(all_types)
    fig, ax = plt.subplots(figsize=(7, 4))
    fig.set_facecolor(t["bg"])
    _apply_chart_style(ax, theme)

    x = np.arange(len(all_types))
    n_accounts = len(accounts)
    width = 0.7 / max(n_accounts, 1)

    for i, acc in enumerate(accounts):
        ct = acc.get("content_types", {})
        values = [ct.get(tp, 0) for tp in all_types]
        offset = (i - n_accounts / 2 + 0.5) * width
        color = CHART_COLORS_HEX[i % len(CHART_COLORS_HEX)]
        ax.bar(x + offset, values, width, label=f"@{acc['username']}", color=color, edgecolor="none")

    type_labels = {"image": "Imagem", "video": "Video", "carousel": "Carrossel", "reel": "Reels", "short": "Shorts"}
    ax.set_xticks(x)
    ax.set_xticklabels([type_labels.get(tp, tp.title()) for tp in all_types])
    for label in ax.get_xticklabels():
        label.set_color(t["text"])
        label.set_fontsize(10)
    ax.set_ylabel("Quantidade", color=t["text_secondary"], fontsize=10)
    ax.legend(facecolor=t["card_bg"], edgecolor=t["grid"], labelcolor=t["text"], fontsize=9)
    fig.tight_layout()
    return _chart_to_bytes(fig)


def _chart_top_posts(accounts: list[dict], theme: str = "dark") -> io.BytesIO | None:
    """Bar chart of top posts across all accounts by likes."""
    all_posts = []
    for i, acc in enumerate(accounts):
        for p in acc.get("top_posts", []):
            all_posts.append({
                "username": acc["username"],
                "likes": p.get("likes_count", 0),
                "caption": (p.get("caption", "") or "")[:40],
                "color_idx": i,
            })
    if not all_posts:
        return None

    all_posts.sort(key=lambda x: x["likes"], reverse=True)
    top = all_posts[:8]

    t = THEMES[theme]
    fig, ax = plt.subplots(figsize=(7, max(2.5, len(top) * 0.7)))
    fig.set_facecolor(t["bg"])
    _apply_chart_style(ax, theme)

    labels = [f"@{p['username']}" for p in top]
    values = [p["likes"] for p in top]
    colors = [CHART_COLORS_HEX[p["color_idx"] % len(CHART_COLORS_HEX)] for p in top]

    bars = ax.barh(range(len(top)), values, color=colors, height=0.6, edgecolor="none")
    ax.set_yticks(range(len(top)))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("Likes", color=t["text_secondary"], fontsize=10)

    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + max(values) * 0.02, bar.get_y() + bar.get_height() / 2,
                _format_number(val), va="center", color=t["text"], fontsize=10, fontweight="bold")

    ax.set_xlim(0, max(values) * 1.2 if values else 1)
    for label in ax.get_yticklabels():
        label.set_color(t["text"])
        label.set_fontsize(10)
    fig.tight_layout()
    return _chart_to_bytes(fig)


# ──────────────── Dashboard PDF Builder ────────────────


class _DashboardPDF(FPDF):
    """Extended FPDF with dashboard helpers."""

    def __init__(self, theme: str = "dark"):
        super().__init__()
        self.theme = theme
        self.t = THEMES[theme]
        self.set_auto_page_break(auto=True, margin=15)
        # Register DejaVu fonts for full Unicode support
        self.add_font("DejaVu", "", FONT_REGULAR, uni=True)
        self.add_font("DejaVu", "B", FONT_BOLD, uni=True)

    def dark_page(self):
        """Add a new page with themed background."""
        self.add_page()
        r, g, b = self.t["bg_rgb"]
        self.set_fill_color(r, g, b)
        self.rect(0, 0, 210, 297, "F")

    def section_title(self, title: str, y: float | None = None):
        """Draw a section title with accent underline."""
        if y is not None:
            self.set_y(y)
        r, g, b = self.t["accent_rgb"]
        self.set_text_color(r, g, b)
        self.set_font("DejaVu", "B", 16)
        self.cell(0, 10, title, ln=True)
        # Accent underline
        self.set_draw_color(r, g, b)
        self.set_line_width(0.8)
        self.line(10, self.get_y(), 80, self.get_y())
        self.ln(5)
        # Reset text color
        r, g, b = self.t["text_rgb"]
        self.set_text_color(r, g, b)

    def themed_text(self, text: str, size: int = 10, bold: bool = False, color: str = "text"):
        """Write text with themed color."""
        rgb = self.t[f"{color}_rgb"]
        self.set_text_color(*rgb)
        style = "B" if bold else ""
        self.set_font("DejaVu", style, size)
        self.multi_cell(0, size * 0.55, text)

    def metric_card(self, x: float, y: float, w: float, h: float,
                    label: str, value: str, color_rgb: tuple = None):
        """Draw a KPI metric card."""
        cr, cg, cb = self.t["card_bg_rgb"]
        self.set_fill_color(cr, cg, cb)
        self.rect(x, y, w, h, "F")

        # Label
        self.set_xy(x + 3, y + 4)
        r, g, b = self.t["text_muted_rgb"]
        self.set_text_color(r, g, b)
        self.set_font("DejaVu", "", 8)
        self.cell(w - 6, 4, label)

        # Value
        self.set_xy(x + 3, y + 12)
        if color_rgb:
            self.set_text_color(*color_rgb)
        else:
            r, g, b = self.t["text_rgb"]
            self.set_text_color(r, g, b)
        self.set_font("DejaVu", "B", 14)
        self.cell(w - 6, 8, value)

    def embed_chart(self, chart_buf: io.BytesIO, x: float = 10, w: float = 190):
        """Embed a matplotlib chart image and advance Y position."""
        from PIL import Image as PILImage
        chart_buf.seek(0)
        img = PILImage.open(chart_buf)
        img_w, img_h = img.size
        rendered_h = w * (img_h / img_w)  # mm, preserving aspect ratio
        chart_buf.seek(0)
        y_before = self.get_y()
        self.image(chart_buf, x=x, y=y_before, w=w)
        self.set_y(y_before + rendered_h + 2)


def render_dashboard_pdf(report_data: dict, insights: str = "", theme: str = "dark") -> bytes:
    """Render a professional dashboard-style PDF report.

    Args:
        report_data: Output from collect_report_data().
        insights: LLM-generated insights text.
        theme: 'dark' or 'light'.

    Returns:
        PDF file bytes.
    """
    if not report_data or not report_data.get("accounts"):
        return b""

    if theme not in THEMES:
        theme = "dark"

    accounts = report_data["accounts"]
    is_single = len(accounts) == 1

    try:
        pdf = _DashboardPDF(theme=theme)
        t = THEMES[theme]

        # ── Page 1: Cover ──
        pdf.dark_page()

        # Decorative gradient band
        accent_r, accent_g, accent_b = t["accent_rgb"]
        for i in range(60):
            alpha = 1.0 - (i / 60)
            r = int(accent_r * alpha * 0.3)
            g = int(accent_g * alpha * 0.3)
            b = int(accent_b * alpha * 0.3)
            bg_r, bg_g, bg_b = t["bg_rgb"]
            r = min(255, r + bg_r)
            g = min(255, g + bg_g)
            b = min(255, b + bg_b)
            pdf.set_draw_color(r, g, b)
            pdf.line(0, 30 + i * 0.8, 210, 30 + i * 0.8)

        # Brand
        pdf.set_xy(10, 90)
        pdf.set_text_color(accent_r, accent_g, accent_b)
        pdf.set_font("DejaVu", "B", 30)
        pdf.cell(0, 15, "TEQ")

        # Title
        pdf.set_xy(10, 115)
        r, g, b = t["text_rgb"]
        pdf.set_text_color(r, g, b)
        pdf.set_font("DejaVu", "B", 24)
        pdf.cell(0, 15, report_data.get("title", "Relatorio"))

        # Date
        pdf.set_xy(10, 140)
        r, g, b = t["text_secondary_rgb"]
        pdf.set_text_color(r, g, b)
        pdf.set_font("DejaVu", "", 12)
        date_str = datetime.fromisoformat(report_data["generated_at"]).strftime("%d/%m/%Y %H:%M")
        pdf.cell(0, 8, f"Gerado em {date_str}")

        # Period
        period = report_data.get("period_days", 30)
        pdf.set_xy(10, 152)
        pdf.cell(0, 8, f"Periodo de analise: {period} dias")

        # Account list
        pdf.set_xy(10, 175)
        pdf.set_font("DejaVu", "", 11)
        for i, acc in enumerate(accounts):
            color = CHART_COLORS[i % len(CHART_COLORS)]
            pdf.set_text_color(*color)
            pdf.set_x(15)
            platform_label = acc["platform"].title()
            pdf.cell(0, 8, f"@{acc['username']}  ({platform_label})", ln=True)

        # ── Page 2: KPI Cards ──
        pdf.dark_page()
        pdf.section_title("Visao Geral")

        card_w = 58 if not is_single else 90
        card_h = 28
        cards_per_row = 3 if not is_single else 2
        start_x = 10
        gap = 3

        for i, acc in enumerate(accounts):
            acc_color = CHART_COLORS[i % len(CHART_COLORS)]

            # Account header
            pdf.set_x(10)
            pdf.set_text_color(*acc_color)
            pdf.set_font("DejaVu", "B", 13)
            pdf.cell(0, 8, f"@{acc['username']} ({acc['platform']})", ln=True)
            pdf.ln(2)

            base_y = pdf.get_y()

            metrics = [
                ("Seguidores", _format_number(acc["followers"])),
                ("Engajamento", f"{acc['engagement_rate']}%"),
                ("Crescimento", f"+{acc['growth_pct']}%"),
                ("Media Likes", _format_number(acc["avg_likes"])),
                ("Media Comments", _format_number(acc["avg_comments"])),
                ("Posts/Semana", f"{acc.get('posting_frequency', 0)}"),
            ]

            for j, (label, value) in enumerate(metrics):
                row = j // cards_per_row
                col = j % cards_per_row
                cx = start_x + col * (card_w + gap)
                cy = base_y + row * (card_h + gap)
                val_color = acc_color if j < 3 else None
                pdf.metric_card(cx, cy, card_w, card_h, label, value, val_color)

            rows_needed = (len(metrics) + cards_per_row - 1) // cards_per_row
            pdf.set_y(base_y + rows_needed * (card_h + gap) + 5)

        # ── Page 3: Followers Comparison ──
        if len(accounts) > 1:
            pdf.dark_page()
            pdf.section_title("Comparacao de Seguidores")
            chart = _chart_followers_comparison(accounts, theme)
            if chart:
                pdf.embed_chart(chart)

        # ── Page 4: Followers Timeline ──
        has_snapshots = any(len(a.get("snapshots", [])) >= 2 for a in accounts)
        if has_snapshots:
            pdf.dark_page()
            pdf.section_title("Evolucao de Seguidores")
            chart = _chart_followers_timeline(accounts, theme)
            if chart:
                pdf.embed_chart(chart)
                pdf.ln(5)
                r, g, b = t["text_muted_rgb"]
                pdf.set_text_color(r, g, b)
                pdf.set_font("DejaVu", "", 8)
                pdf.cell(0, 5, f"Dados dos ultimos {period} dias (snapshots a cada 6 horas)", ln=True)

            # Engagement timeline — separate page
            eng_chart = _chart_engagement_timeline(accounts, theme)
            if eng_chart:
                pdf.dark_page()
                pdf.section_title("Evolucao do Engajamento")
                pdf.embed_chart(eng_chart)
        else:
            # No snapshots - show note
            pdf.dark_page()
            pdf.section_title("Evolucao Temporal")
            r, g, b = t["text_secondary_rgb"]
            pdf.set_text_color(r, g, b)
            pdf.set_font("DejaVu", "", 11)
            pdf.multi_cell(0, 7,
                "Dados historicos insuficientes para graficos de evolucao.\n\n"
                "Os snapshots sao coletados automaticamente a cada 6 horas. "
                "Gere o relatorio novamente em alguns dias para ver os graficos de tendencia."
            )

        # ── Page 5: Engagement Analysis ──
        pdf.dark_page()
        pdf.section_title("Analise de Engajamento")
        chart = _chart_engagement_comparison(accounts, theme)
        if chart:
            pdf.embed_chart(chart)

        # ── Page 6: Content Strategy ──
        has_content_types = any(a.get("content_types") for a in accounts)
        if has_content_types:
            pdf.dark_page()
            pdf.section_title("Estrategia de Conteudo")
            chart = _chart_content_types(accounts, theme)
            if chart:
                pdf.embed_chart(chart)
                pdf.ln(8)

            # Posting frequency table
            pdf.set_text_color(*t["accent_rgb"])
            pdf.set_font("DejaVu", "B", 12)
            pdf.cell(0, 8, "Frequencia de Publicacao", ln=True)
            pdf.ln(3)

            for i, acc in enumerate(accounts):
                color = CHART_COLORS[i % len(CHART_COLORS)]
                pdf.set_text_color(*color)
                pdf.set_font("DejaVu", "B", 10)
                pdf.cell(60, 6, f"@{acc['username']}")
                pdf.set_text_color(*t["text_rgb"])
                pdf.set_font("DejaVu", "", 10)
                freq = acc.get("posting_frequency", 0)
                pdf.cell(40, 6, f"{freq} posts/semana")

                # Best content type
                ct = acc.get("content_types", {})
                if ct:
                    best_type = max(ct, key=ct.get)
                    type_labels = {"image": "Imagem", "video": "Video", "carousel": "Carrossel",
                                   "reel": "Reels", "short": "Shorts"}
                    pdf.set_text_color(*t["text_secondary_rgb"])
                    pdf.cell(0, 6, f"Mais usado: {type_labels.get(best_type, best_type)}", ln=True)
                else:
                    pdf.ln()

        # ── Page 7: Top Posts Chart ──
        chart = _chart_top_posts(accounts, theme)
        if chart:
            pdf.dark_page()
            pdf.section_title("Top Posts")
            pdf.embed_chart(chart)

        # ── Page 7b: Top Posts Table ──
        # Collect all top posts
        all_posts = []
        for i, acc in enumerate(accounts):
            for p in acc.get("top_posts", []):
                all_posts.append({"username": acc["username"], "color_idx": i, **p})
        all_posts.sort(key=lambda x: x.get("likes_count", 0), reverse=True)

        if all_posts:
            pdf.dark_page()
            pdf.section_title("Detalhes dos Top Posts")

            pdf.set_font("DejaVu", "B", 9)
            pdf.set_text_color(*t["text_muted_rgb"])
            col_widths = [8, 35, 25, 25, 97]
            headers = ["#", "Conta", "Likes", "Coments", "Legenda"]
            cr, cg, cb = t["card_bg_rgb"]
            pdf.set_fill_color(cr, cg, cb)

            for j, (hw, ht) in enumerate(zip(col_widths, headers)):
                pdf.cell(hw, 7, ht, fill=True)
            pdf.ln()

            pdf.set_font("DejaVu", "", 8)
            for rank, post in enumerate(all_posts[:10], 1):
                color = CHART_COLORS[post["color_idx"] % len(CHART_COLORS)]
                fill = rank % 2 == 0
                if fill:
                    pdf.set_fill_color(cr, cg, cb)

                pdf.set_text_color(*t["text_rgb"])
                pdf.cell(col_widths[0], 6, str(rank), fill=fill)
                pdf.set_text_color(*color)
                pdf.cell(col_widths[1], 6, f"@{post['username']}", fill=fill)
                pdf.set_text_color(*t["text_rgb"])
                pdf.cell(col_widths[2], 6, _format_number(post.get("likes_count", 0)), fill=fill)
                pdf.cell(col_widths[3], 6, _format_number(post.get("comments_count", 0)), fill=fill)
                caption = (post.get("caption", "") or "")[:60]
                pdf.set_text_color(*t["text_secondary_rgb"])
                pdf.cell(col_widths[4], 6, caption, fill=fill, ln=True)

        # ── Page 8: Rankings (only for 2+ accounts) ──
        if not is_single:
            pdf.dark_page()
            pdf.section_title("Rankings")

            comp = report_data.get("comparison", {})
            rankings = [
                ("Seguidores", comp.get("followers_ranking", [])),
                ("Engajamento", comp.get("engagement_ranking", [])),
                ("Crescimento 30d", comp.get("growth_ranking", [])),
            ]

            col_width = 60
            start_x = 10

            for col_idx, (title, ranking) in enumerate(rankings):
                if not ranking:
                    continue
                x = start_x + col_idx * (col_width + 5)

                # Column header
                pdf.set_xy(x, 40)
                pdf.set_text_color(*t["accent_rgb"])
                pdf.set_font("DejaVu", "B", 11)
                pdf.cell(col_width, 8, title)

                for rank, (username, value) in enumerate(ranking, 1):
                    y = 52 + rank * 10
                    pdf.set_xy(x, y)

                    # Medal colors for top 3
                    if rank == 1:
                        pdf.set_text_color(255, 215, 0)  # gold
                    elif rank == 2:
                        pdf.set_text_color(192, 192, 192)  # silver
                    elif rank == 3:
                        pdf.set_text_color(205, 127, 50)  # bronze
                    else:
                        pdf.set_text_color(*t["text_rgb"])

                    pdf.set_font("DejaVu", "B", 10)
                    pdf.cell(8, 6, f"{rank}.")
                    pdf.set_text_color(*t["text_rgb"])
                    pdf.set_font("DejaVu", "", 10)

                    if isinstance(value, float):
                        val_str = f"@{username} ({value}%)"
                    elif isinstance(value, int):
                        val_str = f"@{username} ({_format_number(value)})"
                    else:
                        val_str = f"@{username} ({value})"
                    pdf.cell(col_width - 8, 6, val_str)

        # ── Page 9: Insights ──
        if insights:
            pdf.dark_page()
            pdf.section_title("Insights e Oportunidades")

            pdf.set_font("DejaVu", "", 10)
            pdf.set_x(10)
            for line in insights.split("\n"):
                line = line.strip()
                if not line:
                    pdf.ln(3)
                    continue
                pdf.set_x(10)
                if line.startswith(("-", "*", "\u2022")):
                    pdf.set_text_color(*t["accent_rgb"])
                    pdf.set_font("DejaVu", "B", 10)
                    pdf.multi_cell(190, 6, line)
                else:
                    pdf.set_text_color(*t["text_secondary_rgb"])
                    pdf.set_font("DejaVu", "", 10)
                    pdf.multi_cell(190, 6, line)

        # ── Footer on last page ──
        pdf.set_y(-30)
        pdf.set_draw_color(*t["divider_rgb"])
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(3)
        pdf.set_text_color(*t["text_muted_rgb"])
        pdf.set_font("DejaVu", "", 8)
        pdf.cell(0, 5, "Gerado por TEQ  |  Monitoramento de Redes Sociais", align="C")

        return bytes(pdf.output())

    except Exception as e:
        logger.error("Erro ao gerar dashboard PDF: %s", e, exc_info=True)
        return b""


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

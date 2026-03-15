"""
Canvas Editor tools — Canva Conversacional.

Provides LLM-callable tools for composing images via natural language commands.
Each tool mutates a canvas document (JSON), saves to DB, renders a preview,
and sends it to the user via WebSocket.
"""

import json
import logging
import uuid
from typing import Optional

import cloudinary.uploader

from src.models.canvas_session import (
    create_canvas_session,
    get_canvas_session,
    get_active_canvas,
    update_canvas_json,
)
from src.models.shared_assets import (
    search_assets,
    get_asset_by_name,
    increment_usage,
)
from src.tools.image_generation.canvas_renderer import render_canvas, clear_image_cache
from src.integrations.image_storage import _ensure_cloudinary_config, convert_to_webp
from src.events import emit_event_sync

logger = logging.getLogger(__name__)

_ensure_cloudinary_config()

# Format presets
_FORMATS = {
    "1080x1080": (1080, 1080),
    "1080x1350": (1080, 1350),
    "1350x1080": (1350, 1080),
    "1080x1920": (1080, 1920),
}

# Position presets → (x, y) as percentage of canvas
_POSITIONS = {
    "top-left": (0.08, 0.08),
    "top": (0.08, 0.08),
    "top-center": (0.1, 0.08),
    "top-right": (0.6, 0.08),
    "center-left": (0.08, 0.35),
    "center": (0.1, 0.35),
    "center-right": (0.6, 0.35),
    "bottom-left": (0.08, 0.7),
    "bottom": (0.08, 0.7),
    "bottom-center": (0.1, 0.7),
    "bottom-right": (0.6, 0.7),
}


def create_canvas_tools(user_id: str, channel: str = "web", notifier=None):
    """Factory that creates canvas editor tools with user context pre-injected."""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_fail_canvas(canvas_id: str = "") -> tuple:
        """Load canvas session, return (canvas_dict, canvas_doc) or raise."""
        if canvas_id:
            session = get_canvas_session(canvas_id)
        else:
            session = get_active_canvas(user_id)
        if not session:
            return None, None
        return session, session["canvas_json"]

    def _save_and_render(session_id: str, canvas_doc: dict, upload: bool = True) -> str:
        """Save canvas to DB, render, upload preview, send WS event."""
        # Render
        png_bytes = render_canvas(canvas_doc)

        thumbnail_url = ""
        if upload:
            try:
                webp_bytes = convert_to_webp(png_bytes)
                result = cloudinary.uploader.upload(
                    webp_bytes,
                    folder=f"canvas/{user_id}",
                    resource_type="image",
                    format="webp",
                )
                thumbnail_url = result.get("secure_url", "")
            except Exception as e:
                logger.error("Failed to upload canvas preview: %s", e)

        update_canvas_json(session_id, canvas_doc, thumbnail_url=thumbnail_url or None)

        # Notify frontend
        layers_count = len(canvas_doc.get("layers", []))
        emit_event_sync(user_id, "canvas_preview", {
            "canvas_id": session_id,
            "preview_url": thumbnail_url,
            "layers_count": layers_count,
        })

        return thumbnail_url

    def _resolve_position(position: str, w: int, h: int, layer_w: int = 0, layer_h: int = 0) -> tuple:
        """Convert position string to (x, y) pixel coordinates."""
        if "," in position:
            # Absolute coords: "x:100,y:200"
            parts = {}
            for p in position.split(","):
                k, v = p.strip().split(":")
                parts[k.strip()] = int(v.strip())
            return parts.get("x", 0), parts.get("y", 0)

        pos = _POSITIONS.get(position, _POSITIONS.get("center"))
        x = int(w * pos[0])
        y = int(h * pos[1])

        # Center the layer if we know its size
        if position == "center" and layer_w and layer_h:
            x = (w - layer_w) // 2
            y = (h - layer_h) // 2

        return x, y

    def _next_z() -> int:
        """Get next z_index for a new layer."""
        session = get_active_canvas(user_id)
        if not session:
            return 1
        layers = session["canvas_json"].get("layers", [])
        if not layers:
            return 1
        return max(l.get("z_index", 0) for l in layers) + 1

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def create_canvas(
        title: str = "Novo Canvas",
        format: str = "1080x1080",
        background_type: str = "color",
        background_value: str = "#1A1A2E",
        background_image_url: str = "",
        gradient_colors: str = "",
        palette_primary: str = "#1A1A2E",
        palette_accent: str = "#E94560",
        palette_text_primary: str = "#FFFFFF",
        palette_text_secondary: str = "#D0D0D0",
    ) -> str:
        """
        Cria um novo canvas para composicao de imagem.

        Inicia uma sessao de edicao onde voce pode adicionar textos, imagens, shapes e overlays
        via os outros canvas tools. O canvas funciona como um projeto editavel com layers.

        Args:
            title: Nome do canvas/projeto.
            format: Dimensoes. "1080x1080" (quadrado), "1080x1350" (retrato), "1350x1080" (paisagem), "1080x1920" (stories).
            background_type: "color" (cor solida), "image" (imagem de fundo), "gradient" (gradiente).
            background_value: Cor hex do fundo quando background_type="color". Ex: "#1A1A2E".
            background_image_url: URL da imagem de fundo quando background_type="image".
            gradient_colors: Cores do gradiente separadas por virgula. Ex: "#000000,#333333".
            palette_primary: Cor primaria da paleta (fundo de overlays).
            palette_accent: Cor de destaque/CTA.
            palette_text_primary: Cor principal do texto.
            palette_text_secondary: Cor secundaria do texto.

        Returns:
            Confirmacao com ID do canvas e preview URL.
        """
        clear_image_cache()
        dims = _FORMATS.get(format, (1080, 1080))

        background = {"type": background_type, "value": background_value}
        if background_type == "image" and background_image_url:
            background["image_url"] = background_image_url
        elif background_type == "gradient" and gradient_colors:
            background["gradient"] = {"colors": [c.strip() for c in gradient_colors.split(",")]}

        canvas_doc = {
            "version": 1,
            "width": dims[0],
            "height": dims[1],
            "background": background,
            "layers": [],
            "palette": {
                "primary": palette_primary,
                "accent": palette_accent,
                "text_primary": palette_text_primary,
                "text_secondary": palette_text_secondary,
            },
        }

        session_id = create_canvas_session(user_id, canvas_doc, title=title, fmt=format)
        preview_url = _save_and_render(session_id, canvas_doc)

        return (
            f"Canvas criado! ID: {session_id}\n"
            f"Formato: {format} ({dims[0]}x{dims[1]})\n"
            f"Fundo: {background_type}\n"
            f"Preview: {preview_url}\n\n"
            "Agora voce pode adicionar layers com add_text_layer, add_image_layer, add_shape_layer, etc."
        )

    def add_text_layer(
        text: str,
        position: str = "center",
        font_size: int = 48,
        font_weight: int = 700,
        font_family: str = "Montserrat",
        color: str = "#FFFFFF",
        align: str = "center",
        shadow: bool = True,
        max_width_percent: int = 80,
        max_lines: int = 6,
        canvas_id: str = "",
    ) -> str:
        """
        Adiciona um layer de texto ao canvas.

        Args:
            text: O texto a ser adicionado.
            position: Posicao no canvas. Opcoes: "top-left", "top", "top-center", "top-right",
                      "center-left", "center", "center-right", "bottom-left", "bottom", "bottom-right".
                      Ou coordenadas absolutas: "x:100,y:200".
            font_size: Tamanho maximo da fonte em pixels. O texto sera auto-ajustado pra caber.
            font_weight: Peso da fonte. 400=regular, 700=bold.
            font_family: "Montserrat" (titulos bold) ou "Inter" (texto body).
            color: Cor do texto em hex. Ex: "#FFFFFF".
            align: Alinhamento: "left", "center", "right".
            shadow: Se True, adiciona sombra blur atras do texto.
            max_width_percent: Porcentagem da largura do canvas usada para wrap do texto. 80 = 80%.
            max_lines: Numero maximo de linhas para wrap do texto.
            canvas_id: ID do canvas. Vazio = usa o canvas ativo mais recente.

        Returns:
            Confirmacao com layer ID e preview.
        """
        session, canvas_doc = _get_or_fail_canvas(canvas_id)
        if not session:
            return "Nenhum canvas ativo. Use create_canvas primeiro."

        w = canvas_doc["width"]
        h = canvas_doc["height"]
        layer_w = int(w * max_width_percent / 100)
        x, y = _resolve_position(position, w, h, layer_w, 0)

        layer_id = f"txt_{uuid.uuid4().hex[:6]}"
        shadow_config = None
        if shadow:
            shadow_config = {"color": "#000000", "blur": 8, "offset_x": 0, "offset_y": 4, "opacity": 160}

        layer = {
            "id": layer_id,
            "type": "text",
            "content": text,
            "x": x,
            "y": y,
            "width": layer_w,
            "font_family": font_family,
            "font_size": font_size,
            "font_weight": font_weight,
            "color": color,
            "align": align,
            "shadow": shadow_config,
            "max_lines": max_lines,
            "z_index": _next_z(),
            "visible": True,
        }

        canvas_doc["layers"].append(layer)
        preview_url = _save_and_render(session["id"], canvas_doc)

        return f"Texto adicionado! Layer: {layer_id}\nPreview: {preview_url}"

    def add_image_layer(
        source_url: str,
        position: str = "center",
        width_percent: int = 50,
        height_percent: int = 0,
        border_radius: int = 0,
        border_color: str = "",
        border_width: int = 0,
        opacity: int = 255,
        fit: str = "cover",
        shadow: bool = False,
        canvas_id: str = "",
    ) -> str:
        """
        Adiciona um layer de imagem ao canvas.

        Pode ser uma URL direta, imagem enviada pelo usuario, ou URL de imagem ja gerada.
        Suporta fundo transparente (PNG com alpha), bordas arredondadas e sombra.

        Args:
            source_url: URL da imagem a inserir.
            position: Posicao no canvas. Mesmas opcoes do add_text_layer.
            width_percent: Largura como % do canvas. Ex: 50 = metade do canvas.
            height_percent: Altura como % do canvas. 0 = proporcional a largura.
            border_radius: Raio dos cantos em px. 0=quadrado, 999=circulo.
            border_color: Cor da borda em hex. Vazio = sem borda.
            border_width: Largura da borda em px.
            opacity: Opacidade 0-255. 255=opaco, 0=invisivel.
            fit: "cover" (preenche cortando), "contain" (cabe inteiro), "fill" (estica).
            shadow: Se True, adiciona sombra drop shadow.
            canvas_id: ID do canvas. Vazio = canvas ativo.

        Returns:
            Confirmacao com layer ID e preview.
        """
        session, canvas_doc = _get_or_fail_canvas(canvas_id)
        if not session:
            return "Nenhum canvas ativo. Use create_canvas primeiro."

        w = canvas_doc["width"]
        h = canvas_doc["height"]
        target_w = int(w * width_percent / 100)
        target_h = int(h * height_percent / 100) if height_percent > 0 else target_w
        x, y = _resolve_position(position, w, h, target_w, target_h)

        layer_id = f"img_{uuid.uuid4().hex[:6]}"
        border_config = None
        if border_color and border_width > 0:
            border_config = {"color": border_color, "width": border_width}

        shadow_config = None
        if shadow:
            shadow_config = {"color": "#000000", "blur": 12, "offset_x": 0, "offset_y": 6, "opacity": 100}

        layer = {
            "id": layer_id,
            "type": "image",
            "source_url": source_url,
            "x": x,
            "y": y,
            "width": target_w,
            "height": target_h,
            "border_radius": border_radius,
            "border": border_config,
            "opacity": opacity,
            "fit": fit,
            "shadow": shadow_config,
            "z_index": _next_z(),
            "visible": True,
        }

        canvas_doc["layers"].append(layer)
        preview_url = _save_and_render(session["id"], canvas_doc)

        return f"Imagem adicionada! Layer: {layer_id}\nPreview: {preview_url}"

    def add_icon_layer(
        icon_name: str,
        position: str = "center",
        size: int = 48,
        color: str = "#FFFFFF",
        canvas_id: str = "",
    ) -> str:
        """
        Adiciona um icone SVG ao canvas a partir da biblioteca compartilhada.

        Busca o icone pelo nome na biblioteca de assets compartilhados. Se nao encontrar pelo
        nome exato, faz busca por tags. Icones disponiveis incluem: banknote, chart-bar,
        arrow-right, arrow-left, arrow-up, arrow-down, check, star, heart, share, bookmark,
        camera, mail, phone, user, search, settings, plus, x, clock, trending-up, dollar-sign,
        target, zap, globe, entre outros adicionados pela comunidade.

        Args:
            icon_name: Nome do icone a buscar na biblioteca. Ex: "star", "heart", "chart-bar".
            position: Posicao no canvas.
            size: Tamanho do icone em pixels.
            color: Cor do icone em hex.
            canvas_id: ID do canvas. Vazio = canvas ativo.

        Returns:
            Confirmacao com layer ID e preview, ou lista de icones disponiveis se nao encontrado.
        """
        session, canvas_doc = _get_or_fail_canvas(canvas_id)
        if not session:
            return "Nenhum canvas ativo. Use create_canvas primeiro."

        # Search shared_assets library
        svg_content = ""
        asset_id = None

        # 1. Try exact name match
        asset = get_asset_by_name(icon_name, category="icon")
        if not asset:
            # 2. Try search by name/tags
            results = search_assets(icon_name, category="icon", limit=5)
            if results:
                asset = results[0]

        if asset:
            asset_id = asset["id"]
            svg_content = asset.get("metadata", {}).get("svg_content", "")
            if asset_id:
                try:
                    increment_usage(asset_id)
                except Exception:
                    pass
        else:
            # Not found — suggest alternatives
            alternatives = search_assets("", category="icon", limit=10)
            alt_names = [a["name"] for a in alternatives]
            return (
                f"Icone '{icon_name}' nao encontrado na biblioteca.\n"
                f"Icones disponiveis: {', '.join(alt_names)}\n"
                "Tente outro nome ou busque por tags."
            )

        w = canvas_doc["width"]
        h = canvas_doc["height"]
        x, y = _resolve_position(position, w, h, size, size)

        layer_id = f"icon_{uuid.uuid4().hex[:6]}"
        layer = {
            "id": layer_id,
            "type": "icon",
            "svg_content": svg_content,
            "x": x,
            "y": y,
            "size": size,
            "color": color,
            "z_index": _next_z(),
            "visible": True,
        }

        canvas_doc["layers"].append(layer)
        preview_url = _save_and_render(session["id"], canvas_doc)

        return f"Icone '{icon_name}' adicionado! Layer: {layer_id}\nPreview: {preview_url}"

    def add_shape_layer(
        shape: str = "rect",
        position: str = "top",
        width_percent: int = 100,
        height_px: int = 80,
        fill_color: str = "#E94560",
        stroke_color: str = "",
        stroke_width: int = 0,
        opacity: int = 255,
        border_radius: int = 0,
        direction: str = "right",
        canvas_id: str = "",
    ) -> str:
        """
        Adiciona um shape (forma geometrica) ao canvas.

        Args:
            shape: Tipo da forma: "rect" (retangulo), "circle" (circulo), "line" (linha), "arrow" (seta).
            position: Posicao no canvas.
            width_percent: Largura como % do canvas.
            height_px: Altura em pixels.
            fill_color: Cor de preenchimento em hex.
            stroke_color: Cor do contorno em hex. Vazio = sem contorno.
            stroke_width: Largura do contorno em px.
            opacity: Opacidade 0-255.
            border_radius: Raio dos cantos em px (so para rect).
            direction: Direcao da seta: "up", "down", "left", "right" (so para arrow).
            canvas_id: ID do canvas. Vazio = canvas ativo.

        Returns:
            Confirmacao com layer ID e preview.
        """
        session, canvas_doc = _get_or_fail_canvas(canvas_id)
        if not session:
            return "Nenhum canvas ativo. Use create_canvas primeiro."

        w = canvas_doc["width"]
        h = canvas_doc["height"]
        sw = int(w * width_percent / 100)
        x, y = _resolve_position(position, w, h, sw, height_px)

        layer_id = f"shp_{uuid.uuid4().hex[:6]}"
        layer = {
            "id": layer_id,
            "type": "shape",
            "shape": shape,
            "x": x,
            "y": y,
            "width": sw,
            "height": height_px,
            "fill": fill_color,
            "stroke_color": stroke_color if stroke_color else None,
            "stroke_width": stroke_width,
            "opacity": opacity,
            "border_radius": border_radius,
            "direction": direction,
            "z_index": _next_z(),
            "visible": True,
        }

        canvas_doc["layers"].append(layer)
        preview_url = _save_and_render(session["id"], canvas_doc)

        return f"Shape '{shape}' adicionado! Layer: {layer_id}\nPreview: {preview_url}"

    def add_overlay(
        overlay_type: str = "gradient",
        position: str = "bottom",
        color: str = "#000000",
        opacity: int = 180,
        direction: str = "",
        blur_radius: int = 20,
        border_radius: int = 30,
        canvas_id: str = "",
    ) -> str:
        """
        Adiciona overlay (gradiente ou glassmorphism) ao canvas.

        Gradiente: transicao suave de transparente para a cor. Ideal para texto sobre imagem.
        Glass: efeito de vidro fosco (blur do fundo + cor semi-transparente). Ideal para cards.

        Args:
            overlay_type: "gradient" ou "glass".
            position: Regiao: "top", "bottom", "full", "center-card".
            color: Cor do overlay em hex.
            opacity: Opacidade maxima 0-255.
            direction: Direcao do gradiente: "top", "bottom", "full". Vazio = usa position.
            blur_radius: Raio do blur (so para glass).
            border_radius: Raio dos cantos (so para glass).
            canvas_id: ID do canvas. Vazio = canvas ativo.

        Returns:
            Confirmacao com layer ID e preview.
        """
        session, canvas_doc = _get_or_fail_canvas(canvas_id)
        if not session:
            return "Nenhum canvas ativo. Use create_canvas primeiro."

        layer_id = f"ovl_{uuid.uuid4().hex[:6]}"
        layer = {
            "id": layer_id,
            "type": "overlay",
            "overlay_type": overlay_type,
            "position": position,
            "color": color,
            "opacity": opacity,
            "direction": direction or position,
            "blur_radius": blur_radius,
            "border_radius": border_radius,
            "z_index": _next_z(),
            "visible": True,
        }

        canvas_doc["layers"].append(layer)
        preview_url = _save_and_render(session["id"], canvas_doc)

        return f"Overlay '{overlay_type}' adicionado! Layer: {layer_id}\nPreview: {preview_url}"

    def move_layer(
        layer_id: str = "last",
        direction: str = "",
        amount_px: int = 50,
        new_position: str = "",
        canvas_id: str = "",
    ) -> str:
        """
        Move um layer no canvas.

        Args:
            layer_id: ID do layer a mover. "last" = ultimo adicionado.
            direction: Direcao relativa: "up", "down", "left", "right".
            amount_px: Quantidade de pixels para mover na direcao.
            new_position: Nova posicao absoluta. Ex: "center", "top-left", ou "x:100,y:200".
                         Se fornecido, ignora direction/amount_px.
            canvas_id: ID do canvas. Vazio = canvas ativo.

        Returns:
            Confirmacao com preview.
        """
        session, canvas_doc = _get_or_fail_canvas(canvas_id)
        if not session:
            return "Nenhum canvas ativo."

        layer = _find_layer(canvas_doc, layer_id)
        if not layer:
            return f"Layer '{layer_id}' nao encontrado."

        w = canvas_doc["width"]
        h = canvas_doc["height"]

        if new_position:
            lw = layer.get("width", 0)
            lh = layer.get("height", 0)
            layer["x"], layer["y"] = _resolve_position(new_position, w, h, lw, lh)
        else:
            if direction == "up":
                layer["y"] = max(0, layer.get("y", 0) - amount_px)
            elif direction == "down":
                layer["y"] = layer.get("y", 0) + amount_px
            elif direction == "left":
                layer["x"] = max(0, layer.get("x", 0) - amount_px)
            elif direction == "right":
                layer["x"] = layer.get("x", 0) + amount_px

        preview_url = _save_and_render(session["id"], canvas_doc)
        return f"Layer '{layer['id']}' movido! Preview: {preview_url}"

    def update_layer(
        layer_id: str = "last",
        text: str = "",
        font_size: int = 0,
        color: str = "",
        opacity: int = -1,
        visible: bool = True,
        fill_color: str = "",
        source_url: str = "",
        canvas_id: str = "",
    ) -> str:
        """
        Atualiza propriedades de um layer existente.

        Passe apenas os campos que deseja mudar. Campos vazios/zero sao ignorados.

        Args:
            layer_id: ID do layer. "last" = ultimo adicionado.
            text: Novo texto (para layers de texto).
            font_size: Novo tamanho de fonte.
            color: Nova cor (texto ou icone).
            opacity: Nova opacidade 0-255. -1 = nao alterar.
            visible: Se False, esconde o layer sem remover.
            fill_color: Nova cor de preenchimento (para shapes).
            source_url: Nova URL de imagem (para layers de imagem).
            canvas_id: ID do canvas. Vazio = canvas ativo.

        Returns:
            Confirmacao com preview.
        """
        session, canvas_doc = _get_or_fail_canvas(canvas_id)
        if not session:
            return "Nenhum canvas ativo."

        layer = _find_layer(canvas_doc, layer_id)
        if not layer:
            return f"Layer '{layer_id}' nao encontrado."

        if text:
            layer["content"] = text
        if font_size > 0:
            layer["font_size"] = font_size
        if color:
            layer["color"] = color
        if opacity >= 0:
            layer["opacity"] = opacity
        if fill_color:
            layer["fill"] = fill_color
        if source_url:
            layer["source_url"] = source_url
        layer["visible"] = visible

        preview_url = _save_and_render(session["id"], canvas_doc)
        return f"Layer '{layer['id']}' atualizado! Preview: {preview_url}"

    def remove_layer(
        layer_id: str = "last",
        canvas_id: str = "",
    ) -> str:
        """
        Remove um layer do canvas.

        Args:
            layer_id: ID do layer a remover. "last" = ultimo adicionado.
            canvas_id: ID do canvas. Vazio = canvas ativo.

        Returns:
            Confirmacao com preview.
        """
        session, canvas_doc = _get_or_fail_canvas(canvas_id)
        if not session:
            return "Nenhum canvas ativo."

        layers = canvas_doc.get("layers", [])
        if not layers:
            return "Canvas nao tem layers."

        if layer_id == "last":
            removed = layers.pop()
        else:
            idx = next((i for i, l in enumerate(layers) if l["id"] == layer_id), None)
            if idx is None:
                return f"Layer '{layer_id}' nao encontrado."
            removed = layers.pop(idx)

        preview_url = _save_and_render(session["id"], canvas_doc)
        return f"Layer '{removed['id']}' removido! Preview: {preview_url}"

    def render_canvas_tool(
        canvas_id: str = "",
    ) -> str:
        """
        Renderiza o canvas atual e envia o preview final.

        Use apos fazer varias alteracoes para ver o resultado ou quando o usuario pedir
        para ver como esta ficando.

        Args:
            canvas_id: ID do canvas. Vazio = canvas ativo.

        Returns:
            URL da imagem renderizada.
        """
        session, canvas_doc = _get_or_fail_canvas(canvas_id)
        if not session:
            return "Nenhum canvas ativo."

        preview_url = _save_and_render(session["id"], canvas_doc)
        return f"Canvas renderizado! Preview: {preview_url}"

    def apply_template(
        template: str,
        title: str = "",
        body: str = "",
        cta_text: str = "",
        slide_number: int = 1,
        total_slides: int = 5,
        canvas_id: str = "",
    ) -> str:
        """
        Aplica um template pre-definido ao canvas, adicionando layers automaticamente.

        Os templates replicam os layouts profissionais de carrosseis do Instagram:
        - "capa": Gradiente no terco inferior + accent bar + titulo grande. Ideal para slide 1.
        - "conteudo": Card semi-transparente com numero do slide, titulo e body. Ideal para slides centrais.
        - "fechamento": Card com CTA centralizado. Ideal para ultimo slide.
        - "topbar": Barra escura no topo para @username ou titulo de serie.

        Apos aplicar o template, o usuario pode editar individualmente cada layer gerado.

        Args:
            template: "capa", "conteudo", "fechamento", "topbar".
            title: Texto do titulo.
            body: Texto complementar/corpo.
            cta_text: Texto do CTA (so para fechamento).
            slide_number: Numero do slide (para conteudo, exibe indicador).
            total_slides: Total de slides no carrossel.
            canvas_id: ID do canvas. Vazio = canvas ativo.

        Returns:
            Confirmacao com layers criados e preview.
        """
        session, canvas_doc = _get_or_fail_canvas(canvas_id)
        if not session:
            return "Nenhum canvas ativo. Use create_canvas primeiro."

        w = canvas_doc["width"]
        h = canvas_doc["height"]
        palette = canvas_doc.get("palette", {})
        primary = palette.get("primary", "#1A1A2E")
        accent = palette.get("accent", "#E94560")
        text_primary = palette.get("text_primary", "#FFFFFF")
        text_secondary = palette.get("text_secondary", "#D0D0D0")

        added_layers = []

        if template == "capa":
            # Gradient overlay bottom 50%
            added_layers.append({
                "id": f"ovl_{uuid.uuid4().hex[:6]}",
                "type": "overlay",
                "overlay_type": "gradient",
                "position": "bottom",
                "color": primary,
                "opacity": 210,
                "direction": "bottom",
                "height": int(h * 0.55),
                "z_index": _next_z(),
                "visible": True,
            })
            # Accent bar
            bar_y = int(h * 0.56)
            added_layers.append({
                "id": f"shp_{uuid.uuid4().hex[:6]}",
                "type": "shape",
                "shape": "rect",
                "x": w // 2 - 30,
                "y": bar_y,
                "width": 60,
                "height": 5,
                "fill": accent,
                "border_radius": 3,
                "opacity": 255,
                "z_index": _next_z() + 1,
                "visible": True,
            })
            # Title
            if title:
                added_layers.append({
                    "id": f"txt_{uuid.uuid4().hex[:6]}",
                    "type": "text",
                    "content": title,
                    "x": int(w * 0.08),
                    "y": bar_y + 18,
                    "width": int(w * 0.84),
                    "font_family": "Montserrat",
                    "font_size": 90,
                    "font_weight": 700,
                    "color": text_primary,
                    "align": "center",
                    "shadow": {"color": "#000000", "blur": 8, "offset_x": 0, "offset_y": 4, "opacity": 160},
                    "max_lines": 3,
                    "z_index": _next_z() + 2,
                    "visible": True,
                })
            # Subtitle
            if body:
                added_layers.append({
                    "id": f"txt_{uuid.uuid4().hex[:6]}",
                    "type": "text",
                    "content": body,
                    "x": int(w * 0.08),
                    "y": int(h * 0.82),
                    "width": int(w * 0.84),
                    "font_family": "Inter",
                    "font_size": 32,
                    "font_weight": 400,
                    "color": text_secondary,
                    "align": "center",
                    "shadow": None,
                    "max_lines": 2,
                    "z_index": _next_z() + 3,
                    "visible": True,
                })

        elif template == "conteudo":
            # Glass card
            margin = int(w * 0.06)
            added_layers.append({
                "id": f"ovl_{uuid.uuid4().hex[:6]}",
                "type": "overlay",
                "overlay_type": "glass",
                "position": "center-card",
                "color": primary,
                "opacity": 160,
                "blur_radius": 20,
                "border_radius": 30,
                "z_index": _next_z(),
                "visible": True,
            })
            # Slide number
            added_layers.append({
                "id": f"txt_{uuid.uuid4().hex[:6]}",
                "type": "text",
                "content": f"{slide_number:02d}/{total_slides:02d}",
                "x": margin + int(w * 0.08),
                "y": int(h * 0.15),
                "width": int(w * 0.3),
                "font_family": "Inter",
                "font_size": 24,
                "font_weight": 500,
                "color": text_secondary,
                "align": "left",
                "shadow": None,
                "max_lines": 1,
                "z_index": _next_z() + 1,
                "visible": True,
            })
            # Accent bar
            added_layers.append({
                "id": f"shp_{uuid.uuid4().hex[:6]}",
                "type": "shape",
                "shape": "rect",
                "x": margin + int(w * 0.08),
                "y": int(h * 0.20),
                "width": 50,
                "height": 4,
                "fill": accent,
                "border_radius": 2,
                "opacity": 255,
                "z_index": _next_z() + 2,
                "visible": True,
            })
            # Title
            if title:
                added_layers.append({
                    "id": f"txt_{uuid.uuid4().hex[:6]}",
                    "type": "text",
                    "content": title,
                    "x": margin + int(w * 0.08),
                    "y": int(h * 0.23),
                    "width": int(w * 0.72),
                    "font_family": "Montserrat",
                    "font_size": 56,
                    "font_weight": 700,
                    "color": text_primary,
                    "align": "left",
                    "shadow": None,
                    "max_lines": 3,
                    "z_index": _next_z() + 3,
                    "visible": True,
                })
            # Body
            if body:
                added_layers.append({
                    "id": f"txt_{uuid.uuid4().hex[:6]}",
                    "type": "text",
                    "content": body,
                    "x": margin + int(w * 0.08),
                    "y": int(h * 0.48),
                    "width": int(w * 0.72),
                    "font_family": "Inter",
                    "font_size": 32,
                    "font_weight": 400,
                    "color": text_secondary,
                    "align": "left",
                    "shadow": None,
                    "max_lines": 6,
                    "z_index": _next_z() + 4,
                    "visible": True,
                })

        elif template == "fechamento":
            # Glass card
            added_layers.append({
                "id": f"ovl_{uuid.uuid4().hex[:6]}",
                "type": "overlay",
                "overlay_type": "glass",
                "position": "center-card",
                "color": primary,
                "opacity": 170,
                "blur_radius": 20,
                "border_radius": 30,
                "z_index": _next_z(),
                "visible": True,
            })
            # Main CTA text
            main_text = cta_text or title
            if main_text:
                added_layers.append({
                    "id": f"txt_{uuid.uuid4().hex[:6]}",
                    "type": "text",
                    "content": main_text,
                    "x": int(w * 0.14),
                    "y": int(h * 0.30),
                    "width": int(w * 0.72),
                    "font_family": "Montserrat",
                    "font_size": 72,
                    "font_weight": 700,
                    "color": text_primary,
                    "align": "center",
                    "shadow": {"color": "#000000", "blur": 6, "offset_x": 0, "offset_y": 4, "opacity": 160},
                    "max_lines": 3,
                    "z_index": _next_z() + 1,
                    "visible": True,
                })
            # Accent bar
            added_layers.append({
                "id": f"shp_{uuid.uuid4().hex[:6]}",
                "type": "shape",
                "shape": "rect",
                "x": w // 2 - 40,
                "y": int(h * 0.55),
                "width": 80,
                "height": 5,
                "fill": accent,
                "border_radius": 3,
                "opacity": 255,
                "z_index": _next_z() + 2,
                "visible": True,
            })
            # Body
            if body:
                added_layers.append({
                    "id": f"txt_{uuid.uuid4().hex[:6]}",
                    "type": "text",
                    "content": body,
                    "x": int(w * 0.14),
                    "y": int(h * 0.60),
                    "width": int(w * 0.72),
                    "font_family": "Inter",
                    "font_size": 30,
                    "font_weight": 400,
                    "color": text_secondary,
                    "align": "center",
                    "shadow": None,
                    "max_lines": 3,
                    "z_index": _next_z() + 3,
                    "visible": True,
                })

        elif template == "topbar":
            # Dark bar at top
            bar_h = int(h * 0.07)
            added_layers.append({
                "id": f"shp_{uuid.uuid4().hex[:6]}",
                "type": "shape",
                "shape": "rect",
                "x": 0,
                "y": 0,
                "width": w,
                "height": bar_h,
                "fill": primary,
                "opacity": 200,
                "border_radius": 0,
                "z_index": _next_z(),
                "visible": True,
            })
            if title:
                added_layers.append({
                    "id": f"txt_{uuid.uuid4().hex[:6]}",
                    "type": "text",
                    "content": title,
                    "x": int(w * 0.04),
                    "y": int(bar_h * 0.2),
                    "width": int(w * 0.92),
                    "font_family": "Inter",
                    "font_size": 24,
                    "font_weight": 500,
                    "color": text_secondary,
                    "align": "left",
                    "shadow": None,
                    "max_lines": 1,
                    "z_index": _next_z() + 1,
                    "visible": True,
                })

        # Add all layers
        for layer in added_layers:
            canvas_doc["layers"].append(layer)

        preview_url = _save_and_render(session["id"], canvas_doc)
        layer_ids = [l["id"] for l in added_layers]

        return (
            f"Template '{template}' aplicado! {len(added_layers)} layers criados.\n"
            f"Layers: {', '.join(layer_ids)}\n"
            f"Preview: {preview_url}\n\n"
            "Voce pode editar qualquer layer individualmente com update_layer ou move_layer."
        )

    def _find_layer(canvas_doc: dict, layer_id: str) -> Optional[dict]:
        """Find layer by ID or 'last'."""
        layers = canvas_doc.get("layers", [])
        if not layers:
            return None
        if layer_id == "last":
            return layers[-1]
        return next((l for l in layers if l["id"] == layer_id), None)

    # ------------------------------------------------------------------
    # Return all tools
    # ------------------------------------------------------------------
    return [
        create_canvas,
        add_text_layer,
        add_image_layer,
        add_icon_layer,
        add_shape_layer,
        add_overlay,
        move_layer,
        update_layer,
        remove_layer,
        render_canvas_tool,
        apply_template,
    ]

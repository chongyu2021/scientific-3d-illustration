"""Two-dimensional layout layer.

All text (especially CJK), headers, legends, arrows, callouts and zoom-insets
are drawn here with Pillow, never inside Blender.  That keeps typography crisp
and independent from the 3D render resolution.
"""

from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import s3d_core as core
import s3d_fonts

Vec2 = Tuple[float, float]


# --------------------------------------------------------------------------
# colour helpers
# --------------------------------------------------------------------------
def rgb(value, alpha: Optional[int] = None):
    if isinstance(value, (list, tuple)):
        r, g, b = int(value[0]), int(value[1]), int(value[2])
        a = int(value[3]) if len(value) > 3 else 255
        return (r, g, b, a if alpha is None else alpha)
    s = str(value).lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return (r, g, b, 255 if alpha is None else alpha)


def mix(c1, c2, t):
    a = rgb(c1)
    b = rgb(c2)
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(4))


# --------------------------------------------------------------------------
# font cache
# --------------------------------------------------------------------------
class Fonts:
    def __init__(self, scale: float):
        self.scale = scale
        self.regular_path = s3d_fonts.find_font(False)
        self.bold_path = s3d_fonts.find_font(True) or self.regular_path
        self.cache: Dict[Tuple[int, bool], ImageFont.FreeTypeFont] = {}

    def get(self, size: float, bold: bool = False):
        px = max(8, int(round(size * self.scale)))
        key = (px, bold)
        if key not in self.cache:
            self.cache[key] = ImageFont.truetype(self.bold_path if bold else self.regular_path, px)
        return self.cache[key]


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def linear_gradient(size: Tuple[int, int], c0, c1, horizontal=False) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(img)
    n = w if horizontal else h
    for i in range(n):
        t = i / max(1, n - 1)
        c = mix(c0, c1, t)
        if horizontal:
            d.line([(i, 0), (i, h)], fill=c)
        else:
            d.line([(0, i), (w, i)], fill=c)
    return img


def rounded_mask(size: Tuple[int, int], radius: float) -> Image.Image:
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    return m


def paste_rounded(base: Image.Image, fill_img: Image.Image, box, radius: float):
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    layer = fill_img.resize((w, h)) if fill_img.size != (w, h) else fill_img.copy()
    base.paste(layer, (x0, y0), rounded_mask((w, h), radius))


def draw_text(draw, xy, text, font, fill, anchor="la", spacing=0):
    if spacing:
        # manual letter spacing (used sparingly for latin captions)
        x, y = xy
        for ch in text:
            draw.text((x, y), ch, font=font, fill=fill, anchor=anchor)
            x += draw.textlength(ch, font=font) + spacing
        return
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def text_size(draw, text, font) -> Tuple[int, int]:
    b = draw.textbbox((0, 0), text, font=font)
    return b[2] - b[0], b[3] - b[1]


def dashed_line(draw, pts, fill, width, dash=12, gap=8):
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 1e-6:
            continue
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        pos = 0.0
        while pos < length:
            end = min(pos + dash, length)
            draw.line([(x0 + ux * pos, y0 + uy * pos), (x0 + ux * end, y0 + uy * end)],
                      fill=fill, width=width)
            pos = end + gap


def dashed_rect(draw, box, fill, width, dash=12, gap=8):
    x0, y0, x1, y1 = box
    dashed_line(draw, [(x0, y0), (x1, y0)], fill, width, dash, gap)
    dashed_line(draw, [(x1, y0), (x1, y1)], fill, width, dash, gap)
    dashed_line(draw, [(x1, y1), (x0, y1)], fill, width, dash, gap)
    dashed_line(draw, [(x0, y1), (x0, y0)], fill, width, dash, gap)


def radial_glow(size, center, radius, color, peak=40):
    w, h = size
    small = Image.new("L", (max(8, w // 8), max(8, h // 8)), 0)
    d = ImageDraw.Draw(small)
    cx, cy = center[0] / 8, center[1] / 8
    r = radius / 8
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=peak)
    small = small.filter(ImageFilter.GaussianBlur(r * 0.35))
    small = small.resize((w, h), Image.LANCZOS)
    layer = Image.new("RGBA", (w, h), rgb(color)[:3] + (0,))
    layer.putalpha(small)
    return layer


# --------------------------------------------------------------------------
# legend icons
# --------------------------------------------------------------------------
def draw_icon(draw: ImageDraw.ImageDraw, kind: str, cx: float, cy: float, s: float,
              palette: Dict[str, Any], style: Dict[str, Any]):
    blue = palette.get("static", "#1F5FBF")
    gray = "#AEB4BD"
    if kind in ("voxel", "voxel_dim", "block", "swatch_blue", "swatch_gray"):
        w = s * 0.92
        h = s * 0.78
        top = [(cx, cy - h * 0.52), (cx + w * 0.5, cy - h * 0.22),
               (cx, cy + h * 0.08), (cx - w * 0.5, cy - h * 0.22)]
        left = [(cx - w * 0.5, cy - h * 0.22), (cx, cy + h * 0.08),
                (cx, cy + h * 0.52), (cx - w * 0.5, cy + h * 0.24)]
        right = [(cx + w * 0.5, cy - h * 0.22), (cx, cy + h * 0.08),
                 (cx, cy + h * 0.52), (cx + w * 0.5, cy + h * 0.24)]
        if kind in ("block", "swatch_gray"):
            draw.polygon(top, fill=rgb("#D3D7DD"), outline=rgb("#6E7684"), width=max(1, int(s * 0.035)))
            draw.polygon(left, fill=rgb("#B6BCC5"), outline=rgb("#6E7684"), width=max(1, int(s * 0.035)))
            draw.polygon(right, fill=rgb("#9BA2AC"), outline=rgb("#6E7684"), width=max(1, int(s * 0.035)))
        else:
            fill_a = 120 if kind != "voxel_dim" else 60
            edge = rgb("#7FB4E2" if kind != "voxel_dim" else "#A9CCEA")
            for poly, sh in ((top, "#E4F2FE"), (left, "#CFE7FD"), (right, "#BCD9F6")):
                draw.polygon(poly, fill=rgb(sh, fill_a), outline=edge,
                             width=max(1, int(s * 0.03)))
        return
    if kind in ("point_static", "point_dynamic", "point_warn", "dot"):
        color = {"point_static": blue, "point_dynamic": palette.get("dynamic", "#E12B2B"),
                 "point_warn": "#F08C00", "dot": palette.get("muted", "#4B6076")}[kind]
        r = s * 0.30
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=rgb(color))
        hl = r * 0.34
        draw.ellipse([cx - r * 0.42 - hl, cy - r * 0.42 - hl,
                      cx - r * 0.42 + hl, cy - r * 0.42 + hl],
                     fill=(255, 255, 255, 150))
        return
    if kind in ("ray", "ray_light"):
        color = "#1668C7" if kind == "ray" else "#8FB9E2"
        x0, x1 = cx - s * 0.46, cx + s * 0.30
        draw.line([(x0, cy + s * 0.12), (x1, cy - s * 0.12)], fill=rgb(color),
                  width=max(2, int(s * 0.09)))
        head = s * 0.22
        draw.polygon([(x1 + head * 0.7, cy - s * 0.17),
                      (x1 - head * 0.1, cy - s * 0.30),
                      (x1 - head * 0.05, cy - s * 0.02)],
                     fill=rgb(color))
        return
    if kind == "path":
        draw.line([(cx - s * 0.45, cy + s * 0.25), (cx - s * 0.1, cy - s * 0.2),
                   (cx + s * 0.4, cy + s * 0.1)], fill=rgb("#154C8C"),
                  width=max(2, int(s * 0.07)), joint="curve")
        return
    if kind == "robot":
        draw.rounded_rectangle([cx - s * 0.32, cy - s * 0.1, cx + s * 0.32, cy + s * 0.3],
                               radius=s * 0.08, fill=rgb("#F2F5F8"), outline=rgb("#6C7A88"),
                               width=max(1, int(s * 0.04)))
        draw.ellipse([cx - s * 0.12, cy - s * 0.32, cx + s * 0.12, cy - s * 0.08],
                     fill=rgb("#5E93C9"))
        return
    # fallback: plain dot
    r = s * 0.2
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=rgb(blue))


# --------------------------------------------------------------------------
# panel layout
# --------------------------------------------------------------------------
def panel_layout(figure: Dict[str, Any], panel: Dict[str, Any]) -> Dict[str, Any]:
    return core.panel_layout(figure, panel)


def _fit_box(box, size):
    """contain-fit an image of `size` inside `box`, returns (x0,y0,x1,y1)."""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    iw, ih = size
    s = min(bw / iw, bh / ih)
    w, h = iw * s, ih * s
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


# --------------------------------------------------------------------------
# main composer
# --------------------------------------------------------------------------
def compose(scene: Dict[str, Any], out_dir: str, panels_dir: Optional[str] = None,
            transparent_page: bool = False, preview: bool = False) -> str:
    style = core.resolve_style(scene)
    palette = style.get("palette", {})
    figure = scene.get("figure", {})
    W, H = figure.get("size", [3600, 1300])
    W, H = int(W), int(H)
    u = W / 3600.0
    fonts = Fonts(u * (0.5 if preview else 1.0))

    panels_dir = panels_dir or os.path.join(out_dir, "panels")
    page = figure.get("background", palette.get("page", "#FFFFFF"))
    canvas = Image.new("RGBA", (W, H), (255, 255, 255, 0) if transparent_page else rgb(page))

    draw = ImageDraw.Draw(canvas)

    # ---------- background wash ----------
    if not transparent_page:
        wash = radial_glow((W, H), (W * 0.5, H * 0.1), W * 0.55,
                           figure.get("wash_color", "#DCEBFA"), int(figure.get("wash_peak", 26)))
        canvas.alpha_composite(wash)

    # ---------- title ----------
    title = figure.get("title") or {}
    if title.get("text"):
        size = float(title.get("size", 84))
        font = fonts.get(size, bold=True)
        color = rgb(title.get("color", palette.get("ink", "#10243C")))
        y = float(title.get("y", 70))
        draw.text((W / 2, y), title["text"], font=font, fill=color, anchor="ma")
        if title.get("subtitle"):
            sfont = fonts.get(34, bold=False)
            draw.text((W / 2, y + size * 1.18), title["subtitle"], font=sfont,
                      fill=rgb(palette.get("muted", "#4B6076")), anchor="ma")

    panel_by_id = {p.get("id", p.get("view")): p for p in figure.get("panels", [])}

    # ---------- panel bodies ----------
    for panel in figure.get("panels", []):
        if panel.get("hidden"):
            continue
        x0, y0, x1, y1 = panel["rect"]
        radius = float(panel.get("radius", 20)) * u
        lay = panel_layout(figure, panel)

        # shadow
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sh)
        off = float(panel.get("shadow_offset", 10)) * u
        sd.rounded_rectangle([x0, y0 + off, x1, y1 + off], radius=radius,
                             fill=rgb(panel.get("shadow_color", "#0B2B4D"), 60))
        sh = sh.filter(ImageFilter.GaussianBlur(float(panel.get("shadow_blur", 16)) * u))
        canvas.alpha_composite(sh)

        # body
        bg = panel.get("background", palette.get("panel_bg", ["#FBFDFF", "#EFF7FF"]))
        if isinstance(bg, str):
            bg = [bg, bg]
        paste_rounded(canvas, linear_gradient((max(1, int(x1 - x0)), max(1, int(y1 - y0))), bg[0], bg[1]),
                      [x0, y0, x1, y1], radius)

        # header
        header = panel.get("header") or {}
        hh = lay["header_h"]
        if header and hh > 0:
            hc = header.get("colors", palette.get("header", ["#123F73", "#1E5FA8"]))
            hw, hh_px = max(1, int(x1 - x0)), max(1, int(hh))
            grad = linear_gradient((hw, hh_px), hc[0], hc[1], horizontal=True)
            hmask = Image.new("L", (hw, hh_px), 0)
            hd = ImageDraw.Draw(hmask)
            hd.rounded_rectangle([0, 0, hw - 1, hh_px - 1], radius=radius, fill=255)
            hd.rectangle([0, max(0, hh_px - radius), hw - 1, hh_px - 1], fill=255)
            canvas.paste(grad, (int(x0), int(y0)), hmask)
            hfont = fonts.get(float(header.get("size", 40)), bold=True)
            hcolor = rgb(header.get("color", "#FFFFFF"))
            draw.text(((x0 + x1) / 2, y0 + hh / 2 - 1 * u), header.get("text", ""),
                      font=hfont, fill=hcolor, anchor="mm")

        # border
        draw.rounded_rectangle([x0, y0, x1, y1], radius=radius,
                               outline=rgb(panel.get("border", palette.get("panel_border", "#9CC3E8")),
                                           panel.get("border_alpha", 255)),
                               width=max(1, int(float(panel.get("border_width", 2.2)) * u)))

        # render
        view = panel.get("view")
        img_path = os.path.join(panels_dir, "panel_%s.png" % view)
        render_box = _fit_box(lay["render_rect"], (1, 1))
        if os.path.exists(img_path):
            # soft interior glow behind the model
            glow = radial_glow((int(x1 - x0), int(y1 - y0)),
                               ((x0 + x1) / 2 - x0, (y0 + y1) / 2 - y0),
                               (x1 - x0) * 0.45, "#CFE8FF", 40)
            canvas.alpha_composite(glow, (int(x0), int(y0)))
            im = Image.open(img_path).convert("RGBA")
            render_box = _fit_box(lay["render_rect"], im.size)
            box = [int(round(v)) for v in render_box]
            im = im.resize((max(1, box[2] - box[0]), max(1, box[3] - box[1])), Image.LANCZOS)
            if not transparent_page:
                canvas.alpha_composite(im, (box[0], box[1]))
            else:
                canvas.alpha_composite(im, (box[0], box[1]))

        # legend
        legend = panel.get("legend") or {}
        items = legend.get("items", [])
        if items and lay["legend_rect"]:
            lx0, ly0, lx1, ly1 = lay["legend_rect"]
            row_h = lay["legend_row_height"] * u
            isz = float(legend.get("icon_size", 34)) * u
            fsz = float(legend.get("size", 28))
            font = fonts.get(fsz)
            fcolor = rgb(legend.get("color", palette.get("ink", "#10243C")))
            cols = int(legend.get("columns", 1))
            per_col = int(math.ceil(len(items) / cols))
            col_w = (lx1 - lx0) / cols
            for i, it in enumerate(items):
                col = i // per_col
                row = i % per_col
                cx = lx0 + col * col_w + isz * 0.75
                cy = ly0 + row * row_h + row_h * 0.42
                draw_icon(draw, it.get("icon", "dot"), cx, cy, isz, palette, style)
                draw.text((cx + isz * 0.85, cy), it.get("text", ""), font=font,
                          fill=fcolor, anchor="lm")

        # footer note
        note = panel.get("note")
        if note:
            nf = fonts.get(float(note.get("size", 24)))
            draw.text(((x0 + x1) / 2, y1 - lay["pad"] * 0.7),
                      note.get("text", ""), font=nf,
                      fill=rgb(note.get("color", palette.get("muted", "#4B6076"))), anchor="md")

    # ---------- arrows between panels ----------
    arrows = figure.get("arrows", [])
    if arrows == "auto":
        arrows = []
        ps = [p for p in figure.get("panels", []) if not p.get("hidden") and not p.get("overlay")]
        for a, b in zip(ps, ps[1:]):
            ya = (a["rect"][1] + a["rect"][3]) / 2 + float(a["rect"][3] - a["rect"][1]) * 0.02
            arrows.append({"from": [a["rect"][2] + 6 * u, ya], "to": [b["rect"][0] - 6 * u, ya]})
    arrows = list(arrows) + list(figure.get("extra_arrows", []))
    for ar in arrows:
        x0, y0 = ar["from"]
        x1, y1 = ar["to"]
        color = rgb(ar.get("color", palette.get("accent", "#154C8C")))
        hw = float(ar.get("width", 62)) * u / 2.0
        head = float(ar.get("head", 34)) * u
        notch = float(ar.get("notch", 16)) * u
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 1e-6:
            continue
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        bx, by = x1 - ux * head, y1 - uy * head
        poly = [(x0 + px * hw, y0 + py * hw),
                (bx + px * hw, by + py * hw),
                (x1, y1),
                (bx - px * hw, by - py * hw),
                (x0 - px * hw, y0 - py * hw),
                (x0 + ux * notch, y0 + uy * notch)]
        draw.polygon(poly, fill=color)

    # ---------- zoom links (dashed) ----------
    for link in figure.get("zoom_links", []):
        src = panel_by_id.get(link.get("from_panel"))
        dst = panel_by_id.get(link.get("to_panel"))
        if not src or not dst:
            continue
        sview = next((v for v in scene.get("views", []) if v["id"] == src.get("view")), None)
        if not sview:
            continue
        src_img = os.path.join(panels_dir, "panel_%s.png" % src.get("view"))
        if not os.path.exists(src_img):
            continue
        size = Image.open(src_img).size
        box = _fit_box(panel_layout(figure, src)["render_rect"], size)
        corners = link["source_box"]
        lo, hi = corners
        pts = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
        proj = [core.project_point(p, sview.get("camera", {}), size) for p in pts]
        sx0 = min(p[0] for p in proj) / size[0]
        sx1 = max(p[0] for p in proj) / size[0]
        sy0 = min(p[1] for p in proj) / size[1]
        sy1 = max(p[1] for p in proj) / size[1]
        rx0 = box[0] + sx0 * (box[2] - box[0])
        rx1 = box[0] + sx1 * (box[2] - box[0])
        ry0 = box[1] + sy0 * (box[3] - box[1])
        ry1 = box[1] + sy1 * (box[3] - box[1])
        color = rgb(link.get("color", palette.get("warn", "#D92B2B")))
        lw = max(1, int(float(link.get("width", 2.6)) * u))
        dashed_rect(draw, [rx0, ry0, rx1, ry1], color, lw,
                    dash=float(link.get("dash", 14)) * u, gap=float(link.get("gap", 9)) * u)
        d = dst["rect"]
        # connect nearest corners of the two rectangles
        pairs = [((rx1, ry0), (d[0], d[1])), ((rx1, ry1), (d[0], d[3]))]
        for a, b in pairs:
            dashed_line(draw, [a, b], color, max(1, lw - 1),
                        dash=float(link.get("dash", 14)) * u, gap=float(link.get("gap", 9)) * u)

    # ---------- callouts ----------
    for co in figure.get("callouts", []):
        panel = panel_by_id.get(co.get("panel"))
        if not panel:
            continue
        view = next((v for v in scene.get("views", []) if v["id"] == panel.get("view")), None)
        if not view:
            continue
        img_path = os.path.join(panels_dir, "panel_%s.png" % panel.get("view"))
        if not os.path.exists(img_path):
            continue
        size = Image.open(img_path).size
        box = _fit_box(panel_layout(figure, panel)["render_rect"], size)
        target_px = core.project_point(co["target"], view.get("camera", {}), size)
        tx = box[0] + target_px[0] / size[0] * (box[2] - box[0])
        ty = box[1] + target_px[1] / size[1] * (box[3] - box[1])

        fsz = float(co.get("size", 27))
        font = fonts.get(fsz, bold=bool(co.get("bold", False)))
        tw, th = text_size(draw, co.get("text", ""), font)
        padx = float(co.get("pad_x", 16)) * u
        pady = float(co.get("pad_y", 9)) * u
        w = tw + 2 * padx
        h = th + 2 * pady + 4 * u

        rr = box
        ax, ay = co.get("at", [0.5, 0.5])
        lx = rr[0] + ax * (rr[2] - rr[0])
        ly = rr[1] + ay * (rr[3] - rr[1])
        align = co.get("align", "left")
        if align == "center":
            bx0 = lx - w / 2
        elif align == "right":
            bx0 = lx - w
        else:
            bx0 = lx
        by0 = ly
        bx1, by1 = bx0 + w, by0 + h

        # leader line from nearest box edge midpoint
        if co.get("leader", True):
            edge_pts = [(bx0, by0 + h / 2), (bx1, by0 + h / 2),
                        (bx0 + w / 2, by0), (bx0 + w / 2, by1)]
            ex, ey = min(edge_pts, key=lambda p: (p[0] - tx) ** 2 + (p[1] - ty) ** 2)
            kink = co.get("kink")
            lcolor = rgb(co.get("line_color", palette.get("ink", "#10243C")))
            lw = max(1, int(float(co.get("line_width", 2.0)) * u))
            if kink:
                kx, ky = kink
                dashed_line(draw, [(ex, ey), (kx, ky), (tx, ty)], lcolor, lw, dash=10 * u, gap=6 * u)
            else:
                dashed_line(draw, [(ex, ey), (tx, ty)], lcolor, lw, dash=10 * u, gap=6 * u)
            r = float(co.get("dot", 4.5)) * u
            draw.ellipse([tx - r, ty - r, tx + r, ty + r], fill=lcolor)

        radius = float(co.get("radius", 10)) * u
        draw.rounded_rectangle([bx0, by0, bx1, by1], radius=radius,
                               fill=rgb(co.get("fill", "#FFFFFF"), co.get("fill_alpha", 242)),
                               outline=rgb(co.get("border", palette.get("ink", "#10243C"))),
                               width=max(1, int(float(co.get("border_width", 1.8)) * u)))
        draw.text((bx0 + padx, by0 + pady + 1 * u), co.get("text", ""), font=font,
                  fill=rgb(co.get("color", palette.get("ink", "#10243C"))), anchor="la")

    # ---------- flow chips (2D process boxes) ----------
    for chip in figure.get("chips", []):
        x0, y0, x1, y1 = chip["rect"]
        radius = float(chip.get("radius", 12)) * u
        draw.rounded_rectangle([x0, y0, x1, y1], radius=radius,
                               fill=rgb(chip.get("fill", "#EAF3FC")),
                               outline=rgb(chip.get("border", palette.get("accent", "#154C8C"))),
                               width=max(1, int(float(chip.get("border_width", 2.2)) * u)))
        font = fonts.get(float(chip.get("size", 30)), bold=bool(chip.get("bold", True)))
        draw.text(((x0 + x1) / 2, (y0 + y1) / 2), chip.get("text", ""), font=font,
                  fill=rgb(chip.get("color", palette.get("ink", "#10243C"))), anchor="mm")

    # ---------- final ----------
    os.makedirs(out_dir, exist_ok=True)
    suffix = "_rgba" if transparent_page else ""
    out_path = os.path.join(out_dir, "figure%s.png" % suffix)
    if transparent_page:
        canvas.save(out_path)
    else:
        canvas.convert("RGB").save(out_path, quality=95)
    return out_path

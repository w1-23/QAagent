# -*- coding: utf-8 -*-
"""Shared design system for CitrusQA × HA figures (task book §3, §10).

Emit pure SVG masters: every text is a <text> node (font-family Arial/Helvetica),
every shape is a vector primitive, icons are inlined Lucide (ISC) stroke paths.
The canvas records QA evidence (texts, connectors, colours, numbers) so the
automated checker can verify §10 items 1-9.
"""
import io, json, re
from pathlib import Path

ICONS_DIR = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\01_design_system\icons")
ICONS_MS_DIR = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\01_design_system\icons_ms")
ICON_STYLE = "ms"        # "ms" = Material Symbols filled (Apache-2.0, matches reference)
LICENSE = {"ms": "Apache-2.0 (@material-symbols/svg-400, filled)",
           "lucide": "ISC (lucide-static v0.462.0)"}

# ── task-book palette (§3.1, fixed) ────────────────────────────────────
C = {
    "rag":      "#4F8868",  "rag_bg":      "#E7F1E5",
    "web":      "#C87558",  "web_bg":      "#FBEAE1",
    "sup":      "#287D99",  "sup_bg":      "#E4F2F8",
    "audit":    "#BE9346",  "audit_bg":    "#FAF0D8",
    "meta":     "#7772A8",  "meta_bg":     "#F0EDFA",
    "text":     "#26343B",  "text2":       "#52616A",
    "border":   "#D4DCE0",  "bg":          "#FFFFFF",
}
ALL_COLORS = set(C.values())

MM_PER_PT = 25.4 / 72.0
PT_PER_MM = 72.0 / 25.4


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class Canvas:
    """SVG master with QA evidence collection."""

    def __init__(self, width_mm, height_mm, fig_id):
        self.w_pt = round(width_mm * PT_PER_MM, 2)
        self.h_pt = round(height_mm * PT_PER_MM, 2)
        self.w_mm, self.h_mm, self.fig_id = width_mm, height_mm, fig_id
        self.el = []
        self.defs = []
        self.texts = []        # dicts: x,y,s,size,weight,anchor,box
        self.box_stacks = []   # for overflow checks: (x,y,w,h,tag)
        self.connectors = []   # dicts: x1,y1,x2,y2
        self.colors = set()
        self.icon_files = {}   # name -> source path + license
        self._mk = 0

    # ---------------------------------------------------------- helpers --
    def marker(self):
        """Register (reusable) one arrowhead marker."""
        self._mk += 1
        mid = f"arrow{self._mk}"
        self.defs.append(
            f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
            f'<path d="M 0 1 L 9 5 L 0 9 z" fill="context-stroke"/></marker>')
        return mid

    def rrect(self, x, y, w, h, fill, stroke=None, sw=0.6, r=4, tag=""):
        if stroke:
            self.colors.add(stroke)
        self.colors.add(fill)
        self.box_stacks.append((x, y, w, h, tag))
        self.el.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'rx="{r}" ry="{r}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw}"/>')

    def text(self, x, y, s, size=8, weight="normal", color=None, anchor="start",
             box=None, tag=""):
        color = color or C["text"]
        self.colors.add(color)
        if box is not None and not isinstance(box, (tuple, list)):
            box = (x, y, box, size)  # bare width → synthesize tuple
        w = None if box is None else box[2]
        self.texts.append(dict(x=round(x, 1), y=round(y, 1), s=s, size=size,
                               weight=weight, color=color, anchor=anchor,
                               box=w, tag=tag))
        self.el.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-family="Arial, Helvetica, sans-serif" '
            f'font-size="{size}" font-weight="{weight}" fill="{color}" '
            f'text-anchor="{anchor}">{esc(s)}</text>')

    def block(self, x, y, lines, size=8, weight="normal", color=None, lead=1.28,
              anchor="start", box_w=None, tag=""):
        """Multi-line text block; returns bottom y."""
        yy = y
        for i, s in enumerate(lines):
            self.text(x, yy, s, size=size, weight=weight, color=color,
                      anchor=anchor, box=(x, yy, box_w, size) if box_w else None,
                      tag=f"{tag}.{i}")
            yy += size * lead
        return yy

    def icon(self, name, cx, cy, size_pt=9, color=None, stroke_w=1.9, style=None):
        """Inline an icon as vector geometry.

        style="ms"     → Material Symbols filled (48×48 viewBox 0 -960 960 960,
                          single filled path set), closest to the approved reference;
        style="lucide" → Lucide line icons (24×24 stroke paths).
        Both are inlined (not rasterised), so the SVG stays fully editable.
        """
        style = style or ICON_STYLE
        color = color or C["text"]
        self.colors.add(color)
        if style == "ms":
            p = ICONS_MS_DIR / f"{name}.svg"
            raw = p.read_text(encoding="utf-8")
            inner = re.findall(r"<path[^>]*?/?>", raw)
            inner_xml = "".join(re.sub(r'\s(?:fill|stroke|class)="[^"]*"', "", n)
                                for n in inner)
            s = size_pt / 960.0
            self.el.append(
                f'<g transform="translate({cx - size_pt / 2:.1f},{cy + size_pt / 2:.1f}) '
                f'scale({s:.6f})" fill="{color}" stroke="none">{inner_xml}</g>')
            self.icon_files[name] = {"path": str(p), "license": LICENSE["ms"]}
            return
        p = ICONS_DIR / f"{name}.svg"
        raw = p.read_text(encoding="utf-8")
        m = re.search(r"<svg[^>]*>([\s\S]*?)</svg>", raw)
        body = m.group(1) if m else raw
        nodes = re.findall(
            r"<(?:path|circle|rect|line|polyline|polygon|ellipse)\b[^>]*?/>|"
            r"<(?:path|circle|rect|line|polyline|polygon|ellipse)\b[^>]*?>[\s\S]*?</(?:path|circle|rect|line|polyline|polygon|ellipse)>",
            body)
        cleaned = []
        for n in nodes:
            n = re.sub(r'\s(?:fill|stroke|stroke-width|stroke-linecap|stroke-linejoin|class)="[^"]*"', "", n)
            cleaned.append(n)
        inner_xml = "".join(cleaned)
        s = size_pt / 24.0
        self.el.append(
            f'<g transform="translate({cx - size_pt / 2:.1f},{cy - size_pt / 2:.1f}) '
            f'scale({s:.4f})" fill="none" stroke="{color}" stroke-width="{stroke_w}" '
            f'stroke-linecap="round" stroke-linejoin="round">{inner_xml}</g>')
        self.icon_files[name] = {"path": str(p), "license": LICENSE["lucide"]}

    def arrow(self, x1, y1, x2, y2, color=None, w=0.8):
        color = color or C["border"]
        self.colors.add(color)
        mk = self.marker()
        self.connectors.append(dict(x1=x1, y1=y1, x2=x2, y2=y2))
        self.el.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{color}" stroke-width="{w}" marker-end="url(#{mk})"/>')

    def line(self, x1, y1, x2, y2, color=None, w=0.7, dash=None):
        color = color or C["border"]
        self.colors.add(color)
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.el.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{color}" stroke-width="{w}"{d}/>')

    # ------------------------------------------------------------- save --
    def numbers(self):
        """All numeric tokens appearing in texts (with thousands separators)."""
        out = set()
        for t in self.texts:
            for m in re.finditer(r"\d[\d,\.]*%?", t["s"]):
                out.add(m.group(0))
        return out

    def save(self, path: Path, extra_check=None):
        if extra_check:
            extra_check(self)
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{self.w_mm}mm" height="{self.h_mm}mm" '
            f'viewBox="0 0 {self.w_pt} {self.h_pt}" '
            f'font-family="Arial, Helvetica, sans-serif">\n'
            + "\n".join(self.defs) + "\n"
            + f'<rect x="0" y="0" width="{self.w_pt}" height="{self.h_pt}" fill="{C["bg"]}"/>\n'
            + "\n".join(self.el) + "\n</svg>\n")
        Path(path).write_text(svg, encoding="utf-8")
        qa = {
            "fig": self.fig_id, "width_mm": self.w_mm, "height_mm": self.h_mm,
            "canvas_pt": [self.w_pt, self.h_pt], "n_icons": len(self.icon_files),
            "icons": self.icon_files, "n_connectors": len(self.connectors),
            "numbers": sorted(self.numbers()),
            "colors": sorted(self.colors),
        }
        Path(str(path).replace(".svg", "_qa.json")).write_text(
            json.dumps(qa, ensure_ascii=False, indent=1), encoding="utf-8")
        return qa

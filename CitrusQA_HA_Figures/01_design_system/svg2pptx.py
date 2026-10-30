# -*- coding: utf-8 -*-
"""svg2pptx — mirror our own SVG masters into editable PPTX twins.

Mirrors: <rect> → rounded-rectangle shape; <text> → textbox (same size,
weight, colour, alignment); <line marker-end> → straight connector with a
triangle tail-end; icon <g> groups → tinted placeholder ovals (true vector
icons live in the SVG master; PPTX keeps layout fully editable);
band <path> (straight sides known wavelength corners) → freeform polygon.

QA (§10.2): the SVG is the editable master (texts are <text> nodes);
the PPTX mirrors every box so editors can drag module layouts.
"""
import sys, io, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path
from lxml import etree
from pptx import Presentation
from pptx.util import Emu, Pt as PPt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn as pqn

NS = {"svg": "http://www.w3.org/2000/svg"}


def hex2rgb(h):
    h = h.lstrip("#")
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def lighten(h, t=0.72):       # t: proportion of white blended
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    r = int(r * (1 - t) + 255 * t); g = int(g * (1 - t) + 255 * t); b = int(b * (1 - t) + 255 * t)
    return f"{r:02X}{g:02X}{b:02X}"


def path_to_polys(d, steps=8):
    """Flatten an SVG path (M/L/H/V/C/S/Q/T/Z, absolute+relative, no arcs) into
    polygon point lists [ [(x,y), ...], ... ] in the icon's own coordinate space.

    All 35 Material Symbols icons in icons_ms/ use only these commands, so the
    PPTX twin can carry genuine vector icons instead of placeholder circles.
    """
    toks = re.findall(r"[MmLlHhVvCcSsQqTtZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", d)
    polys, cur, start = [], [], (0.0, 0.0)
    cx = cy = 0.0
    prev_c = prev_q = None
    i, cmd = 0, None
    def num():
        nonlocal i
        v = float(toks[i]); i += 1
        return v
    while i < len(toks):
        t = toks[i]
        if re.match(r"[A-Za-z]", t):
            cmd = t; i += 1
            if cmd in "Zz":
                if cur:
                    cur.append(start)
                    polys.append(cur)
                    cur = []
                cx, cy = start
                prev_c = prev_q = None
                continue
        rel = cmd.islower()
        C = cmd.upper()
        if C == "M":
            x, y = num(), num()
            x, y = (cx + x, cy + y) if rel else (x, y)
            if cur:
                polys.append(cur)
            cur = [(x, y)]
            start = (x, y); cx, cy = x, y
            cmd = "l" if rel else "L"          # implicit lineto after moveto
        elif C == "L":
            x, y = num(), num()
            x, y = (cx + x, cy + y) if rel else (x, y)
            cur.append((x, y)); cx, cy = x, y
        elif C == "H":
            x = num(); x = cx + x if rel else x
            cur.append((x, cy)); cx = x
        elif C == "V":
            y = num(); y = cy + y if rel else y
            cur.append((cx, y)); cy = y
        elif C in ("C", "S"):
            if C == "C":
                x1, y1, x2, y2, x, y = (num() for _ in range(6))
                if rel:
                    x1, y1, x2, y2, x, y = cx + x1, cy + y1, cx + x2, cy + y2, cx + x, cy + y
            else:
                x2, y2, x, y = (num() for _ in range(4))
                if rel:
                    x2, y2, x, y = cx + x2, cy + y2, cx + x, cy + y
                x1, y1 = (2 * cx - prev_c[0], 2 * cy - prev_c[1]) if prev_c else (cx, cy)
            for k in range(1, steps + 1):
                u = k / steps; v = 1 - u
                bx = (v ** 3) * cx + 3 * (v ** 2) * u * x1 + 3 * v * (u ** 2) * x2 + (u ** 3) * x
                by = (v ** 3) * cy + 3 * (v ** 2) * u * y1 + 3 * v * (u ** 2) * y2 + (u ** 3) * y
                cur.append((bx, by))
            prev_c = (x2, y2); prev_q = None
            cx, cy = x, y
        elif C in ("Q", "T"):
            if C == "Q":
                x1, y1, x, y = (num() for _ in range(4))
                if rel:
                    x1, y1, x, y = cx + x1, cy + y1, cx + x, cy + y
            else:
                x, y = num(), num()
                if rel:
                    x, y = cx + x, cy + y
                x1, y1 = (2 * cx - prev_q[0], 2 * cy - prev_q[1]) if prev_q else (cx, cy)
            for k in range(1, steps + 1):
                u = k / steps; v = 1 - u
                bx = (v ** 2) * cx + 2 * v * u * x1 + (u ** 2) * x
                by = (v ** 2) * cy + 2 * v * u * y1 + (u ** 2) * y
                cur.append((bx, by))
            prev_q = (x1, y1); prev_c = None
            cx, cy = x, y
    if cur:
        polys.append(cur)
    return polys


def mirror(svg_path: Path, pptx_path: Path, icon_ns=True):
    root = etree.parse(str(svg_path)).getroot()
    VB = [float(x) for x in root.get("viewBox").split()]
    WMM = float(root.get("width").replace("mm", ""))
    HMM = float(root.get("height").replace("mm", ""))
    scale = WMM * 36000.0 / VB[2]          # EMU per viewBox unit (1 mm = 36000 EMU)
    offx = -VB[0] * scale
    offy = -VB[1] * scale

    prs = Presentation()
    prs.slide_width = Emu(int(WMM * 36000))
    prs.slide_height = Emu(int(HMM * 36000))
    slide = prs.slides.add_slide(prs.slide_layouts[6])

    ICON_MARK = 0

    def emu(v):
        return int(round(v * scale + (offx if False else 0)))

    def _clear_margins(tf):
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0

    n_txt = n_rect = n_line = n_icon = n_band = 0
    for el in root.iter():
        tag = etree.QName(el).localname
        if tag == "rect":
            x = float(el.get("x", 0)); y = float(el.get("y", 0))
            w = float(el.get("width", 0)); h = float(el.get("height", 0))
            fill = el.get("fill", "#FFFFFF"); stroke = el.get("stroke")
            sw = float(el.get("stroke-width", 0.6) or 0.6)
            if fill.startswith("rgb") or w <= 0 or h <= 0:   # background plate etc.
                continue
            if stroke in (None, "none") or not stroke.startswith("#"):
                stroke = None
            sh = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                        Emu(emu(x)), Emu(emu(y)), Emu(emu(w)), Emu(emu(h)))
            try:
                sh.adjustments[0] = 0.10
            except Exception:
                pass
            sh.fill.solid(); sh.fill.fore_color.rgb = hex2rgb(fill)
            if stroke:
                sh.line.color.rgb = hex2rgb(stroke); sh.line.width = PPt(sw)
            else:
                sh.line.fill.background()
            sh.shadow.inherit = False
            n_rect += 1
        elif tag == "text":
            x = float(el.get("x", 0)); y = float(el.get("y", 0))
            size = float(el.get("font-size", 8)); weight = el.get("font-weight", "normal")
            color = el.get("fill", "#26343B"); anchor = el.get("text-anchor", "start")
            txt = "".join(el.itertext())
            if not txt.strip():
                continue
            tb = slide.shapes.add_textbox(Emu(emu(x - (2 if anchor == 'start' else 0))),
                                          Emu(emu(y - size * 1.05)), Emu(emu(max(12, len(txt) * size * 0.62 + 6))),
                                          Emu(int(size * 1.6 * 12700)))
            _clear_margins(tb.text_frame)
            tf = tb.text_frame; tf.word_wrap = False
            p = tf.paragraphs[0]
            p.alignment = {"start": PP_ALIGN.LEFT, "middle": PP_ALIGN.CENTER,
                           "end": PP_ALIGN.RIGHT}.get(anchor, PP_ALIGN.LEFT)
            r = p.add_run(); r.text = txt
            r.font.size = PPt(size); r.font.bold = (weight == "bold")
            r.font.color.rgb = hex2rgb(color)
            r.font.name = "Arial"
            n_txt += 1
        elif tag == "line":
            x1 = float(el.get("x1", 0)); y1 = float(el.get("y1", 0))
            x2 = float(el.get("x2", 0)); y2 = float(el.get("y2", 0))
            col = el.get("stroke", "#D4DCE0"); sw = float(el.get("stroke-width", 0.8))
            ln = slide.shapes.add_connector(1, Emu(emu(x1)), Emu(emu(y1)), Emu(emu(x2)), Emu(emu(y2)))
            ln.line.color.rgb = hex2rgb(col); ln.line.width = PPt(sw)
            lnE = ln.line._get_or_add_ln()
            te = lnE.makeelement(pqn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"})
            lnE.append(te)
            ln.shadow.inherit = False
            n_line += 1
        elif tag == "g" and el.get("transform") and icon_ns:
            m = re.match(r"translate\(([-\d.]+),\s*([-\d.]+)\)\s*scale\(([\d.]+)\)", el.get("transform"))
            if m:
                tx, ty, s = float(m.group(1)), float(m.group(2)), float(m.group(3))
                stroke_v = el.get("stroke") or ""
                fill_v = el.get("fill") or ""
                is_ms = not stroke_v.startswith("#") and fill_v.startswith("#")
                col = fill_v if is_ms else (stroke_v if stroke_v.startswith("#") else "#26343B")
                built = 0
                for pth in [e for e in el.iter() if etree.QName(e).localname == "path"]:
                    for poly in path_to_polys(pth.get("d", "")):
                        if len(poly) < 3:
                            continue
                        pts = [(Emu(emu(tx + px * s)), Emu(emu(ty + py * s))) for px, py in poly]
                        ff = slide.shapes.build_freeform(pts[0][0], pts[0][1], scale=1)
                        ff.add_line_segments(pts[1:], close=True)
                        shp = ff.convert_to_shape()
                        shp.fill.solid()
                        shp.fill.fore_color.rgb = hex2rgb(col)
                        shp.line.fill.background()
                        shp.shadow.inherit = False
                        built += 1
                if built == 0:      # unexpected geometry → visible-safe placeholder
                    size = (960.0 * s) if is_ms else (24.0 * s)
                    top = (ty - size) if is_ms else ty
                    sh = slide.shapes.add_shape(MSO_SHAPE.OVAL, Emu(emu(tx)), Emu(emu(top)),
                                                Emu(emu(size)), Emu(emu(size)))
                    sh.fill.solid(); sh.fill.fore_color.rgb = hex2rgb(lighten(col, 0.85))
                    sh.line.color.rgb = hex2rgb(col); sh.line.width = PPt(0.6)
                    sh.shadow.inherit = False
                    built = 1
                n_icon += built
        elif tag == "path" and el.get("fill") and abs(float(el.get("fill-opacity", -1)) - 0.3) < 1e-6:
            # flow band (M x t C ... L ... C ... Z) → straight ribbon polygon
            nums = [float(v) for v in re.findall(r"[-\d.]+", el.get("d", ""))]
            if len(nums) >= 12:
                x1, t1, x2, t2, x2b, b2, b1 = nums[0], nums[1], nums[3], nums[4], nums[5], nums[6], nums[11]
                ff = slide.shapes.build_freeform(Emu(emu(x1)), Emu(emu(t1)), scale=1)
                ff.add_line_segments([(Emu(emu(x2)), Emu(emu(t2))),
                                      (Emu(emu(x2b)), Emu(emu(b2))),
                                      (Emu(emu(x1)), Emu(emu(b1)))], close=True)
                shp = ff.convert_to_shape()
                shp.fill.solid(); shp.fill.fore_color.rgb = hex2rgb(lighten(el.get("fill"), 0.30))
                shp.line.fill.background(); shp.shadow.inherit = False
                n_band += 1
    prs.save(str(pptx_path))
    return dict(svg=str(svg_path), pptx=str(pptx_path), mm=[WMM, HMM],
                rects=n_rect, texts=n_txt, lines=n_line, icons=n_icon, bands=n_band)


if __name__ == "__main__":
    import json
    FIG1 = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\Fig1")
    FIG2 = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\Fig2")
    r1 = mirror(FIG1 / "fig1_system.svg", FIG1 / "fig1_system.pptx")
    r2 = mirror(FIG2 / "fig2_corpus.svg", FIG2 / "fig2_corpus.pptx")
    (FIG1 / "pptx_mirror_log.json").write_text(json.dumps(r1, indent=1), encoding="utf-8")
    (FIG2 / "pptx_mirror_log.json").write_text(json.dumps(r2, indent=1), encoding="utf-8")
    print(json.dumps([r1, r2], indent=1))

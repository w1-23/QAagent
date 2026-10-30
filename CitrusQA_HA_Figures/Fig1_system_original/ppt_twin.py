# -*- coding: utf-8 -*-
"""Editable PPT twins of the two mechanism master figures.

Consumes the SAME layout data (00_shared/flow_layout.py) as the matplotlib
vector versions (1:1 correspondence). Text wrapped to box width, boxes
auto-grow downward. Figure 2 statistics = independent text box.

Outputs:
  40_flow_system_master/flow_system_master_ppt.pptx
  41_flow_corpus_pipeline_ppt.pptx   (at 2026927-python绘图latest root, keep together)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ppt_style as ps
import flow_layout as L

# ── PI font ruling: autofit so no text spills out of its box in the PPT twin ──
_ps_add_text = ps.add_text


def _autofit_add_text(*a, **kw):
    tb = _ps_add_text(*a, **kw)
    from pptx.enum.text import MSO_AUTO_SIZE
    tb.text_frame.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    return tb


ps.add_text = _autofit_add_text
from pptx.enum.shapes import MSO_SHAPE

GRAY_T = "#52616A"
PAD_TB = 0.20       # title -> body gap, cm (task book v13 s3)
PAD_T = 0.30        # title offset below box top, cm


def render_layout(prs, lay, y_off_cm=0.0):
    W_cm = lay["W"] * 2.54
    H_cm = lay["H"] * 2.54
    slide = prs.slides.add_slide(prs.slide_layouts[6])

    def node(n):
        nid, x, y, w, h, title, sub, fc, ec, shape, ts, ss = n
        x_cm, w_cm = x * W_cm, w * W_cm
        top = (1 - y - h) * H_cm + y_off_cm
        h_cm = h * H_cm
        t_lines = ps.wrap_cm(title, w_cm - 0.30, ts, True) if title else []
        s_lines = ps.wrap_cm(sub, w_cm - 0.30, ss) if sub else []
        t_h = len(t_lines) * ps.line_h_cm(ts)
        s_h = len(s_lines) * ps.line_h_cm(ss) if s_lines else 0.0
        need = 0.16 + t_h + (PAD_TB + s_h if s_lines else 0.0)
        if need > h_cm:
            h_cm = need                            # grow downward, top fixed
        if shape == "diamond":
            shp = slide.shapes.add_shape(MSO_SHAPE.DIAMOND, ps.Cm(x_cm),
                                         ps.Cm(top), ps.Cm(w_cm), ps.Cm(h_cm))
        else:
            shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                         ps.Cm(x_cm), ps.Cm(top), ps.Cm(w_cm),
                                         ps.Cm(h_cm))
            try:
                shp.adjustments[0] = 0.10
            except Exception:
                pass
        if fc:
            shp.fill.solid()
            shp.fill.fore_color.rgb = ps.rgb(fc)
        else:
            shp.fill.background()
        shp.line.color.rgb = ps.rgb(ec if ec else "#52616A")
        shp.line.width = ps.Pt(0.5)
        shp.shadow.inherit = False
        tcol = GRAY_T if nid == "strip" else ps.INK
        # container: title pinned near the top, no body text (task book v12)
        if shape == "container":
            if t_lines:
                ps.add_text(slide, x_cm + 0.05, top + PAD_T, w_cm - 0.1, t_h,
                            "\n".join(t_lines), size=ts, bold=True,
                            color=ps.INK, align="center", line_spacing=1.2)
        elif shape == "diamond" or sub is None:
            ps.add_text(slide, x_cm + 0.05, top, w_cm - 0.1, h_cm, title,
                        size=ts, bold=(nid != "strip"), color=tcol,
                        align="center", anchor="middle")
        else:
            # vertically centre the title+body block (task book v11), matching
            # the matplotlib renderer in flow_system_master.draw_layout
            content_cm = t_h + (PAD_TB + s_h if s_lines else 0.0)
            pad_top = max(0.10, (h_cm - content_cm) / 2.0)
            ps.add_text(slide, x_cm + 0.08, top + pad_top, w_cm - 0.16, t_h,
                        "\n".join(t_lines), size=ts, bold=True, color=ps.INK,
                        align="center", line_spacing=1.2)
            ps.add_text(slide, x_cm + 0.08, top + pad_top + t_h + PAD_TB,
                        w_cm - 0.16, s_h + 0.1, "\n".join(s_lines), size=ss,
                        color=GRAY_T, align="center", line_spacing=1.2)

    def edge(e):
        pts, color, lw, ls, arrow = e
        for i in range(len(pts) - 1):
            (x1, y1), (x2, y2) = pts[i], pts[i + 1]
            last = i == len(pts) - 2
            if last and arrow:
                ps.add_arrow(slide, x1 * W_cm, (1 - y1) * H_cm + y_off_cm,
                             x2 * W_cm, (1 - y2) * H_cm + y_off_cm,
                             color=color, lw=lw)
            else:
                ps.add_line(slide, x1 * W_cm, (1 - y1) * H_cm + y_off_cm,
                            x2 * W_cm, (1 - y2) * H_cm + y_off_cm,
                            color=color, lw=lw)

    def text(t):
        x, y, w, h, txt, size, color, align, rot, bold = t
        tb = ps.add_text(slide, x * W_cm, (1 - y - h) * H_cm + y_off_cm,
                         w * W_cm, h * H_cm, txt, size=size, color=color,
                         align=align, bold=bold, line_spacing=1.2)
        if rot:
            tb.rotation = rot

    for n in lay["nodes"]:
        node(n)
    for e in lay["edges"]:
        edge(e)
    for t in lay["texts"]:
        text(t)
    return slide


def main():
    """Editable PPT twin of the ORIGINAL Fig.1 layout (LAYOUT1 only), PI palette."""
    prs = ps.new_deck()
    W_CM = L.LAYOUT1["W"] * 2.54
    H_CM = L.LAYOUT1["H"] * 2.54 + 1.30          # room for the legend box
    prs.slide_width = ps.Cm(W_CM)
    prs.slide_height = ps.Cm(H_CM)
    render_layout(prs, L.LAYOUT1)
    legend = L.LAYOUT1.get("legend", "")
    if legend:
        ps.add_text(prs.slides[0], 0.30, H_CM - 1.05, W_CM - 0.60, 0.80,
                    legend, size=7, color="#52616A", align="left")
    out = os.path.join(HERE, "out", "fig1_system_original.pptx")
    prs.save(out)
    print("saved", out)


if __name__ == "__main__":
    main()

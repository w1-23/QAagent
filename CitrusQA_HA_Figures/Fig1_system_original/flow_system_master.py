# -*- coding: utf-8 -*-
"""Figure 1 - CitrusQA multi-agent end-to-end architecture (matplotlib vector).

Layout source: 00_shared/flow_layout.py (LAYOUT1) - shared 1:1 with the
editable PPT twin. Text is wrapped to box width (cm); boxes auto-grow
downward when the wrapped text needs more room (task book 6.4).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mpl_style as ms
import flow_layout as L

PANEL = ""
CAPTION = L.LAYOUT1["caption"]
SOURCE = L.LAYOUT1["source"]
NOTE = L.LAYOUT1["note"]
TAG = "Flow - System master"
OUTDIR = os.path.join(HERE, "out")
OUT = "fig1_system_original"

PAD_TB = 0.20       # title -> body gap, cm (task book v13 s3)
PAD_T = 0.30        # title offset below box top, cm


def draw_layout(ax, lay, fig_w_cm, fig_h_cm):
    asp = fig_w_cm / fig_h_cm
    top_title = {"loc", "web"}

    for nid, x, y, w, h, title, sub, fc, ec, shape, ts, ss in lay["nodes"]:
        f = fc if fc else "none"
        e = ec if ec else "#52616A"
        if shape == "diamond":
            ms.diamond(ax, x + w / 2, y + h / 2, w, h, title, fc=f, ec=e, ts=ts)
            continue
        w_cm = w * fig_w_cm
        h_cm = h * fig_h_cm
        t_lines = ms.wrap_cm(title, w_cm - 0.30, ts, True) if title else []
        s_lines = ms.wrap_cm(sub, w_cm - 0.30, ss) if sub else []
        t_h = len(t_lines) * ms.line_h_cm(ts)
        s_h = len(s_lines) * ms.line_h_cm(ss) if s_lines else 0.0
        need_cm = 0.16 + t_h + (PAD_TB + s_h if s_lines else 0.0)
        top = y + h                                # fraction
        if need_cm > h_cm:                         # grow downward, top fixed
            h_cm = need_cm
        y_bot = top - h_cm / fig_h_cm              # bottom (fraction)
        ms.rbox(ax, x, y_bot, w, h_cm / fig_h_cm, "", None, fc=f, ec=e, lw=0.5,
                aspect=asp)
        # container: title pinned near the top, no body text (task book v12);
        # offset matches the 0.30 cm title rule from v13 s3
        if nid in top_title or shape == "container":
            if t_lines:
                ax.text(x + w / 2, top - (PAD_T + t_h / 2) / fig_h_cm,
                        "\n".join(t_lines), ha="center", va="center",
                        fontsize=ts, fontweight="bold", color="#26343B", zorder=6)
            continue
        # vertically CENTRE the title+body block (task book v13 s3): with the
        # boxes sized to content this puts the title 0.30 cm below the box top
        # and the body 0.20 cm below the title.
        content_cm = t_h + (PAD_TB + s_h if s_lines else 0.0)
        pad_top = max(0.08, (h_cm - content_cm) / 2.0)
        ty = top - (pad_top + t_h / 2) / fig_h_cm
        ax.text(x + w / 2, ty, "\n".join(t_lines), ha="center", va="center",
                fontsize=ts, fontweight="bold", color="#26343B",
                linespacing=1.2, zorder=6)
        if s_lines:
            sy = top - (pad_top + t_h + PAD_TB + s_h / 2) / fig_h_cm
            ax.text(x + w / 2, sy, "\n".join(s_lines), ha="center", va="center",
                    fontsize=ss, color="#52616A", linespacing=1.2, zorder=6)

    for pts, color, lw, ls, arrow in lay["edges"]:
        if arrow:
            ms.polyline(ax, pts, color=color, lw=lw, ls=ls)
        else:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            ax.plot(xs, ys, color=color, lw=lw, ls=ls, solid_capstyle="butt",
                    zorder=4)

    for x, y, w, h, t, size, color, align, rot, bold in lay["texts"]:
        # anchor on the requested edge of the text box (task book v12)
        if align == "left":
            xa = x
        elif align == "right":
            xa = x + w
        else:
            xa = x + w / 2
        ax.text(xa, y + h / 2, t, fontsize=size, color=color, ha=align,
                va="center", rotation=rot,
                fontweight="bold" if bold else "normal", linespacing=1.2,
                zorder=6)


def make_fig():
    fig = ms.plt.figure(figsize=(L.LAYOUT1["W"], L.LAYOUT1["H"]))
    ax = ms.flow_ax(fig)
    draw_layout(ax, L.LAYOUT1, L.LAYOUT1["W"] * 2.54, L.LAYOUT1["H"] * 2.54)
    return fig


if __name__ == "__main__":
    ms.save(make_fig(), os.path.join(OUTDIR, OUT), "")

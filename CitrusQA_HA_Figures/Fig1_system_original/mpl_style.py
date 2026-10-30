# -*- coding: utf-8 -*-
"""Publication-grade matplotlib style for CitrusQA figures (English).

Spec (task book v7): Times New Roman everywhere; figure title 10pt bold; axis label 8pt;
ticks 7pt; legend 8pt; data labels 7pt; caption 7pt; wspace=0.3/hspace=0.35;
axis line 0.5pt #333; top/right spines removed; ticks in (len 2pt, w 0.5);
NO GRID ANYWHERE (task book 2.3); legend frameon=False; bar width 0.6 with #333
0.5pt edge; error bars capped #333 0.8pt; forest = SOLID #333 zero line 0.5pt +
#333 CI 1.0pt with caps + semantic point (ms=3.5) + MDD band #d0e2c0 alpha .3;
heatmap sequential cmap #d0e2c0->#a2c986->#49c2d9, 0.5pt white cell
borders, 2-decimal TNR 7pt values, null cells #F0F0F0 labelled "null";
line 1.2pt with shape-coded markers (size 4); NO DASHED LINES ANYWHERE;
radar 1.2pt lines, UNFILLED; no 3D/shadow/gradients. Exports: editable SVG
(svg.fonttype='none'), 600 dpi TIFF and PNG.
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
import numpy as np

# ── palette ──
C1, C2, C3, C4, C5, C6, C7, C8, C9, C10, C11 = (
    "#7b95c6", "#49c2d9", "#a1d8e8", "#67a583", "#a2c986", "#d0e2c0",
    "#fded95", "#ffc1a6", "#f59c7c", "#f47254", "#c85e62")
SEQ = [C1, C2, C3, C4, C5, C6, C7, C8, C9, C10, C11]

# ── revised palette (task book v3): drop #7b95c6 / #67a583 / #c85e62, ordered S1..S8 ──
S1, S2, S3, S4, S5, S6, S7, S8 = ("#49c2d9", "#a1d8e8", "#a2c986", "#d0e2c0",
                                  "#fded95", "#ffc1a6", "#f59c7c", "#f47254")
SEQ8 = [S1, S2, S3, S4, S5, S6, S7, S8]
S_LIGHT = {S3, S4, S5, S6}          # need a #333 edge when used as fill
RULE, MUTED = "#52616A", "#B9C2C7"
NEED_EDGE = {C3, C5, C6, C7, C8}

# semantic locks
SYS5 = {"CitrusQA": C10, "DeepSeek-A": C1, "DeepSeek-B": C3,
        "Qwen-max-A": C4, "Qwen-max-B": C6}
SYS5_ORDER = ["CitrusQA", "DeepSeek-A", "DeepSeek-B", "Qwen-max-A", "Qwen-max-B"]
CONFIG_KEY = {"CitrusQA": "citrusqa", "DeepSeek-A": "same_base_trackA",
              "DeepSeek-B": "same_base_trackB", "Qwen-max-A": "mainstream_trackA",
              "Qwen-max-B": "mainstream_trackB"}
EXP1 = {"Z_Z": C1, "Z_E": C3, "E_Z": C2, "E_E": C5}
EXP2 = {"C1": C1, "C2": C3, "C3": C4, "C4": C10}
CHANNEL = {"Dense": C1, "BM25": C10, "RRF": C2, "RRF window": C2}
GOLD = {"G0": C1, "G1": C10, "G2": C11}
WEB = {"A": C1, "B": C10, "C": C11, "C'": C7, "D": C9}
FOREST_SIG = {"pos": C4, "neg": C11, "ns": C3}

DIM_SHORT = {"D3": "Factual", "D4": "Completeness", "D5": "Key points",
             "D6": "Temporal", "D7": "Scope honesty", "D8": "Calibration",
             "D9": "Presentation", "D10": "Conciseness"}
DIM_LABEL = {"D1": "D1 Citation existence", "D2": "D2 Citation support",
             "D3": "D3 Factual correctness", "D4": "D4 Completeness",
             "D5": "D5 Key-point coverage", "D6": "D6 Temporal validity",
             "D7": "D7 Scope honesty", "D8": "D8 Uncertainty calibration",
             "D9": "D9 Presentation", "D10": "D10 Conciseness"}


def setup():
    plt.rcParams.update({
        "font.family": ["Arial"],
        "font.size": 7,
        "axes.titlesize": 10, "axes.titleweight": "bold", "axes.titlepad": 6,
        "axes.labelsize": 8,
        "xtick.labelsize": 7, "ytick.labelsize": 7,
        "legend.fontsize": 8, "legend.frameon": False,
        "axes.linewidth": 0.5, "axes.edgecolor": RULE,
        "axes.spines.top": False, "axes.spines.right": False,
        "xtick.direction": "in", "ytick.direction": "in",
        "xtick.major.size": 2, "ytick.major.size": 2,
        "xtick.major.width": 0.5, "ytick.major.width": 0.5,
        "xtick.color": RULE, "ytick.color": RULE,
        "grid.color": "#E5E5E5", "grid.linestyle": "-", "grid.linewidth": 0.3,
        "axes.grid": False,
        "svg.fonttype": "none", "pdf.fonttype": 42, "ps.fonttype": 42,
        "savefig.dpi": 600, "figure.facecolor": "white",
        "axes.facecolor": "white",
        "lines.solid_capstyle": "butt",
        "mathtext.fontset": "stix",
    })


setup()


def new_fig(w=9.0, h=5.0, nrows=1, ncols=1, wspace=0.3, hspace=0.35, **kw):
    fig, axes = plt.subplots(nrows, ncols, figsize=(w, h), **kw)
    fig.subplots_adjust(wspace=wspace, hspace=hspace,
                        top=0.90, bottom=0.13, left=0.09, right=0.97)
    return fig, axes


def one(w=9.0, h=5.0):
    fig, ax = new_fig(w, h)
    return fig, ax


def grid_y(ax):
    # 任务书 §5.5：网格全部删除 —— 保留函数签名（旧脚本仍在调用），但不再画网格
    ax.set_axisbelow(True)


def save(fig, base, source=""):
    """base: path without extension. Writes .svg / .pdf / .tif(600dpi) / .png(600dpi).

    Per revised task book the caption/source is NOT baked into the figure; it is
    delivered as an external text box (see build_all)."""
    os.makedirs(os.path.dirname(os.path.abspath(base)), exist_ok=True)
    for ext, dpi in (("svg", None), ("pdf", None), ("tif", 600), ("png", 600)):
        p = "%s.%s" % (base, ext)
        if dpi:
            fig.savefig(p, dpi=dpi, bbox_inches="tight", pad_inches=0.15)
        else:
            fig.savefig(p, bbox_inches="tight", pad_inches=0.15)
        print("saved", p)
    plt.close(fig)
    return base + ".png"


def label_bars(ax, bars, fmt="%.2f", size=7, pad=0.01):
    ymax = ax.get_ylim()[1]
    for b in bars:
        v = b.get_height()
        # never render "-0.00": values that round to zero print as 0.00
        s = "0.00" if (fmt == "%.2f" and abs(v) < 0.005) else fmt % v
        ax.text(b.get_x() + b.get_width() / 2, v + pad * ymax, s,
                ha="center", va="bottom", fontsize=size, color="#52616A")


def err(ax, xs, vals, plus, minus, color=RULE, lw=0.8, capsize=3, zorder=6):
    ax.errorbar(xs, vals, yerr=[minus, plus], fmt="none", ecolor=color,
                elinewidth=lw, capsize=capsize, capthick=lw, zorder=zorder)


def seq_cmap():
    """Revised sequential scale (task book v3): #d0e2c0 -> #a2c986 -> #49c2d9."""
    return LinearSegmentedColormap.from_list("mseq", ["#d0e2c0", "#a2c986", "#49c2d9"])


# uniform canvas sizes (cm) -> inches
CM = 1 / 2.54
SINGLE = (8.5 * CM, 6.0 * CM)        # single-column bars / small panels
DOUBLE = (17.0 * CM, 6.0 * CM)       # double-column forests / lines
RADAR = (8.5 * CM, 8.5 * CM)
HBAR = (17.0 * CM, 7.0 * CM)         # horizontal bars with long labels
# heatmaps (task book v8 5.1): uniform 7x4 inch canvas, aspect='auto' -> compact cells
HEAT4 = (7.0, 4.0)      # figsize is in inches
HEAT510 = HEAT4


def heat(ax, M, rowlabels, collabels, vmin=None, vmax=None, cmap=None,
         fmt="%.2f", null="null", cbar=True, cb_label=""):
    """M: 2D list, None -> null cell #F0F0F0."""
    cmap = cmap or seq_cmap()
    vals = [v for r in M for v in r if v is not None]
    vmin = min(vals) if vmin is None else vmin
    vmax = max(vals) if vmax is None else vmax
    nr, nc = len(M), len(M[0])
    disp = np.full((nr, nc), np.nan)
    for i in range(nr):
        for j in range(nc):
            if M[i][j] is not None:
                disp[i, j] = M[i][j]
    masked = np.ma.masked_invalid(disp)
    cmap.set_bad("#F0F0F0")
    # task book v8 5.1: aspect='auto' -> cells fill the axes (compact, not square)
    ax.set_aspect("auto")
    ax.pcolormesh(np.arange(nc + 1), np.arange(nr + 1), masked[::-1],
                  cmap=cmap, vmin=vmin, vmax=vmax,
                  edgecolors="white", linewidth=0.5)
    ax.invert_yaxis()
    ax.set_xticks(np.arange(nc) + 0.5)
    ax.set_xticklabels(collabels)
    ax.set_yticks(np.arange(nr) + 0.5)
    ax.set_yticklabels(rowlabels)
    ax.tick_params(length=0)
    for sp in ax.spines.values():
        sp.set_visible(False)
    for i in range(nr):
        for j in range(nc):
            v = M[i][j]
            if v is None:
                ax.text(j + 0.5, i + 0.5, null, ha="center", va="center",
                        fontsize=7, color="#8C8C8C")
            else:
                fill = cmap((v - vmin) / (vmax - vmin))
                lum = 0.299 * fill[0] + 0.587 * fill[1] + 0.114 * fill[2]
                ax.text(j + 0.5, i + 0.5, fmt % v, ha="center", va="center",
                        fontsize=7, color="white" if lum < 0.5 else "#1F1F1F")
    if cbar:
        from matplotlib.cm import ScalarMappable
        from matplotlib.colors import Normalize
        sm = ScalarMappable(Normalize(vmin, vmax), cmap)
        cb = plt.gcf().colorbar(sm, ax=ax, fraction=0.035, pad=0.02)
        cb.ax.tick_params(labelsize=7, length=2, width=0.5)
        cb.outline.set_linewidth(0.5)
        cb.outline.set_edgecolor(RULE)
        if cb_label:
            cb.set_label(cb_label, fontsize=7)


def forest(ax, rows, xlim, xlab, label_size=8, ann_size=7, row_mdd=False,
           x0_ann=0.68, pad_right=0.34):
    """rows: dict(label, diff, lo, hi, sig, mdd, ann). Axes spans labels+plot+ann;
    plot area = [0, 1-pad_right] of axes fraction. All coordinates manual."""
    ax.set_xlim(0, 1)
    ax.set_ylim(len(rows) + 0.85, -0.7)
    ax.axis("off")
    px0, px1 = 0.24, 1 - pad_right          # plot span in axes fraction

    def X(v):
        return px0 + (v - xlim[0]) / (xlim[1] - xlim[0]) * (px1 - px0)

    for i, r in enumerate(rows):
        y = i
        ax.text(0.225, y, r["label"], ha="right", va="center", fontsize=label_size,
                color="#52616A")
        if row_mdd and r.get("mdd") is not None:
            ax.add_patch(Rectangle((X(-r["mdd"]), y - 0.30),
                                   X(r["mdd"]) - X(-r["mdd"]), 0.60,
                                   color=C6, alpha=0.3, lw=0, zorder=1))
        ax.errorbar(X(r["diff"]), y,
                    xerr=[[X(r["diff"]) - X(r["lo"])],
                          [X(r["hi"]) - X(r["diff"])]],
                    fmt="none", ecolor=RULE, elinewidth=1.0, capsize=3,
                    zorder=4)
        c = r.get("color", S1)
        ax.scatter(X(r["diff"]), y, s=15, color=c, edgecolor=RULE,
                   linewidth=0.5, zorder=5)
        if r.get("ann"):
            ax.text(x0_ann, y, r["ann"], ha="left", va="center", fontsize=ann_size,
                    color="#52616A")
    ax.vlines(X(0), -0.7, len(rows) - 0.3, color=RULE, ls="-", lw=0.5, zorder=3)
    ax.annotate("", xy=(px1, len(rows) - 0.15), xytext=(px0, len(rows) - 0.15),
                arrowprops=dict(arrowstyle="->", lw=0.5, color=RULE))
    for t, xt in _ticks_for(xlim):
        ax.text(X(xt), len(rows) - 0.15 + 0.12, t, ha="center", va="top",
                fontsize=7, color=RULE)
    ax.text((px0 + px1) / 2, len(rows) - 0.15 + 0.62, xlab, ha="center", va="top",
            fontsize=7, color=MUTED)


def _ticks_for(xlim):
    import math
    x0, x1 = xlim
    raw = [x0 + (x1 - x0) * k / 6 for k in range(7)]
    step = None
    span = x1 - x0
    for cand in (0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0):
        if span / cand <= 6:
            step = cand
            break
    step = step or span / 6
    start = math.ceil(x0 / step) * step
    out, v = [], start
    while v <= x1 + 1e-9:
        out.append(("%g" % v, v))
        v += step
    return out


def tint_like(hex_color, strength=0.45):
    r, g, b = (int(hex_color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    f = lambda c: int(round(255 - (255 - c) * strength))
    return "#%02X%02X%02X" % (f(r), f(g), f(b))


# ── vector flow-diagram helpers (pure matplotlib, no PPT) ──
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Polygon


def grouped(ncat, nseries, span=0.5, intra=0.1):
    """Task-book v4 grouped bars: group span 0.5 of the category slot,
    intra-group gap = 0.1 x bar width. Returns (positions, bar_width)."""
    wb = span / (nseries + intra * (nseries - 1))
    offs = [(k - (nseries - 1) / 2) * (wb + intra * wb) for k in range(nseries)]
    pos = [[i + o for i in range(ncat)] for o in offs]
    return pos, wb


def tint(hex_color, strength=0.30):
    r, g, b = (int(hex_color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    f = lambda c: int(round(255 - (255 - c) * strength))
    return "#%02X%02X%02X" % (f(r), f(g), f(b))


def flow_ax(fig, rect=(0, 0, 1, 1)):
    ax = fig.add_axes(rect)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    return ax


def rbox(ax, x, y, w, h, title, sub=None, fc="#FFFFFF", ec=RULE, lw=0.8,
         ts=8, ss=7, tc="#26343B", sc="#52616A", rounding=0.010, aspect=1.0,
         ls="-", zorder=3):
    """Rounded box; title bold centered (upper), sub gray centered (lower)."""
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0,rounding_size=%.4f" % rounding,
        mutation_aspect=aspect, fc=fc, ec=ec, lw=lw, ls=ls, zorder=zorder))
    if sub is None:
        ax.text(x + w / 2, y + h / 2, title, ha="center", va="center",
                fontsize=ts, color=tc, fontweight="bold", linespacing=1.3,
                zorder=zorder + 1)
    else:
        ax.text(x + w / 2, y + h * 0.66, title, ha="center", va="center",
                fontsize=ts, color=tc, fontweight="bold", linespacing=1.25,
                zorder=zorder + 1)
        ax.text(x + w / 2, y + h * 0.30, sub, ha="center", va="center",
                fontsize=ss, color=sc, linespacing=1.25, zorder=zorder + 1)


def note_box(ax, x, y, w, h, text, ts=7, fc="#FFFFFF", tc="#52616A",
             aspect=1.0, align="left", rounding=0.008, zorder=2):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0,rounding_size=%.4f" % rounding,
        mutation_aspect=aspect, fc=fc, ec="none", zorder=zorder))
    tx = x + 0.012 if align == "left" else x + w / 2
    ax.text(tx, y + h / 2, text, ha=align if align == "left" else "center",
            va="center", fontsize=ts, color=tc, linespacing=1.45,
            zorder=zorder + 1)


def farrow(ax, p1, p2, color=RULE, lw=0.8, ms=7, ls="-", zorder=4):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle="-|>", mutation_scale=ms,
                                 color=color, lw=lw, linestyle=ls, zorder=zorder,
                                 shrinkA=0, shrinkB=0))


def polyline(ax, pts, color=RULE, lw=0.8, ls="-", arrow_end=True, ms=7,
             zorder=4):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    ax.plot(xs, ys, color=color, lw=lw, ls=ls, solid_capstyle="butt",
            zorder=zorder)
    if arrow_end:
        (x1, y1), (x2, y2) = pts[-2], pts[-1]
        farrow(ax, (x1, y1), (x2, y2), color=color, lw=lw, ms=ms, ls="-",
               zorder=zorder)


def diamond(ax, cx, cy, w, h, text, fc="#FAF0D8", ec="#BE9346", ts=6.8,
            zorder=3):
    ax.add_patch(Polygon([(cx, cy + h / 2), (cx + w / 2, cy),
                          (cx, cy - h / 2), (cx - w / 2, cy)],
                         closed=True, fc=fc, ec=ec, lw=0.8, zorder=zorder))
    ax.text(cx, cy, text, ha="center", va="center", fontsize=ts,
            color="#26343B", fontweight="bold", zorder=zorder + 1)


def _is_cjk(ch):
    o = ord(ch)
    return (0x2E80 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF
            or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF)


_FCACHE = {}
_SCALE_PX = 16.0


def _is_cjk(ch):
    o = ord(ch)
    return (0x2E80 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF
            or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF)


_FCACHE = {}
_SCALE_PX = 16.0


def _is_cjk(ch):
    o = ord(ch)
    return (0x2E80 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF
            or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF)


def _font(size_pt, bold, cjk):
    key = (round(size_pt, 1), bool(bold), bool(cjk))
    if key not in _FCACHE:
        from PIL import ImageFont
        if cjk:
            path = "C:/Windows/Fonts/simsun.ttc"
        elif bold:
            path = "C:/Windows/Fonts/timesbd.ttf"
        else:
            path = "C:/Windows/Fonts/times.ttf"
        _FCACHE[key] = ImageFont.truetype(path, int(round(size_pt * _SCALE_PX)))
    return _FCACHE[key]


def wrap_cm(text, max_w_cm, size_pt, bold=False):
    out = []
    for para in str(text).split("\n"):
        if not para.strip():
            out.append("")
            continue
        toks, cur = [], ""
        for ch in para:
            if _is_cjk(ch) or ch == " ":
                if cur:
                    toks.append(cur)
                    cur = ""
                toks.append(ch)
            else:
                cur += ch
        if cur:
            toks.append(cur)
        line = ""
        for tk in toks:
            trial = line + tk
            if line and text_w_cm(trial.rstrip(), size_pt, bold) > max_w_cm:
                out.append(line.rstrip())
                line = tk if tk != " " else ""
            else:
                line = trial
        out.append(line.rstrip())
    return out


def line_h_cm(size_pt):
    return size_pt * 1.2 / 72.0 * 2.54


def text_w_cm(text, size_pt, bold=False):
    total = 0.0
    for ch in str(text):
        f = _font(size_pt, bold, _is_cjk(ch))
        total += f.getlength(ch)
    return total / _SCALE_PX / 72.0 * 2.54

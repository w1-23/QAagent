# -*- coding: utf-8 -*-
"""Publication style for the statistical figures (Fig.3/Fig.4), per task-book §3.

Design language: white canvas, Arial/Helvetica, 0.5 pt #333 axes, no top/right
spines, inward ticks, Y-only dashed grid (#E5E5E5, 0.3 pt), frameon=False
legends, no shadow/3-D. Exports editable SVG (fonttype='none' → real text
objects) + PDF + PNG + 600 dpi TIFF.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from pathlib import Path

# palette (same fixed system as the structural figures)
C = {
    "rag": "#4F8868", "rag_bg": "#E7F1E5",
    "web": "#C87558", "web_bg": "#FBEAE1",
    "sup": "#287D99", "sup_bg": "#E4F2F8",
    "audit": "#BE9346", "audit_bg": "#FAF0D8",
    "meta": "#7772A8", "meta_bg": "#F0EDFA",
    "text": "#26343B", "text2": "#52616A",
    "border": "#D4DCE0", "bg": "#FFFFFF",
    "grid": "#E5E5E5",
}
JUDGE_COLOR = {"kimi-k2.6": C["sup"], "glm-5.3": C["audit"]}
HEAT_CMAP = LinearSegmentedColormap.from_list("citrus_heat", ["#F2F7EF", "#BDD8B4", "#4F8868"])
MM = 1 / 25.4


def use_style():
    plt.rcParams.update({
        "font.family": ["Arial", "Helvetica", "DejaVu Sans"],
        "font.size": 8,
        "axes.titlesize": 9.5,
        "axes.titleweight": "bold",
        "axes.labelsize": 8,
        "axes.labelcolor": C["text"],
        "axes.edgecolor": "#333333",
        "axes.linewidth": 0.5,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": C["grid"],
        "grid.linewidth": 0.3,
        "grid.linestyle": "--",
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "xtick.color": C["text"],
        "ytick.color": C["text"],
        "xtick.direction": "in",
        "ytick.direction": "in",
        "xtick.major.size": 2,
        "ytick.major.size": 2,
        "xtick.major.width": 0.5,
        "ytick.major.width": 0.5,
        "legend.fontsize": 7.5,
        "legend.frameon": False,
        "figure.facecolor": "white",
        "figure.dpi": 110,
        "savefig.facecolor": "white",
        "svg.fonttype": "none",          # keep text as editable <text> nodes
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    })


def open_fig(w_mm, h_mm):
    fig = plt.figure(figsize=(w_mm * MM, h_mm * MM))
    return fig


def finish(ax, ygrid_only=True):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y" if ygrid_only else "both", color=C["grid"], lw=0.3, ls="--")
    ax.grid(axis="x", visible=not ygrid_only)


def save(fig, out_base: Path, tag: str = ""):
    """Exact-size export (no tight cropping) so the canvas is precisely 174 mm wide."""
    out_base = Path(out_base)
    out_base.parent.mkdir(parents=True, exist_ok=True)
    paths = {}
    for ext, kw in (("svg", {}), ("pdf", {}), ("eps", {}),
                    ("png", {"dpi": 300}), ("tif", {"dpi": 600})):
        p = out_base.with_suffix("." + ext)
        fig.savefig(p, bbox_inches=None, pad_inches=0, **kw)
        paths[ext] = str(p)
    plt.close(fig)
    return paths


def annot(ax, x, y, s, size=6.8, color=None, ha="center", va="bottom", weight="normal"):
    ax.text(x, y, s, fontsize=size, ha=ha, va=va, color=color or C["text"], fontweight=weight)


def bar_with_ci(ax, xs, vals, cis, colors, width=0.62, label=None):
    """Bars with 95% CI whiskers (caps), value labels above."""
    import numpy as np
    for x, v, ci, col in zip(xs, vals, cis, colors):
        ax.bar(x, v, width=width, color=col, edgecolor="#333333", linewidth=0.4, label=label)
        lo, hi = ci
        ax.plot([x, x], [lo, hi], color="#333333", lw=0.8, solid_capstyle="butt", zorder=5)
        ax.plot([x - 0.10, x + 0.10], [hi, hi], color="#333333", lw=0.8, zorder=5)
        ax.plot([x - 0.10, x + 0.10], [lo, lo], color="#333333", lw=0.8, zorder=5)
        annot(ax, x, hi + 0.015, f"{v*100:.1f}", size=6.8, va="bottom")


def forest(ax, labels, deltas, cis, mdds, color, xlabel, title):
    """Paired-difference forest: point + 95% CI, 0 line, MDD-guided marker fill."""
    import numpy as np
    ys = np.arange(len(labels))[::-1]
    for y, d, ci, m in zip(ys, deltas, cis, mdds):
        lo, hi = ci
        ax.plot([lo, hi], [y, y], color=color, lw=1.0, solid_capstyle="butt", zorder=3)
        ax.plot([lo, lo], [y - .16, y + .16], color=color, lw=1.0, zorder=3)
        ax.plot([hi, hi], [y - .16, y + .16], color=color, lw=1.0, zorder=3)
        exceeds = abs(d) > m
        ax.plot([d], [y], marker="o", ms=4.6, zorder=4,
                markerfacecolor=color if exceeds else "white",
                markeredgecolor=color, markeredgewidth=0.9)
        ax.annotate(f"{d:+.3f}", (max(hi, d) + 0.03, y), fontsize=6.6,
                    va="center", ha="left", color=C["text"])
    ax.axvline(0, color="#333333", lw=0.8, ls="--", zorder=1)
    los = [c[0] for c in cis] + [0.0]
    his = [c[1] for c in cis] + [0.0]
    ax.set_xlim(min(los) - 0.07, max(his + list(deltas)) + 0.30)
    ax.set_yticks(ys)
    ax.set_yticklabels(labels)
    ax.set_ylim(-0.7, len(labels) - 0.3)
    ax.set_xlabel(xlabel)
    ax.set_title(title, loc="left")
    finish(ax, ygrid_only=False)
    ax.grid(axis="y", visible=False)


def heat_legend(ax, vmin, vmax, n=6, label="Mean score"):
    """Vector colour-scale legend (matplotlib's colorbar uses an AxesImage, which
    would be embedded as a raster <image> in the SVG export)."""
    from matplotlib.patches import Rectangle
    from matplotlib.colors import Normalize
    norm = Normalize(vmin, vmax)
    for i in range(n):
        v0 = vmin + (vmax - vmin) * i / n
        ax.add_patch(Rectangle((0, i), 1, 1, facecolor=HEAT_CMAP(norm(v0 + (vmax - vmin) / (2 * n))),
                               edgecolor="#FFFFFF", linewidth=0.4))
    ax.set_xlim(-0.2, 4.6)
    ax.set_ylim(0, n)
    ax.set_xticks([])
    ax.set_yticks([0, n])
    ax.set_yticklabels([f"{vmin:.1f}", f"{vmax:.1f}"], fontsize=7)
    for s in ("top", "right", "bottom", "left"):
        ax.spines[s].set_visible(False)
    ax.grid(False)
    ax.tick_params(length=0)
    ax.set_title(label, loc="left", fontsize=7.5, fontweight="normal")


def forest_paired(ax, labels, deltas, cis, color, xlabel, title,
                  mean_label="Mean (D3–D10)", mean_delta=None, mean_ci=None):
    """Paired-effect forest, PI ruling 2026-10-16.

    Point estimate + 95% paired CI + zero reference line only: no 80% power band,
    no MDD classification (markers are uniform). The summary row is separated by a
    thin rule and labelled 'Mean (D3–D10)' (never 'Final effect'/'End-to-end').
    """
    import numpy as np
    ys = list(np.arange(len(labels))[::-1])
    for y, d, ci in zip(ys, deltas, cis):
        lo, hi = ci
        ax.plot([lo, hi], [y, y], color=color, lw=1.0, solid_capstyle="butt", zorder=3)
        ax.plot([lo, lo], [y - .16, y + .16], color=color, lw=1.0, zorder=3)
        ax.plot([hi, hi], [y - .16, y + .16], color=color, lw=1.0, zorder=3)
        ax.plot([d], [y], marker="o", ms=4.6, zorder=4,
                markerfacecolor=color, markeredgecolor=color, markeredgewidth=0.9)
        ax.annotate(f"{d:+.3f}", (max(hi, d) + 0.03, y), fontsize=6.6,
                    va="center", ha="left", color=C["text"])
    ax.axvline(0, color="#333333", lw=0.8, ls="--", zorder=1)
    yticks, ylabels = list(ys), list(labels)
    xmax_candidates = [max(c[1], d) for c, d in zip(cis, deltas)] + [0.0]
    xmin_candidates = [min(c[0], d) for c, d in zip(cis, deltas)] + [0.0]
    if mean_delta is not None:
        y_m = -1.35
        ax.plot([mean_ci[0], mean_ci[1]], [y_m, y_m], color=color, lw=1.2,
                solid_capstyle="butt", zorder=3)
        ax.plot([mean_ci[0], mean_ci[0]], [y_m - .18, y_m + .18], color=color, lw=1.2, zorder=3)
        ax.plot([mean_ci[1], mean_ci[1]], [y_m - .18, y_m + .18], color=color, lw=1.2, zorder=3)
        ax.plot([mean_delta], [y_m], marker="D", ms=5.0, zorder=4,
                markerfacecolor=color, markeredgecolor=color)
        ax.annotate(f"{mean_delta:+.3f}", (max(mean_ci[1], mean_delta) + 0.03, y_m),
                    fontsize=6.8, va="center", ha="left", color=C["text"], weight="bold")
        # separator above the summary row
        ax.axhline(ys[-1] - 0.72, color=C["border"], lw=0.6, zorder=1)
        yticks.append(y_m)
        ylabels.append(mean_label)
        xmax_candidates.append(max(mean_ci[1], mean_delta))
        xmin_candidates.append(min(mean_ci[0], mean_delta))
    ax.set_yticks(yticks)
    ax.set_yticklabels(ylabels)
    for t, lab in zip(ax.get_yticklabels(), ylabels):
        if lab.startswith("Mean"):
            t.set_fontweight("bold")
    ax.set_ylim(-2.05, len(labels) - 0.3)
    ax.set_xlim(min(xmin_candidates) - 0.09, max(xmax_candidates) + 0.22)
    ax.set_xlabel(xlabel)
    ax.set_title(title, loc="left")
    finish(ax, ygrid_only=False)
    ax.grid(axis="y", visible=False)


def heatmap(ax, rows, cols, M, vmin, vmax, title):
    """Vector heatmap: one Rectangle per cell (imshow would be embedded as a
    raster <image> in the SVG backend, breaking editability)."""
    import numpy as np
    from matplotlib.patches import Rectangle
    from matplotlib.colors import Normalize
    from matplotlib.cm import ScalarMappable
    norm = Normalize(vmin, vmax)
    A = np.array(M, dtype=float)
    nr, nc = A.shape
    for i in range(nr):
        for j in range(nc):
            v = A[i, j]
            fc = HEAT_CMAP(norm(v)) if v == v else (1, 1, 1, 1)
            ax.add_patch(Rectangle((j - 0.5, i - 0.5), 1, 1, facecolor=fc,
                                   edgecolor="#FFFFFF", linewidth=0.5))
            if v == v:
                ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=6.4,
                        color="#FFFFFF" if v > (vmin + vmax) / 2 else C["text"])
    ax.set_xlim(-0.5, nc - 0.5)
    ax.set_ylim(nr - 0.5, -0.5)
    ax.set_xticks(range(len(cols)), cols)
    ax.set_yticks(range(len(rows)), rows)
    ax.set_title(title, loc="left")
    ax.grid(False)
    ax.tick_params(length=0)
    for s in ("top", "right", "bottom", "left"):
        ax.spines[s].set_visible(False)
    return ScalarMappable(norm=norm, cmap=HEAT_CMAP)

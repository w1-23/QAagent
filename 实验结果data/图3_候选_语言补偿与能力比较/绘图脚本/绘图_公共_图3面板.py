# -*- coding: utf-8 -*-
"""图 3 家族 · 共用面板绘制函数（候选甲 / 候选乙 共用，避免重复实现）

只负责**画出 figure 并返回**；对齐门与导出由各候选脚本自己显式写出，
以便静态校验能直接看到 rcParams 与 savefig 设定。
"""
from __future__ import annotations

import pathlib
import sys

DESIGN_SYSTEM = pathlib.Path(
    r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\01_design_system")
if str(DESIGN_SYSTEM) not in sys.path:
    sys.path.insert(0, str(DESIGN_SYSTEM))
import fig_style_ha as S  # noqa: E402

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, Normalize  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

DIMS = [f"D{i}" for i in range(1, 11)]
HEAT = LinearSegmentedColormap.from_list("ha_heat", ["#FFFFFF", "#BDD8B4", "#4F8868"])
VMIN, VMAX = 1.0, 5.0
TRACKS = ["Track A", "Track B", "Track C", "Track D", "Track E"]


def cross_language_fig(snap):
    """3a 跨语言四配置点图。"""
    S.use_style()
    cross = snap["cross_language"]
    order = ["ZH–ZH", "ZH–EN", "EN–ZH", "EN–EN"]
    fig = S.open_fig(174, 62)
    ax = fig.add_axes([0.085, 0.20, 0.88, 0.68])
    # 不铺大面积彩色背景：分组信息由点的颜色（ZH 蓝 / EN 绿）与轴标签承担
    for x, lab in zip(range(len(order)), order):
        d = cross[lab]
        col = S.CORE["blue"] if lab.startswith("ZH") else S.CORE["green"]
        lo, hi = d["ci"]
        ax.plot([x, x], [lo, hi], color=col, lw=1.0, solid_capstyle="butt", zorder=3)
        for yy in (lo, hi):
            ax.plot([x - 0.07, x + 0.07], [yy, yy], color=col, lw=1.0, zorder=3)
        ax.plot([x], [d["rate"]], marker="o", ms=6.6, zorder=4,
                markerfacecolor=col, markeredgecolor=col)
        ax.text(x, hi + 3.2, f"{d['rate']:.1f}%", ha="center", va="bottom",
                fontsize=S.FS["value"], color=S.NEUTRAL["text"])
    ax.set_xticks(list(range(len(order))))
    ax.set_xticklabels(order, fontsize=S.FS["value"], color=S.NEUTRAL["text"])
    ax.set_xlim(-0.5, 3.5)
    ax.set_ylim(0, 100)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_ylabel("Gold evidence in top 10 (%)")
    S.finish(ax, ygrid_only=True)
    return fig


def heat_fig(snap, judge: str, cols=None, cell_fs=8.0, fig_h=74.0):
    """五配置 × 维度 热图（单判官）。cols 可裁到 D3–D10 以降低视觉密度。"""
    S.use_style()
    heat = snap["heat"][judge]
    cols = cols or DIMS
    fig = S.open_fig(174, fig_h)
    ax = fig.add_axes([0.145, 0.175, 0.80, 0.66])
    ax.set_facecolor(S.NEUTRAL["canvas"])
    norm = Normalize(VMIN, VMAX)
    for i, tk in enumerate(TRACKS):
        for j, ci in enumerate(cols):
            v = heat[tk][DIMS.index(ci)]
            if v is None:
                ax.add_patch(Rectangle((j - 0.5, i - 0.5), 1, 1, facecolor=S.NEUTRAL["na"],
                                       edgecolor="#FFFFFF", linewidth=0.6, zorder=1))
                ax.text(j, i, "NA", ha="center", va="center", fontsize=cell_fs,
                        color=S.NEUTRAL["text2"], zorder=2)
            else:
                ax.add_patch(Rectangle((j - 0.5, i - 0.5), 1, 1, facecolor=HEAT(norm(v)),
                                       edgecolor="#FFFFFF", linewidth=0.6, zorder=1))
                ax.text(j, i, f"{v:.1f}", ha="center", va="center", fontsize=cell_fs,
                        color=S.NEUTRAL["text"] if v < 3.6 else "#FFFFFF", zorder=2)

    strip_y0, strip_h, x0, x1 = len(TRACKS) + 0.85, 0.42, 0.0, 6.4
    n = 64
    for k in range(n):
        vv = VMIN + (VMAX - VMIN) * (k + 0.5) / n
        ax.add_patch(Rectangle((x0 + (x1 - x0) * k / n, strip_y0), (x1 - x0) / n, strip_h,
                               facecolor=HEAT(norm(vv)), edgecolor="none", zorder=1))
    ax.text(x0, strip_y0 + strip_h + 0.22, f"{VMIN:.0f}", ha="center", va="top",
            fontsize=S.FS["tick"], color=S.NEUTRAL["text2"])
    ax.text(x1, strip_y0 + strip_h + 0.22, f"{VMAX:.0f}", ha="center", va="top",
            fontsize=S.FS["tick"], color=S.NEUTRAL["text2"])
    ax.text(x1 + 0.5, strip_y0 + strip_h / 2, "Score (1–5)", ha="left", va="center",
            fontsize=S.FS["legend"], color=S.NEUTRAL["text2"])

    ax.set_xlim(-0.5, len(cols) - 0.5)
    ax.set_ylim(len(TRACKS) + 1.75, -0.5)
    ax.set_xticks([])
    for j, c in enumerate(cols):
        ax.text(j, len(TRACKS) - 0.30, c, ha="center", va="top",
                fontsize=S.FS["tick"], color=S.NEUTRAL["text"])
    ax.set_yticks(range(len(TRACKS)))
    ax.set_yticklabels(TRACKS, fontsize=S.FS["value"], color=S.NEUTRAL["text"])
    ax.tick_params(length=0)
    ax.grid(False)
    for sp in ("top", "right", "bottom", "left"):
        ax.spines[sp].set_visible(False)
    ax.set_title(S.JUDGE_LABEL[judge], loc="left", fontsize=S.FS["title"],
                 color=S.NEUTRAL["text"], pad=6)
    return fig


def forest_fig(snap, judge: str):
    """A−B 逐维配对差森林图（单判官，共用 −0.85…+1.62 尺度）。"""
    S.use_style()
    items = snap["forest"][judge]
    col = S.JUDGE[judge]
    fig = S.open_fig(174, 84)
    ax = fig.add_axes([0.255, 0.155, 0.63, 0.755])
    ys = list(range(len(items)))[::-1]
    mean_y = None
    for y, it in zip(ys, items):
        is_mean = it["dim"] == "mean_D3_D10"
        lo, hi, d = it["lo"], it["hi"], it["delta"]
        lw = 1.2 if is_mean else 1.0
        ax.plot([lo, hi], [y, y], color=col, lw=lw, solid_capstyle="butt", zorder=3)
        for xx in (lo, hi):
            ax.plot([xx, xx], [y - 0.17, y + 0.17], color=col, lw=lw, zorder=3)
        ax.plot([d], [y], marker="D" if is_mean else "o", ms=5.6 if is_mean else 4.8,
                zorder=4, markerfacecolor=col, markeredgecolor=col)
        ax.text(1.022, y, f"{d:+.3f}", transform=ax.get_yaxis_transform(),
                ha="left", va="center", fontsize=S.FS["value"], color=S.NEUTRAL["text"],
                fontweight="bold" if is_mean else "normal")
        if is_mean:
            mean_y = y
    if mean_y is not None:
        ax.axhline(mean_y + 0.62, color=S.NEUTRAL["border"], lw=0.6, zorder=1)
    ax.axvline(0, color="#333333", lw=0.7, ls="--", zorder=1)
    ax.set_yticks(ys)
    ax.set_yticklabels([it["row"] for it in items], fontsize=S.FS["value"], color=S.NEUTRAL["text"])
    for t, it in zip(ax.get_yticklabels(), items):
        if it["dim"] == "mean_D3_D10":
            t.set_fontweight("bold")
    ax.set_xlim(-0.85, 1.62)
    ax.set_ylim(-0.75, len(items) - 0.35)
    ax.set_xlabel("Paired score difference  (Track A − Track B)")
    ax.set_title(S.JUDGE_LABEL[judge], loc="left", fontsize=S.FS["title"], color=S.NEUTRAL["text"])
    S.finish(ax, ygrid_only=False)
    ax.grid(False)
    return fig

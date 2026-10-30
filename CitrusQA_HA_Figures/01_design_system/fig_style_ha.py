# -*- coding: utf-8 -*-
"""图 4 家族 · 共享样式（继承既有设计系统，仅覆盖字号与导出）

规范依据：`paper/图件方案_CitrusQA_HA投稿版.md`
  §二 视觉规范（174 mm、Arial、主体标注 ≥8 pt、EPS 字体嵌入）
  §三 配色三层体系（深色只落数据元素、浅色只作大面积填充）
  §五 nature-figure 契约（可编辑 SVG 文字、面板对齐门、导出物）

做法：**引用**既有设计系统 `CitrusQA_HA_Figures/01_design_system/fig_style.py` 的调色板与
几何规格，在本地以继承方式覆盖字号（既有实现刻度 7 pt、格内 6.4 pt，低于本项目 ≥8 pt 要求）。
不修改共享设计系统（它服务既有已完成图件）。
"""
from __future__ import annotations

import pathlib
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib as mpl
import matplotlib.pyplot as plt

# ── 引用既有设计系统（不复制、不修改）────────────────────────────────────────
DESIGN_SYSTEM = pathlib.Path(
    r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\01_design_system")
if str(DESIGN_SYSTEM) not in sys.path:
    sys.path.insert(0, str(DESIGN_SYSTEM))
import fig_style as DS  # noqa: E402

# 面板对齐门（nature-figure skill 的必需工具）
SKILL_SCRIPTS = pathlib.Path(r"C:\Users\Administrator\.dsh\skills\nature-figure\scripts")
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))
from audit_panel_alignment import require_matplotlib_panel_alignment  # noqa: E402

# ── 配色（§三 三层体系；与设计系统同值，集中在此便于核对）────────────────────
CORE = {
    "blue": "#287D99",      # Supervisor / Kimi / 原始检索基线
    "green": "#4F8868",     # Local RAG / 改进检索 / 正向高分
    "orange": "#C87558",    # Web Agent / 联网处置 / 风险
    "gold": "#BE9346",      # Assembly & Audit / GLM 配对效应
}
LIGHT = {"blue": "#E4F2F8", "green": "#E7F1E5", "orange": "#FBEAE1", "gold": "#FAF0D8"}
NEUTRAL = {
    "text": "#26343B", "text2": "#52616A",
    "border": "#D4DCE0", "grid": "#E5E5E5", "na": "#EFF1F2", "canvas": "#FFFFFF",
    "grey": "#8C9AA3",      # 中性灰：灰色基线 / 不联网对照
    "grey_light": "#A5B2B9",  # 浅灰：弱参考线 / CI 辅助线
}
JUDGE = {"kimi-k2.6": CORE["blue"], "glm-5.3": CORE["gold"]}
JUDGE_LABEL = {"kimi-k2.6": "Kimi K2.6", "glm-5.3": "GLM-5.3"}
MM = 1 / 25.4

# 本轮字号（174 mm 实际尺寸下的最终字号，全部 ≥8 pt）
FS = {"title": 9.5, "label": 9, "tick": 8, "value": 8, "legend": 8, "note": 8}


def use_style() -> None:
    """在既有设计系统之上做字号覆盖（不调用 DS.use_style，避免 7 pt 刻度）。"""
    mpl.rcParams.update({
        # HA 要求 Arial / Helvetica；本机无 Helvetica，故以 Arial 为主并给出可用回退
        "font.family": ["Arial", "DejaVu Sans"],
        "font.sans-serif": ["Arial", "DejaVu Sans"],
        "font.size": FS["value"],
        "axes.titlesize": FS["title"], "axes.titleweight": "bold",
        "axes.labelsize": FS["label"], "axes.labelcolor": NEUTRAL["text"],
        "axes.edgecolor": "#333333", "axes.linewidth": 0.6,
        "axes.grid": True, "axes.axisbelow": True,
        "grid.color": NEUTRAL["grid"], "grid.linewidth": 0.35, "grid.linestyle": "--",
        "xtick.labelsize": FS["tick"], "ytick.labelsize": FS["tick"],
        "xtick.color": NEUTRAL["text2"], "ytick.color": NEUTRAL["text2"],
        "xtick.direction": "in", "ytick.direction": "in",
        "xtick.major.size": 2.4, "ytick.major.size": 2.4,
        "xtick.major.width": 0.6, "ytick.major.width": 0.6,
        "legend.fontsize": FS["legend"], "legend.frameon": False,
        "figure.facecolor": NEUTRAL["canvas"], "savefig.facecolor": NEUTRAL["canvas"],
        "figure.dpi": 120,
        "svg.fonttype": "none",     # SVG 文字保持可编辑 <text>
        "pdf.fonttype": 42, "ps.fonttype": 42,
    })


def open_fig(w_mm: float, h_mm: float):
    return plt.figure(figsize=(w_mm * MM, h_mm * MM))


def finish(ax, ygrid_only: bool = True) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    if ygrid_only:
        ax.grid(axis="y", color=NEUTRAL["grid"], lw=0.35, ls="--")
        ax.grid(axis="x", visible=False)
    else:
        ax.grid(axis="x", color=NEUTRAL["grid"], lw=0.35, ls="--")
        ax.grid(axis="y", visible=False)


def align_gate(fig, out_base: pathlib.Path) -> None:
    """面板对齐门（nature-figure 必需）。

    单面板导出 → 记 NOT APPLICABLE（免检）；脚本内出多面板合图 → 必须过门（1.5 pt）。

    导出由各绘图脚本**显式**写出（`.svg` / `.pdf` / `.eps` / `.png`），
    便于静态校验与复现；此处不代劳 savefig。
    """
    out_base = pathlib.Path(out_base)
    out_base.parent.mkdir(parents=True, exist_ok=True)
    require_matplotlib_panel_alignment(
        fig,
        json_out=f"{out_base}.alignment.json",
        overlay_svg=f"{out_base}.alignment.svg",
        tolerance_pt=1.5, gutter_tolerance_pt=1.5, strict=True,
    )


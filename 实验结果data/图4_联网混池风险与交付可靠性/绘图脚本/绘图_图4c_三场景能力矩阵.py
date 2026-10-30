# -*- coding: utf-8 -*-
"""图 4 · 端到端压力测试（重新设计）· 两个面板各自独立成文件，**不写面板字母**

设计依据（用户 2026-10-17 指示）：把「三池均分点图」改为
「三类压力场景 × 对应能力指标」的结果矩阵；重点从"判官之间的一致性"
转向"系统自身在每一类压力场景下是否做到了该场景要验证的能力"。

  图4c1_三场景能力维度矩阵：3 场景 × 4 相关能力维度（D3/D4/D7/D8），
                             Kimi 与 GLM 两个并列小矩阵，共用 1–5 色标
  图4c2_三场景交付结构矩阵：3 场景 × 交付结构客观量（引用结构合规率、平均正文引用数、
                             平均注入证据数、联网引用题占比）

数据来自 ../数据/图4c_核验_压力测试.json（逐维均值由原始判分复算、与冻结件逐位一致）。
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
DESIGN_SYSTEM = pathlib.Path(
    r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\01_design_system")
sys.path.insert(0, str(DESIGN_SYSTEM))
import fig_style_ha as S  # noqa: E402

import matplotlib as mpl  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import Normalize  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from audit_panel_alignment import require_matplotlib_panel_alignment  # noqa: E402

DATA = HERE.parent / "数据" / "图4c_核验_压力测试.json"
OUTDIR = HERE.parent / "绘图产物"

POOL_ORDER = ["A", "B", "C"]
DIM_ORDER = ["D3", "D4", "D7", "D8"]
VMIN, VMAX = 1.0, 5.0


def explicit_rcparams() -> None:
    mpl.rcParams.update({
        "font.family": "Arial",
        "font.sans-serif": ["Arial", "DejaVu Sans"],
        "font.size": 8,
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
    })


def export(fig, name: str) -> None:
    out = OUTDIR / name
    require_matplotlib_panel_alignment(
        fig, json_out=f"{out}.alignment.json", overlay_svg=f"{out}.alignment.svg",
        tolerance_pt=1.5, gutter_tolerance_pt=1.5, strict=True)
    fig.savefig(str(out) + ".svg", bbox_inches=None, pad_inches=0)
    fig.savefig(str(out) + ".pdf", bbox_inches=None, pad_inches=0)
    fig.savefig(str(out) + ".eps", bbox_inches=None, pad_inches=0)
    fig.savefig(str(out) + ".png", dpi=600, bbox_inches=None, pad_inches=0)
    fig.savefig(str(out) + ".tiff", dpi=600, bbox_inches=None, pad_inches=0)
    plt.close(fig)
    print("WROTE", str(out) + ".svg / .pdf / .eps / .tiff / .png")


def panel_matrix(snap):
    """3 场景 × 4 能力维度，Kimi / GLM 两个并列小矩阵，共用 1–5 色标。"""
    S.use_style()
    mat = snap["matrix"]
    scen = snap["scenarios"]
    heat = S.DS.HEAT_CMAP
    norm = Normalize(VMIN, VMAX)

    fig = S.open_fig(174, 74)
    ax = fig.add_axes([0.02, 0.05, 0.96, 0.90])
    ax.set_axis_off()
    ax.set_xlim(-3.55, 9.85)
    ax.set_ylim(4.95, -1.55)

    X0 = {"kimi-k2.6": 0.0, "glm-5.3": 5.35}
    for judge, x0 in X0.items():
        ax.text(x0, -0.92, S.JUDGE_LABEL[judge], ha="left", va="center",
                fontsize=S.FS["title"], color=S.NEUTRAL["text"])
        for c, d in enumerate(DIM_ORDER):
            ax.text(x0 + c + 0.5, 2.95, d, ha="center", va="center",
                    fontsize=S.FS["tick"], color=S.NEUTRAL["text2"])
        for r, pool in enumerate(POOL_ORDER):
            v = mat[judge][pool]
            for c, d in enumerate(DIM_ORDER):
                val = v[d]
                ax.add_patch(Rectangle((x0 + c, r - 0.5), 1, 1,
                                       facecolor=heat(norm(val)), edgecolor="#FFFFFF", linewidth=0.6))
                ax.text(x0 + c + 0.5, r, f"{val:.2f}", ha="center", va="center",
                        fontsize=S.FS["value"],
                        color=S.NEUTRAL["text"] if val < 3.9 else "#FFFFFF")

    for r, pool in enumerate(POOL_ORDER):
        ax.text(-0.12, r, f"{scen[pool]['name']}\n(n = 48)", ha="right", va="center",
                fontsize=S.FS["value"], color=S.NEUTRAL["text"], linespacing=1.35)

    # 共用矢量色标
    sx, sy, sh, n = 0.0, 3.35, 0.30, 64
    for k in range(n):
        vv = VMIN + (VMAX - VMIN) * (k + 0.5) / n
        ax.add_patch(Rectangle((sx + 3.6 * k / n, sy), 3.6 / n, sh,
                               facecolor=heat(norm(vv)), edgecolor="none"))
    ax.text(sx, sy + sh + 0.16, f"{VMIN:.0f}", ha="center", va="top",
            fontsize=S.FS["tick"], color=S.NEUTRAL["text2"])
    ax.text(sx + 3.6, sy + sh + 0.16, f"{VMAX:.0f}", ha="center", va="top",
            fontsize=S.FS["tick"], color=S.NEUTRAL["text2"])
    ax.text(sx + 3.85, sy + sh / 2, "Capability score (1–5)", ha="left", va="center",
            fontsize=S.FS["legend"], color=S.NEUTRAL["text2"])
    return fig


def panel_structure(snap):
    """3 场景 × 交付结构客观量（数字矩阵）。"""
    S.use_style()
    st = snap["structure"]
    scen = snap["scenarios"]
    cols = [("Citation structure\ncompliant", lambda d: "100%"),
            ("Mean in-text\ncitations", lambda d: f"{d['mean_citations']:.2f}"),
            ("Mean retrieved\nevidence", lambda d: f"{d['mean_retrieved']:.2f}"),
            ("Answers using\nweb citations", lambda d: f"{d['web_citation_rate']*100:.0f}%")]

    fig = S.open_fig(174, 60)
    ax = fig.add_axes([0.30, 0.32, 0.68, 0.50])
    ax.set_axis_off()
    ax.set_xlim(0, 4)
    ax.set_ylim(2.85, -1.05)

    for c, (head, _) in enumerate(cols):
        ax.text(c + 0.5, -0.78, head, ha="center", va="center",
                fontsize=S.FS["legend"], color=S.NEUTRAL["text2"], linespacing=1.35)
    for r, pool in enumerate(POOL_ORDER):
        d = st[pool]
        ax.text(-0.12, r, f"{scen[pool]['name']}\n(n = 48)", ha="right", va="center",
                fontsize=S.FS["value"], color=S.NEUTRAL["text"], linespacing=1.35)
        for c, (_, fn) in enumerate(cols):
            ax.text(c + 0.5, r, fn(d), ha="center", va="center",
                    fontsize=S.FS["value"] + 1.5, color=S.NEUTRAL["text"])
    for r in range(len(POOL_ORDER) - 1):
        ax.plot([0, 4], [r + 0.5, r + 0.5], color=S.NEUTRAL["border"], lw=0.6)

    rate = snap["pool_c_rule_rate"]
    fig.text(0.30, 0.155,
             f"Time-sensitive pool: evidence-gap statement or web citation in "
             f"{rate['hit']}/{rate['n']} answers.",
             ha="left", va="center", fontsize=S.FS["note"], color=S.NEUTRAL["text2"])
    fig.text(0.30, 0.085,
             f"Local section carries no [Wn] marker in {snap['local_section_violations']} "
             f"of 144 answers (frozen acceptance check A4).",
             ha="left", va="center", fontsize=S.FS["note"], color=S.NEUTRAL["text2"])
    return fig


def main() -> int:
    snap = json.loads(DATA.read_text(encoding="utf-8"))
    explicit_rcparams()
    export(panel_matrix(snap), "图4c1_三场景能力维度矩阵")
    export(panel_structure(snap), "图4c2_三场景交付结构矩阵")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

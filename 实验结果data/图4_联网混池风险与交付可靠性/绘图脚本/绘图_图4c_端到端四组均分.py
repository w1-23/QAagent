# -*- coding: utf-8 -*-
"""图 4 · 面板三：端到端真实使用与三池压力测试（双判官分组点图）

数据取自 ../数据/图4_核验_数据溯源.json（四组均分均为十维池化均值，与冻结判分逐位一致）。
规范：Kimi 蓝 / GLM 金；共用 1–5 评分轴；两位小数放点端；同组两点用浅灰细线连接，
**不画从零延伸的条形**，避免把评分解读为长度；不画组间显著性（不同题集非同题配对）。
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
DESIGN_SYSTEM = pathlib.Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\01_design_system")
sys.path.insert(0, str(DESIGN_SYSTEM))
import fig_style_ha as S  # noqa: E402

import matplotlib as mpl  # noqa: E402
SKILL_SCRIPTS = pathlib.Path(r"C:\Users\Administrator\.dsh\skills\nature-figure\scripts")
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))
from audit_panel_alignment import require_matplotlib_panel_alignment  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

DATA = HERE.parent / "数据" / "图4_核验_数据溯源.json"
OUT = HERE.parent / "绘图产物" / "图4c_端到端四组均分"

JUDGES = ["kimi-k2.6", "glm-5.3"]


def main() -> int:
    snap = json.loads(DATA.read_text(encoding="utf-8"))
    e = snap["e2e"]
    S.use_style()
    # 显式声明（HA 字体规范 / 可编辑文字 / 不小于 8 pt）
    mpl.rcParams.update({
        "font.family": "Arial",
        "font.sans-serif": ["Arial", "DejaVu Sans"],
        "font.size": 8,
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
    })


    rows = [
        ("Real users", 132, e["real132"]),
        ("In-corpus facts", 48, e["pools"]["In-corpus facts"]),
        ("Adversarial", 48, e["pools"]["Adversarial"]),
        ("Out-of-corpus, time-sensitive", 48, e["pools"]["Out-of-corpus time-sensitive"]),
    ]

    fig = S.open_fig(174, 68)
    ax = fig.add_axes([0.315, 0.155, 0.545, 0.700])

    ys = list(range(len(rows)))[::-1]
    DODGE = 0.17          # 同组两判官上下错开，避免数值接近时点与标签重叠
    for y, (name, n, vals) in zip(ys, rows):
        kv, gv = vals[JUDGES[0]], vals[JUDGES[1]]
        yk, yg = y + DODGE, y - DODGE
        ax.plot([kv, gv], [yk, yg], color=S.NEUTRAL["border"], lw=0.9, zorder=1)
        ax.plot([kv], [yk], marker="o", ms=6.0, zorder=3,
                markerfacecolor=S.JUDGE[JUDGES[0]], markeredgecolor=S.JUDGE[JUDGES[0]])
        ax.plot([gv], [yg], marker="o", ms=6.0, zorder=3,
                markerfacecolor=S.JUDGE[JUDGES[1]], markeredgecolor=S.JUDGE[JUDGES[1]])
        ax.text(kv - 0.055, yk, f"{kv:.2f}", ha="right", va="center",
                fontsize=S.FS["value"], color=S.NEUTRAL["text"])
        ax.text(gv + 0.055, yg, f"{gv:.2f}", ha="left", va="center",
                fontsize=S.FS["value"], color=S.NEUTRAL["text"])

    ax.set_yticks(ys)
    ax.set_yticklabels([f"{name}  (n = {n})" for name, n, _ in rows],
                       fontsize=S.FS["value"], color=S.NEUTRAL["text"])
    ax.set_ylim(-0.62, len(rows) - 0.38)
    ax.set_xlim(1, 5)
    ax.set_xticks([1, 2, 3, 4, 5])
    ax.set_xlabel("Mean judge score (1–5)")
    S.finish(ax, ygrid_only=False)
    ax.grid(False)          # 关闭网格线：避免竖向网格穿过数值标签（碰撞审计要求）

    handles = [Line2D([], [], marker="o", ls="", ms=6.2,
                      markerfacecolor=S.JUDGE[j], markeredgecolor=S.JUDGE[j],
                      label=S.JUDGE_LABEL[j]) for j in JUDGES]
    ax.legend(handles=handles, loc="lower right", bbox_to_anchor=(1.0, 1.005),
              ncol=2, frameon=False, fontsize=S.FS["legend"], handletextpad=0.5,
              columnspacing=1.6)

    # 渲染期面板对齐门（nature-figure 必需；单面板 → NOT APPLICABLE）
    require_matplotlib_panel_alignment(
        fig, json_out=f"{OUT}.alignment.json", overlay_svg=f"{OUT}.alignment.svg",
        tolerance_pt=1.5, gutter_tolerance_pt=1.5, strict=True) or {}
    fig.savefig(str(OUT) + ".svg", bbox_inches=None, pad_inches=0)
    fig.savefig(str(OUT) + ".pdf", bbox_inches=None, pad_inches=0)
    fig.savefig(str(OUT) + ".eps", bbox_inches=None, pad_inches=0)
    fig.savefig(str(OUT) + ".png", dpi=600, bbox_inches=None, pad_inches=0)
    fig.savefig(str(OUT) + ".tiff", dpi=600, bbox_inches=None, pad_inches=0)
    plt.close(fig)
    print("WROTE", str(OUT) + ".svg / .pdf / .eps / .tiff / .png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""图 4 · 面板二：交付引用的结构可核查性（审计数据带）

数据取自 ../数据/图4_核验_数据溯源.json（1,649 / 100% / 0，均由检索与答案原文复算）。
形态：无边框横向数据带（三个大数字 + 短名称），不做柱列、不做卡片。
规范：100% 可解析用绿、其余数字深灰；图内不写"结构≠语义"以外的解释性长句。
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

DATA = HERE.parent / "数据" / "图4_核验_数据溯源.json"
OUT = HERE.parent / "绘图产物" / "图4b_交付引用结构可核查性"


def main() -> int:
    snap = json.loads(DATA.read_text(encoding="utf-8"))
    c = snap["citation"]
    S.use_style()
    # 显式声明（HA 字体规范 / 可编辑文字 / 不小于 8 pt）
    mpl.rcParams.update({
        "font.family": "Arial",
        "font.sans-serif": ["Arial", "DejaVu Sans"],
        "font.size": 8,
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
    })


    fig = S.open_fig(174, 38)
    ax = fig.add_axes([0.02, 0.30, 0.96, 0.58])
    ax.set_axis_off()
    ax.set_xlim(0, 3)
    ax.set_ylim(0, 1)

    marks = f"{c['markers']:,}"
    rate = f"{c['resolvable_rate']:.0f}%"
    outside = f"{c['unresolvable']}"

    items = [
        (0.5, marks, "Citation markers", S.NEUTRAL["text"]),
        (1.5, rate, "Resolvable", S.CORE["green"]),
        (2.5, outside, "Out-of-pool", S.NEUTRAL["text"]),
    ]
    for x, big, label, col in items:
        ax.text(x, 0.66, big, ha="center", va="center", fontsize=19,
                fontweight="bold", color=col)
        ax.text(x, 0.20, label, ha="center", va="center", fontsize=S.FS["label"],
                color=S.NEUTRAL["text2"])

    for x in (1.0, 2.0):
        ax.plot([x, x], [0.08, 0.98], color=S.NEUTRAL["border"], lw=0.6)

    fig.text(0.02, 0.085, "Structural traceability only · semantic support evaluated separately",
             ha="left", va="center", fontsize=S.FS["note"], color=S.NEUTRAL["text2"])
    fig.text(0.98, 0.085, f"84-question batch · mean evidence per answer {c['mean_cited']:.2f}",
             ha="right", va="center", fontsize=S.FS["note"], color=S.NEUTRAL["text2"])

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

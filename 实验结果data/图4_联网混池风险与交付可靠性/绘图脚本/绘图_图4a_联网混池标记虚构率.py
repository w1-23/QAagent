# -*- coding: utf-8 -*-
"""图 4 · 面板一：五种联网处置的模型标记虚构率（横向 lollipop）

数据一律取自 ../数据/图4_核验_数据溯源.json（核验门产物），脚本内无任何硬编码数值。
规范：No web 用中性灰、其余联网四组用陶土橙；不布星号；分母写在数值旁。
无面板字母（组图阶段另行后置添加）。
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
OUT = HERE.parent / "绘图产物" / "图4a_联网混池标记虚构率"

ORDER = ["A", "B", "C", "C'", "D"]
SHORT = {"A": "No web", "B": "Unfiltered", "C": "Receipt-filtered",
         "C'": "Model-visible", "D": "Excerpt"}


def main() -> int:
    snap = json.loads(DATA.read_text(encoding="utf-8"))
    arms = snap["arms"]
    S.use_style()
    # 显式声明（HA 字体规范 / 可编辑文字 / 不小于 8 pt）
    mpl.rcParams.update({
        "font.family": "Arial",
        "font.sans-serif": ["Arial", "DejaVu Sans"],
        "font.size": 8,
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
    })

    n = len(ORDER)
    fig = S.open_fig(174, 74)
    ax = fig.add_axes([0.255, 0.175, 0.475, 0.735])   # 右侧 27% 留给数值列

    ys = list(range(n))[::-1]                          # A 在最上
    rates, labels = [], []
    for tag in ORDER:
        rates.append(arms[tag]["rate"])
        labels.append(f"{tag} · {SHORT[tag]}")

    for i, tag in enumerate(ORDER):
        y, d = ys[i], arms[tag]
        r = d["rate"]
        c = S.NEUTRAL["grey"] if tag == "A" else S.CORE["orange"]
        solid = tag in ("A", "B")
        ax.plot([0, r], [y, y], color=c, lw=1.6 if solid else 1.1,
                solid_capstyle="butt", zorder=2)
        ax.plot([r], [y], marker="o", ms=6.4 if solid else 5.4, zorder=4,
                markerfacecolor=c if solid else S.NEUTRAL["canvas"],
                markeredgecolor=c, markeredgewidth=1.2)
        ax.text(1.035, y, f"{r:.1f}%", transform=ax.get_yaxis_transform(),
                ha="left", va="center", fontsize=S.FS["value"], color=S.NEUTRAL["text"])
        ax.text(1.175, y, f"({d['h']}/{d['n']})", transform=ax.get_yaxis_transform(),
                ha="left", va="center", fontsize=S.FS["value"], color=S.NEUTRAL["text2"])

    # 与 No web 的差异括线（A ↔ B）；数值标签放在右侧留白区，避免被网格线穿过
    y_a, y_b = ys[ORDER.index("A")], ys[ORDER.index("B")]
    xb = 30.6
    ax.plot([xb, xb], [y_a, y_b], color=S.NEUTRAL["text2"], lw=0.6, zorder=5)
    for yy in (y_a, y_b):
        ax.plot([xb - 0.5, xb + 0.5], [yy, yy], color=S.NEUTRAL["text2"], lw=0.6, zorder=5)
    delta = arms["B"]["rate"] - arms["A"]["rate"]
    ax.text(1.035, (y_a + y_b) / 2, f"+{delta:.1f} pp", transform=ax.get_yaxis_transform(),
            ha="left", va="center", fontsize=S.FS["value"], color=S.NEUTRAL["text"])

    ax.set_yticks(ys)
    ax.set_yticklabels(labels, fontsize=S.FS["value"], color=S.NEUTRAL["text"])
    ax.set_ylim(-0.65, n - 0.35)
    ax.set_xlim(0, 40)
    ax.set_xticks(range(0, 41, 10))
    ax.set_xlabel("Judge-flagged hallucination rate (%)")
    S.finish(ax, ygrid_only=False)
    ax.grid(axis="x", color=S.NEUTRAL["grid"], lw=0.35, ls="--")
    ax.grid(axis="y", visible=False)

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

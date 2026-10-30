# -*- coding: utf-8 -*-
"""图 3 候选乙 · 降低五配置热图视觉密度版（**不写面板字母**）

与候选甲的唯一差别：热图**只画 D3–D10**，去掉 D1/D2 两列结构性 NA，
使热图墨水量与注意力负担下降；森林图仍与候选甲一致（A−B 配对效应 + 95% CI）。

输出：图3乙_五配置热图_GLM / 图3乙_五配置热图_Kimi
数据来自 ../数据/图3_核验_数据溯源.json；绘制函数见 绘图_公共_图3面板.py。
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(pathlib.Path(
    r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\01_design_system")))
import fig_style_ha as S  # noqa: E402
import 绘图_公共_图3面板 as P  # noqa: E402

import matplotlib as mpl  # noqa: E402
SKILL_SCRIPTS = pathlib.Path(r"C:\Users\Administrator\.dsh\skills\nature-figure\scripts")
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))
from audit_panel_alignment import require_matplotlib_panel_alignment  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

DATA = HERE.parent / "数据" / "图3_核验_数据溯源.json"
OUTDIR = HERE.parent / "绘图产物"
COLS = [f"D{i}" for i in range(3, 11)]        # D3–D10：去掉结构性 NA 的两列


def export(fig, name: str) -> None:
    out = OUTDIR / name
        # 渲染期面板对齐门（nature-figure 必需；单面板 → NOT APPLICABLE）
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


def main() -> int:
    snap = json.loads(DATA.read_text(encoding="utf-8"))
    # 显式声明（HA 字体规范 / 可编辑文字 / 不小于 8 pt）
    mpl.rcParams.update({
        "font.family": "Arial",
        "font.sans-serif": ["Arial", "DejaVu Sans"],
        "font.size": 8,
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
    })
    export(P.heat_fig(snap, "glm-5.3", cols=COLS, fig_h=68.0), "图3乙_五配置热图_GLM")
    export(P.heat_fig(snap, "kimi-k2.6", cols=COLS, fig_h=68.0), "图3乙_五配置热图_Kimi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

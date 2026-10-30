# -*- coding: utf-8 -*-
"""Fig.3 — Cross-lingual retrieval & comparative answer quality (Round 3).

Panels
  (a1) cross-lingual 2×2 retrieval (synthetic set, n = 96)  — exp1_summary.json
  (a2) language effect on the gold-anchored subset (n = 48, McNemar) — same file
  (b)(c) two judges' heatmaps: Track A–E × D3–D10 (n = 84; Track D n = 80)
  (d)(e) Track A − Track B paired differences per dimension, 95% CI (t), MDD

Recomputation source: statistics/_recompute_fig3.py → fig3_recomputed_stats.json
(two judge instruments reported separately; never averaged)
"""
import sys, io, json
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "01_design_system"))
import numpy as np
import matplotlib.gridspec as gridspec
import fig_style as S

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures")
OUT = ROOT / "Fig3"
STATS = json.loads((ROOT / "statistics/fig3_recomputed_stats.json").read_text(encoding="utf-8"))
SOURCES = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\07_交付与文档_delivery_docs"
               r"\03_图件_figures\英文_figures_en\_sources")
EXP1 = json.loads((SOURCES / "exp1_summary.json").read_text(encoding="utf-8-sig"))

DIMS = [f"D{i}" for i in range(3, 11)]           # D3–D10 (D1/D2 excluded cross-config)
TRACKS = ["A", "B", "C", "D", "E"]
CFG_OF_TRACK = {"A": "citrusqa", "B": "same_base_trackA", "C": "same_base_trackB",
                "D": "mainstream_trackA", "E": "mainstream_trackB"}
# gold-anchored (n = 48) language effect — located by content, keys carry full-width parens
_ANCHOR_KEY = next(k for k in EXP1["by_anchor"] if "带锚点" in k)
ANCHOR_EFF = EXP1["by_anchor"][_ANCHOR_KEY]["effects"]["passed_gold"]["检索语言效应@精排raw"]

W_MM, H_MM = 174.0, 142.0


def main():
    S.use_style()
    fig = S.open_fig(W_MM, H_MM)
    gs = gridspec.GridSpec(2, 2, figure=fig, height_ratios=[1.22, 1.0],
                           width_ratios=[1.0, 1.75], hspace=0.46, wspace=0.24,
                           left=0.118, right=0.985, top=0.925, bottom=0.235)

    # ── (a1) cross-lingual 2×2, n = 96 ──────────────────────────────────
    gs_a = gridspec.GridSpecFromSubplotSpec(2, 1, subplot_spec=gs[0, 0],
                                            height_ratios=[1.35, 1.0], hspace=0.65)
    ax = fig.add_subplot(gs_a[0])
    cells = ["Z_Z", "Z_E", "E_Z", "E_E"]
    labels = ["ZH q\nZH rr", "ZH q\nEN rr", "EN q\nZH rr", "EN q\nEN rr"]
    ov = EXP1["overall"]["cells"]
    vals = [ov[c]["passed_gold_rate"] for c in cells]
    cis = [ov[c]["passed_gold_ci95"] for c in cells]
    cols = [S.C["web"], S.C["web"], S.C["rag"], S.C["rag"]]
    S.bar_with_ci(ax, range(4), vals, cis, cols)
    ax.set_xticks(range(4), labels)
    ax.set_ylim(0, 1.55)
    ax.set_ylabel("Gold passed rate")
    ax.set_title("(a1) Retrieval language (n = 96)", loc="left")
    ax.legend(handles=[
        S.plt.Line2D([], [], marker="s", ls="", color=S.C["web"], label="Chinese query"),
        S.plt.Line2D([], [], marker="s", ls="", color=S.C["rag"], label="English (faithful)"),
    ], loc="upper center", ncols=2, fontsize=6.6, handletextpad=0.4, columnspacing=1.0)
    S.finish(ax)

    # ── (a2) language effect on gold-anchored subset, n = 48 ────────────
    ax2 = fig.add_subplot(gs_a[1])
    eff = ANCHOR_EFF
    yA, yB = 1, 0
    for y, rate, ci, col, name in ((yA, eff["rate_a"], eff["ci_a"], S.C["rag"], "EN query"),
                                   (yB, eff["rate_b"], eff["ci_b"], S.C["web"], "ZH query")):
        ax2.plot([ci[0], ci[1]], [y, y], color=col, lw=1.0)
        ax2.plot([ci[0], ci[0]], [y - .10, y + .10], color=col, lw=1.0)
        ax2.plot([ci[1], ci[1]], [y - .10, y + .10], color=col, lw=1.0)
        ax2.plot([rate], [y], "o", ms=5, color=col)
        ax2.annotate(f"{rate*100:.1f}%", (ci[1] + 0.012, y), fontsize=6.6, va="center")
    ax2.annotate("", xy=(eff["rate_a"], yA - .22), xytext=(eff["rate_b"], yB + .22),
                 arrowprops=dict(arrowstyle="->", lw=0.8, color="#333333"))
    ax2.annotate(f"+{abs(eff['diff'])*100:.1f} pp\nMcNemar p = {eff['mcnemar']['p']:.3f}",
                 (0.02, 0.60), xycoords="axes fraction", ha="left", va="bottom", fontsize=6.6)
    ax2.set_yticks([yA, yB], ["EN", "ZH"])
    ax2.set_xlim(0.6, 1.06)
    ax2.set_ylim(-0.5, 1.5)
    ax2.set_xlabel("Gold passed rate")
    ax2.set_title("(a2) Gold-anchored subset (n = 48)", loc="left")
    S.finish(ax2, ygrid_only=False)
    ax2.grid(False)

    # ── (b)(c) judge heatmaps ───────────────────────────────────────────
    gs_bc = gridspec.GridSpecFromSubplotSpec(1, 3, subplot_spec=gs[0, 1],
                                             width_ratios=[1, 1, 0.13], wspace=0.34)
    im = None
    for k, judge in enumerate(("kimi-k2.6", "glm-5.3")):
        axh = fig.add_subplot(gs_bc[k])
        means = STATS["by_judge"][judge]["per_dim_means"]
        M = [[means[CFG_OF_TRACK[t]][d] for d in DIMS] for t in TRACKS]
        S.heatmap(axh, TRACKS, DIMS, M, 2.0, 5.0,
                  f"({'bc'[k]}) Judge {k+1}: {judge}")
        axh.set_ylabel("Track" if k == 0 else "")
    axcb = fig.add_subplot(gs_bc[2])
    S.heat_legend(axcb, 2.0, 5.0, label="Score")

    # ── (d)(e) paired-difference forests (PI ruling 2026-10-16) ──────────
    #    8 dimension rows D3–D10 + a separated summary row "Mean (D3–D10)";
    #    point estimate + 95% paired CI + zero line only (no MDD band / verdict).
    FOREST = json.loads((ROOT / "statistics/fig3_forest_stats.json").read_text(encoding="utf-8"))
    DIM_LABEL = {d: f"{d} {s}" for d, s in (
        ("D3", "Factual correctness"), ("D4", "Completeness"), ("D5", "Key-point coverage"),
        ("D6", "Temporal validity"), ("D7", "Scope honesty"), ("D8", "Uncertainty calibration"),
        ("D9", "Presentation"), ("D10", "Conciseness"))}
    gs_de = gridspec.GridSpecFromSubplotSpec(1, 3, subplot_spec=gs[1, :],
                                             width_ratios=[0.13, 1.0, 1.0], wspace=0.95)
    for k, judge in enumerate(("kimi-k2.6", "glm-5.3")):
        axf = fig.add_subplot(gs_de[k + 1])
        blk = FOREST["by_judge"][judge]
        labels = [DIM_LABEL[d] for d in DIMS]
        deltas = [blk["per_dimension"][d]["delta"] for d in DIMS]
        cis = [blk["per_dimension"][d]["ci95"] for d in DIMS]
        mrow = blk["mean_d3_d10"]
        S.forest_paired(axf, labels, deltas, cis, S.JUDGE_COLOR[judge],
                        "Track A − Track B (paired)",
                        f"({'de'[k]}) {judge}",
                        mean_delta=mrow["delta"], mean_ci=mrow["ci95"])
    fig.text(0.055, 0.025, "No 80 % power band and no MDD verdict are drawn (PI ruling); "
                           "MDD definitions live in Methods/caption.",
             fontsize=6.4, color=S.C["text2"], va="bottom")
    fig.text(0.055, 0.070, "D1/D2 are excluded (not commonly comparable between Track A and Track B); "
                           "D3–D10 are paired per question.",
             fontsize=6.4, color=S.C["text2"], va="bottom")
    fig.text(0.055, 0.115, "Mean (D3–D10): per-question Δᵢ = mean over d = 3…10 of (S_A − S_B), "
                           "then paired mean with 95% CI (t).",
             fontsize=6.4, color=S.C["text2"], va="bottom")
    fig.text(0.055, 0.160, "Heatmaps (b)(c) use mean blind scores on D3–D10, n = 84 per track "
                           "(Track D n = 80); the two judges are never averaged.",
             fontsize=6.4, color=S.C["text2"], va="bottom")
    return fig


if __name__ == "__main__":
    fig = main()
    paths = S.save(fig, OUT / "fig3_retrieval")
    cell_keys = ["Z_Z", "Z_E", "E_Z", "E_E"]
    ev = {
        "figure": "Fig3",
        "panels": {
            "a1": {"data": "exp1_summary.json overall (n=96)",
                   "cells": {c: EXP1["overall"]["cells"][c]["passed_gold_rate"] for c in cell_keys}},
            "a2": {"data": "exp1_summary.json by_anchor effects (n=48)",
                   "rate_en": ANCHOR_EFF["rate_a"], "rate_zh": ANCHOR_EFF["rate_b"],
                   "diff_pp": round(abs(ANCHOR_EFF["diff"]) * 100, 1),
                   "mcnemar_p": ANCHOR_EFF["mcnemar"]["p"]},
            "bc": {"data": "fig3_recomputed_stats.json per_dim_means (D3–D10; n=84/80)"},
            "de": {"data": "fig3_recomputed_stats.json A_minus_B_per_dim (t-based CI, MDD)"},
        },
        "outputs": paths,
    }
    (OUT / "fig3_qa_evidence.json").write_text(json.dumps(ev, ensure_ascii=False, indent=1),
                                               encoding="utf-8")
    print("Fig3 written:", paths)

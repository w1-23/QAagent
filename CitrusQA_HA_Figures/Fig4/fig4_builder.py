# -*- coding: utf-8 -*-
"""Fig.4 — Citation traceability, answer quality and web-input risk (Round 3).

Layout: top row (a) resolvability · (b1) citation support · (b2) answer factuality;
bottom row (c) the five web-input treatments across the full 174 mm width.
Denominators are stated per panel; the two judges are never averaged.
"""
import sys, json
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
OUT = ROOT / "Fig4"
STATS = json.loads((ROOT / "statistics/fig3_recomputed_stats.json").read_text(encoding="utf-8"))
SOURCES = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\07_交付与文档_delivery_docs"
               r"\03_图件_figures\英文_figures_en\_sources")
ARMS = json.loads((SOURCES / "web_arms_summary.json").read_text(encoding="utf-8-sig"))

W_MM, H_MM = 174.0, 142.0
ARMS_ORDER = ["A", "B", "C", "C'", "D"]
CFG_OF_TRACK = {"A": "citrusqa", "B": "same_base_trackA", "C": "same_base_trackB",
                "D": "mainstream_trackA", "E": "mainstream_trackB"}
TRACKS = ["A", "B", "C", "D", "E"]
ARM_SHORT = ["A\nno web", "B\nunfiltered", "C\nstrict\n(legacy)", "C'\nmodel-\nvisible", "D\nexcerpts"]
ARM_DELTA = {"B": ("A-B", "B"), "C'": ("A-C'", "C'")}      # stored pairwise calls

# citation resolvability layers (data_sources.CITATION_LEVELS, wxRev §3.4)
LEVELS = [
    ("Final answer\n(current batch)", 100.00),
    ("84-question\nbatch", 100.00),
    ("Three pools", 99.96),
    ("Real users", 99.78),
    ("Streaming\n(diagnostic only)", 85.07),
]


def main():
    S.use_style()
    fig = S.open_fig(W_MM, H_MM)
    gs = gridspec.GridSpec(2, 3, figure=fig, height_ratios=[1.0, 1.02],
                           width_ratios=[1.00, 0.80, 1.30],
                           hspace=0.62, wspace=0.42,
                           left=0.125, right=0.985, top=0.935, bottom=0.215)

    # ── (a) citation resolvability ──────────────────────────────────────
    ax = fig.add_subplot(gs[0, 0])
    ys = np.arange(len(LEVELS))[::-1]
    cols = [S.C["rag"]] * 4 + ["#B9C2C7"]
    for y, (name, v), col in zip(ys, LEVELS, cols):
        ax.barh(y, v - 80, left=80, height=0.62, color=col, edgecolor="#333333", lw=0.4)
        ax.annotate(f"{v:.2f}", (v + 0.7, y), fontsize=6.6, va="center")
    ax.set_yticks(ys, [n for n, _ in LEVELS], fontsize=6.4)
    ax.set_xlim(80, 104)
    ax.set_xticks([80, 85, 90, 95, 100])
    ax.set_xlabel("Resolved citations (%)", fontsize=7.5)
    ax.set_title("(a) Citation resolvability", loc="left")
    S.finish(ax, ygrid_only=False)
    ax.grid(False)

    # ── (b1) D2 citation support (Track A only) ─────────────────────────
    ax1 = fig.add_subplot(gs[0, 1])
    d2v, d2n = [], []
    for judge in ("kimi-k2.6", "glm-5.3"):
        m = STATS["by_judge"][judge]["per_dim_means"][CFG_OF_TRACK["A"]]["D2"]
        d2v.append(0.0 if m is None else m)
        d2n.append(STATS["by_judge"][judge]["per_dim_nonnull_n"][CFG_OF_TRACK["A"]]["D2"])
    ax1.bar([0, 1], d2v, width=0.55,
            color=[S.JUDGE_COLOR["kimi-k2.6"], S.JUDGE_COLOR["glm-5.3"]],
            edgecolor="#333333", lw=0.4)
    for x, v, n in zip([0, 1], d2v, d2n):
        ax1.annotate(f"{v:.2f}\nn = {n}", (x, v + 0.05), fontsize=6.6, ha="center")
    ax1.set_xticks([0, 1], ["kimi-\nk2.6", "glm-\n5.3"], fontsize=6.6)
    ax1.set_ylim(2.0, 5.2)
    ax1.set_xlim(-0.6, 1.6)
    ax1.set_ylabel("Judge score (1–5)", fontsize=7.5)
    ax1.set_title("(b1) Citation support (D2)", loc="left")
    S.finish(ax1)

    # ── (b2) D3 answer factuality across tracks ─────────────────────────
    ax2 = fig.add_subplot(gs[0, 2])
    x = np.arange(len(TRACKS))
    for j, judge in enumerate(("kimi-k2.6", "glm-5.3")):
        means = STATS["by_judge"][judge]["per_dim_means"]
        vals = [means[CFG_OF_TRACK[t]]["D3"] for t in TRACKS]
        ax2.bar(x + (j - 0.5) * 0.38, vals, width=0.34, color=S.JUDGE_COLOR[judge],
                edgecolor="#333333", lw=0.4)
        for xi, v in zip(x + (j - 0.5) * 0.38, vals):
            ax2.annotate(f"{v:.2f}", (xi, v + 0.04), fontsize=5.8, ha="center")
    ax2.set_xticks(x, TRACKS)
    ax2.set_ylim(2.0, 5.2)
    ax2.set_title("(b2) Answer factuality (D3)", loc="left")
    S.finish(ax2)

    # ── (c) five web-input treatments (full width) ──────────────────────
    axc = fig.add_subplot(gs[1, :])
    vals = [ARMS["arms"][a]["rate_pooled"] * 100 for a in ARMS_ORDER]
    colors = [S.C["rag"], S.C["web"], S.C["web"], S.C["web"], S.C["web"]]
    for i, (v, col) in enumerate(zip(vals, colors)):
        axc.bar(i, v, width=0.52, color=col, edgecolor="#333333", lw=0.4,
                alpha=1.0 if i == 0 else 0.85)
        axc.annotate(f"{v:.1f}", (i, v + 0.6), fontsize=7.0, ha="center", weight="bold")
    for i, arm in enumerate(ARMS_ORDER):
        if arm in ARM_DELTA:
            key, _ = ARM_DELTA[arm]
            d = ARMS["pairs"][key]["diff_pp"]
            mdd = ARMS["pairs"][key]["mdd"] * 100
            axc.annotate(f"{d:+.1f} pp (MDD {mdd:.1f})", (i, v if False else vals[i] + 3.4),
                         fontsize=6.4, ha="center")
    axc.axhline(vals[0], color="#333333", lw=0.7, ls=":")
    axc.annotate("no-web baseline", (0.45, vals[0] + 0.7), fontsize=6.2, ha="left",
                 color=S.C["text2"])
    axc.set_xticks(range(5), ARM_SHORT, fontsize=6.8)
    axc.set_ylim(0, 40)
    axc.set_ylabel("Model-side hallucination rate (%)", fontsize=7.5)
    axc.set_title("(c) Five web-input treatments (48-question timeliness subset)", loc="left")
    S.finish(axc)

    # ── notes (wrapped short lines inside the exact canvas) ────────────
    notes = [
        "(a) share of in-text citation markers that resolve to a real source; denominators are stated per bar.",
        "(a) the 85.07% streaming value is diagnostic only; the frozen batch contributes 1,649 / 1,649 "
        "resolved markers, 0 outside.",
        "(b1) D2 is scored only where retrieval happens (Track A); B–E produce no citations. "
        "(b2) n = 84 per track (Track D n = 80).",
        "(b2) blue = kimi-k2.6, gold = glm-5.3; citation support ≠ resolvability ≠ factuality.",
        "(c) n = 96 judge × question verdicts per arm (48 q × 2 judges); rate = per-question mean of "
        "hallucination booleans.",
        "(c) A is the no-web baseline, not evidence for the two-section isolation; B − C' gap 5.2 pp < "
        "MDD 12.4 pp → not distinguishable.",
    ]
    for i, line in enumerate(notes):
        fig.text(0.055, 0.145 - i * 0.026, line, fontsize=6.2, color=S.C["text2"], va="top")
    return fig


if __name__ == "__main__":
    fig = main()
    paths = S.save(fig, OUT / "fig4_quality")
    ev = {
        "figure": "Fig4",
        "panels": {
            "a": {"levels": LEVELS, "source": "data_sources.CITATION_LEVELS (wxRev §3.4)"},
            "b1": {"dim": "D2 citation support", "track": "A"},
            "b2": {"dim": "D3 answer factuality", "tracks": TRACKS},
            "c": {"arms": {a: ARMS["arms"][a]["rate_pooled"] for a in ARMS_ORDER},
                  "pairs": ARMS["pairs"],
                  "denominator_note": "n=96 judge×question verdicts per arm (48 q × 2 judges)"},
        },
        "outputs": paths,
    }
    (OUT / "fig4_qa_evidence.json").write_text(json.dumps(ev, ensure_ascii=False, indent=1),
                                               encoding="utf-8")
    print("Fig4 written:", paths)

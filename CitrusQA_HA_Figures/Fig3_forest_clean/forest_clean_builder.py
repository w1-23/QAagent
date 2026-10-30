# -*- coding: utf-8 -*-
"""CLEAN single-panel forest plots for Fig.3e/f — Pi's request (2026-10-16).

Deliverable scope (exactly what was asked):
  * only the two original forest panels (d = kimi-k2.6, e = glm-5.3);
  * Times New Roman;
  * ONLY the data annotations the chart needs (y labels, x ticks, zero line,
    per-row values) — no title, no panel letter, no caption, no notes, no legend,
    so the remaining labelling can be added by hand;
  * PNG output (raster, transparent background); vector twins written as well;
  * writes into Fig3_forest_clean/ ONLY — no existing file is overwritten.

Statistics: statistics/fig3_forest_stats.json  (D3–D10 + separated Mean (D3–D10);
point estimate + 95% paired CI, zero reference line, no MDD band).
"""
import sys, json, csv
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures")
OUT = ROOT / "Fig3_forest_clean"
OUT.mkdir(exist_ok=True)
STATS = json.loads((ROOT / "statistics/fig3_forest_stats.json").read_text(encoding="utf-8"))
COLORS = {"kimi-k2.6": "#287D99", "glm-5.3": "#BE9346"}
FILES = {"kimi-k2.6": "fig3d_forest_kimi", "glm-5.3": "fig3e_forest_glm"}
DIMS = [f"D{i}" for i in range(3, 11)]
SHORT = {"D3": "Factual correctness", "D4": "Completeness", "D5": "Key-point coverage",
         "D6": "Temporal validity", "D7": "Scope honesty", "D8": "Uncertainty calibration",
         "D9": "Presentation", "D10": "Conciseness"}
W_MM, H_MM = 92.0, 62.0
INK = "#26343B"
MEAN_LABEL = "Mean (D3–D10)"

plt.rcParams.update({
    "font.family": ["Times New Roman", "Liberation Serif", "Nimbus Roman", "DejaVu Serif"],
    "font.size": 7,
    "axes.edgecolor": "#333333",
    "axes.linewidth": 0.6,
    "xtick.labelsize": 7,
    "ytick.labelsize": 7,
    "xtick.color": INK, "ytick.color": INK,
    "xtick.direction": "in", "ytick.direction": "in",
    "xtick.major.size": 2.0, "ytick.major.size": 2.0,
    "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "figure.facecolor": "white", "axes.facecolor": "white",
    "savefig.facecolor": "white", "svg.fonttype": "none", "pdf.fonttype": 42,
})


def rows_for(judge):
    blk = STATS["by_judge"][judge]
    rows = [(f"{d} {SHORT[d]}", blk["per_dimension"][d]["delta"],
             blk["per_dimension"][d]["ci95"]) for d in DIMS]
    m = blk["mean_d3_d10"]
    rows.append((MEAN_LABEL, m["delta"], m["ci95"]))
    return rows


def draw(judge, out_base: Path):
    rows = rows_for(judge)
    color = COLORS[judge]
    fig = plt.figure(figsize=(W_MM / 25.4, H_MM / 25.4))
    ax = fig.add_axes([0.42, 0.075, 0.555, 0.905])

    ys = list(np.arange(len(rows))[::-1].astype(float))
    ys[-1] -= 0.75                                    # separate the summary row
    lo_all, hi_all = [], []
    for (label, d, (lo, hi)), y in zip(rows, ys):
        ax.plot([lo, hi], [y, y], color=color, lw=1.0, solid_capstyle="butt", zorder=3)
        ax.plot([lo, lo], [y - .13, y + .13], color=color, lw=1.0, zorder=3)
        ax.plot([hi, hi], [y - .13, y + .13], color=color, lw=1.0, zorder=3)
        ax.plot([d], [y], marker="D" if label == MEAN_LABEL else "o",
                ms=4.4 if label == MEAN_LABEL else 3.9, color=color, zorder=4)
        ax.annotate(f"{d:+.3f}", (max(hi, d) + 0.03, y), fontsize=6.6, va="center",
                    ha="left", color=INK, weight="bold" if label == MEAN_LABEL else "normal")
        lo_all.append(lo); hi_all.append(hi)
    ax.axhline(ys[-1] + 0.45, color="#D4DCE0", lw=0.6, zorder=1)   # rule above the Mean row
    ax.axvline(0, color="#333333", lw=0.8, ls="--", zorder=1)      # zero reference line

    ax.set_yticks(ys)
    ax.set_yticklabels([r[0] for r in rows])
    for t, r in zip(ax.get_yticklabels(), rows):
        if r[0] == MEAN_LABEL:
            t.set_fontweight("bold")
    ax.set_ylim(-1.6, len(rows) - 0.35)
    ax.set_xlim(min(lo_all) - 0.08, max(hi_all) + 0.24)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(False)

    for ext in ("png", "svg", "pdf"):
        p = out_base.with_suffix("." + ext)
        fig.savefig(p, dpi=600 if ext == "png" else None,
                    bbox_inches="tight", pad_inches=0.02,
                    facecolor="none", edgecolor="none", transparent=True)
    plt.close(fig)
    return [str(out_base.with_suffix("." + e)) for e in ("png", "svg", "pdf")]


# ── combined twin (same panels side by side, still label-free) ─────────────
def draw_combined(out_base: Path):
    fig = plt.figure(figsize=(174.0 / 25.4, 64.0 / 25.4))
    for k, judge in enumerate(("kimi-k2.6", "glm-5.3")):
        rows = rows_for(judge)
        color = COLORS[judge]
        ax = fig.add_axes([0.20 + k * 0.42, 0.08, 0.255, 0.90])
        ys = list(np.arange(len(rows))[::-1].astype(float))
        ys[-1] -= 0.75
        lo_all, hi_all = [], []
        for (label, d, (lo, hi)), y in zip(rows, ys):
            ax.plot([lo, hi], [y, y], color=color, lw=1.0, solid_capstyle="butt", zorder=3)
            ax.plot([lo, lo], [y - .13, y + .13], color=color, lw=1.0, zorder=3)
            ax.plot([hi, hi], [y - .13, y + .13], color=color, lw=1.0, zorder=3)
            ax.plot([d], [y], marker="D" if label == MEAN_LABEL else "o",
                    ms=4.4 if label == MEAN_LABEL else 3.9, color=color, zorder=4)
            ax.annotate(f"{d:+.3f}", (max(hi, d) + 0.03, y), fontsize=6.6, va="center",
                        ha="left", color=INK, weight="bold" if label == MEAN_LABEL else "normal")
            lo_all.append(lo); hi_all.append(hi)
        ax.axhline(ys[-1] + 0.45, color="#D4DCE0", lw=0.6, zorder=1)
        ax.axvline(0, color="#333333", lw=0.8, ls="--", zorder=1)
        ax.set_yticks(ys)
        ax.set_yticklabels([r[0] for r in rows])
        for t, r in zip(ax.get_yticklabels(), rows):
            if r[0] == MEAN_LABEL:
                t.set_fontweight("bold")
        ax.set_ylim(-1.6, len(rows) - 0.35)
        ax.set_xlim(min(lo_all) - 0.08, max(hi_all) + 0.24)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.grid(False)
    for ext in ("png", "svg", "pdf"):
        fig.savefig(out_base.with_suffix("." + ext), dpi=600 if ext == "png" else None,
                    bbox_inches="tight", pad_inches=0.02,
                    facecolor="none", edgecolor="none", transparent=True)
    plt.close(fig)
    return [str(out_base.with_suffix("." + e)) for e in ("png", "svg", "pdf")]


if __name__ == "__main__":
    made = {}
    for judge, name in FILES.items():
        made[judge] = draw(judge, OUT / name)
    made["combined"] = draw_combined(OUT / "fig3de_forest_combined")

    # clean data table so the remaining labels can be added by hand
    with (OUT / "forest_clean_data.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["panel", "judge", "row", "dimension_or_summary", "delta",
                    "ci95_low", "ci95_high", "n"])
        for judge in ("kimi-k2.6", "glm-5.3"):
            blk = STATS["by_judge"][judge]
            for d in DIMS:
                e = blk["per_dimension"][d]
                w.writerow(["d" if judge == "kimi-k2.6" else "e", judge, f"{d} {SHORT[d]}",
                            d, e["delta"], e["ci95"][0], e["ci95"][1], e["n"]])
            m = blk["mean_d3_d10"]
            w.writerow(["d" if judge == "kimi-k2.6" else "e", judge, MEAN_LABEL,
                        "mean_D3_D10", m["delta"], m["ci95"][0], m["ci95"][1], m["n"]])
    for k, v in made.items():
        print(k, "→", v[0])
    print("data table →", OUT / "forest_clean_data.csv")

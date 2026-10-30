# -*- coding: utf-8 -*-
"""Fig.3e/f statistics — recompute the paired effect with the CORRECT aggregation.

PI ruling (2026-10-16):
  * Per-dimension rows: D3 … D10 only (D1/D2 are not commonly comparable between
    Track A and Track B, so they must not enter the pooled difference).
  * Summary row "Mean (D3–D10)":  Δ_i = (1/8) Σ_{d=3..10} (S_A,i,d − S_B,i,d)
    computed PER QUESTION first, then mean + 95% paired CI across questions.
  * The old figure's row was hard-coded `mean_all (paired)` = (0.6372, 0.1596) for
    glm / (0.0173, 0.1643) for kimi, using the z=1.96 coefficient. Those numbers
    reproduce the mean over ALL AVAILABLE dimensions (D1–D10), i.e. a different
    aggregation → must NOT be reused as-is; recomputed and renamed here.

Outputs statistics/fig3_forest_stats.json with, per judge:
  per-dimension deltas + 95% CI, the new Mean(D3–D10) row, and (for the record)
  the old all-dimension aggregation so the manuscript discrepancy is explicit.
"""
import json, math, statistics as st
from pathlib import Path

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")
S = ROOT / "07_交付与文档_delivery_docs/03_图件_figures/英文_figures_en/_sources"
OUT = ROOT / "CitrusQA_HA_Figures/statistics"
DIMS = [f"D{i}" for i in range(3, 11)]          # D3–D10
DIM_SHORT = {"D3": "Factual correctness", "D4": "Completeness", "D5": "Key-point coverage",
             "D6": "Temporal validity", "D7": "Scope honesty", "D8": "Uncertainty calibration",
             "D9": "Presentation", "D10": "Conciseness"}
DIM_LABEL = {d: f"{d} {DIM_SHORT[d]}" for d in DIMS}


def t_crit(df, p=0.975):
    """Two-sided 95% Student-t critical value (table for small df, Cornish-Fisher
    expansion for df >= 30) so the CI does not suffer from a coarse table."""
    T = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
         9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
         16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074,
         23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045}
    if df in T:
        return T[df]
    z = 1.959963985
    z3, z5 = z ** 3, z ** 5
    return z + (z3 + z) / (4 * df) + (5 * z5 + 16 * z3 + 3 * z) / (96 * df ** 2)


def paired(xs):
    n = len(xs)
    m = st.mean(xs)
    sd = st.stdev(xs) if n > 1 else 0.0
    tc = t_crit(n - 1)
    hw = tc * sd / math.sqrt(n) if n > 1 else 0.0
    return {"n": n, "delta": round(m, 4), "sd": round(sd, 4), "t_crit": round(tc, 5),
            "ci95": [round(m - hw, 4), round(m + hw, 4)], "half_width": round(hw, 4)}


def load(judge):
    return [json.loads(l) for l in (S / f"judge_scores_{judge}.jsonl").open(encoding="utf-8")]


out = {"aggregation": {
    "per_dimension_rows": "D3–D10 (paired, same questions)",
    "summary_row": "Mean (D3–D10): per-question Δ_i = mean over d=3..10 of (S_A - S_B), then paired mean + 95% CI",
    "excluded": "D1/D2 (not commonly comparable between Track A and Track B)",
    "mdd_band": "not drawn (PI ruling); manuscript MDD definitions live in Methods/caption",
}, "by_judge": {}}

for judge in ("kimi-k2.6", "glm-5.3"):
    rows = load(judge)
    A = {r["id"]: r for r in rows if r["config"] == "citrusqa"}
    B = {r["id"]: r for r in rows if r["config"] == "same_base_trackA"}
    ids = sorted(set(A) & set(B))

    per_dim = {}
    for d in DIMS:
        xs = [A[i][d] - B[i][d] for i in ids
              if isinstance(A[i].get(d), (int, float)) and isinstance(B[i].get(d), (int, float))]
        per_dim[d] = paired(xs)

    # NEW summary row: per-question mean over D3–D10, then paired stats
    d3_10 = []
    for i in ids:
        vals = [A[i][d] - B[i][d] for d in DIMS
                if isinstance(A[i].get(d), (int, float)) and isinstance(B[i].get(d), (int, float))]
        if len(vals) == len(DIMS):
            d3_10.append(st.mean(vals))
    mean_row = paired(d3_10)

    # OLD aggregation (all available dims, i.e. D1–D10 where defined) — record only
    allv = []
    for i in ids:
        vals = [A[i][d] - B[i][d] for d in [f"D{k}" for k in range(1, 11)]
                if isinstance(A[i].get(d), (int, float)) and isinstance(B[i].get(d), (int, float))]
        if vals:
            allv.append(st.mean(vals))
    old_all_dims = paired(allv)
    # old figure's hard-coded values, z=1.96 half-width
    z_hw = 1.959963985 * old_all_dims["sd"] / math.sqrt(old_all_dims["n"])
    old_figure = {"delta": old_all_dims["delta"], "z_half_width": round(z_hw, 4)}

    out["by_judge"][judge] = {
        "per_dimension": per_dim,
        "mean_d3_d10": mean_row,
        "record_old_all_dimensions_aggregation": old_all_dims,
        "record_old_figure_row": old_figure,
        "delta_definition_shift": round(mean_row["delta"] - old_all_dims["delta"], 4),
    }

(OUT / "fig3_forest_stats.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                           encoding="utf-8")

for judge, v in out["by_judge"].items():
    print(f"== {judge}")
    for d in DIMS:
        s = v["per_dimension"][d]
        print(f"   {d:>4} {DIM_SHORT[d]:<24} delta={s['delta']:+.4f} ci=[{s['ci95'][0]:+.4f},{s['ci95'][1]:+.4f}] n={s['n']}")
    m = v["mean_d3_d10"]
    print(f"   NEW Mean (D3–D10)          delta={m['delta']:+.4f} ci=[{m['ci95'][0]:+.4f},{m['ci95'][1]:+.4f}] n={m['n']}")
    o = v["record_old_all_dimensions_aggregation"]
    print(f"   OLD all-dims (record)      delta={o['delta']:+.4f} (paper row {v['record_old_figure_row']})")
print("\nwritten", OUT / "fig3_forest_stats.json")

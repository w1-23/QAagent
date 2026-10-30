# -*- coding: utf-8 -*-
"""(b) Recompute Fig.3 statistics from the per-question judge scores.

Outputs (JSON, machine-checkable):
  * per-dimension means per config per judge (cross-check vs per_dimension_means.csv)
  * Track A−B paired differences per dimension: delta, sd, n, t_crit, CI95, MDD, p (t)
  * ten-dimension combined score per question (mean of the D1..D10 that exist),
    and the A−B paired difference of that combined score (PI definition:
    "把10个维度的分数加一起平均"; end-to-end batches have no nulls)
"""
import json, math, statistics as st
from pathlib import Path

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")
S = ROOT / "07_交付与文档_delivery_docs/03_图件_figures/英文_figures_en/_sources"
OUT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\statistics")
OUT.mkdir(exist_ok=True)

DIMS = [f"D{i}" for i in range(1, 11)]
CFG = ["citrusqa", "same_base_trackA", "same_base_trackB", "mainstream_trackA", "mainstream_trackB"]
TRACK = {"citrusqa": "A", "same_base_trackA": "B", "same_base_trackB": "C",
         "mainstream_trackA": "D", "mainstream_trackB": "E"}


def load(judge):
    p = S / f"judge_scores_{judge}.jsonl"
    rows = []
    for line in p.open(encoding="utf-8"):
        d = json.loads(line)
        rows.append(d)
    return rows


def t_crit_975(df):
    # two-sided 95% t quantile table (fallback to 1.96 for large df)
    T = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
         9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131,
         16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074,
         23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045,
         30: 2.042, 40: 2.021, 50: 2.009, 60: 2.000, 80: 1.990, 100: 1.984, 120: 1.980}
    if df in T:
        return T[df]
    ks = [k for k in T if k <= df]
    return T[max(ks)] if ks else 1.96


out = {"source": "judge_scores_{kimi-k2.6,glm-5.3}.jsonl (per-question, blind)",
       "by_judge": {}}

for judge in ("kimi-k2.6", "glm-5.3"):
    rows = load(judge)
    # per-dim means per config
    means, ns = {}, {}
    for cfg in CFG:
        sub = [r for r in rows if r["config"] == cfg]
        ns[cfg] = len(sub)
        means[cfg] = {dim: (round(st.mean([r[dim] for r in sub if isinstance(r.get(dim), (int, float))]), 4)
                            if any(isinstance(r.get(dim), (int, float)) for r in sub) else None)
                      for dim in DIMS}
    nonnull = {cfg: {dim: sum(1 for r in rows if r["config"] == cfg and isinstance(r.get(dim), (int, float)))
                     for dim in DIMS} for cfg in CFG}
    # per-question ten-dim combined (mean of available dims; end-to-end has no nulls)
    comb = {}
    for r in rows:
        vals = [r[dim] for dim in DIMS if isinstance(r.get(dim), (int, float))]
        comb[(r["config"], r["id"])] = (st.mean(vals) if vals else None, len(vals))
    # A−B paired per dimension + combined
    A = {r["id"]: r for r in rows if r["config"] == "citrusqa"}
    B = {r["id"]: r for r in rows if r["config"] == "same_base_trackA"}
    ids = sorted(set(A) & set(B))
    per_dim, paired_out = {}, {}
    for dim in DIMS:
        xs = [(A[i][dim], B[i][dim]) for i in ids
              if isinstance(A[i].get(dim), (int, float)) and isinstance(B[i].get(dim), (int, float))]
        if not xs:
            per_dim[dim] = None
            continue
        d = [a - b for a, b in xs]
        n = len(d)
        delta = st.mean(d)
        sd = st.stdev(d) if n > 1 else 0.0
        tc = t_crit_975(n - 1)
        hw = tc * sd / math.sqrt(n) if n > 1 else 0.0
        t = delta / (sd / math.sqrt(n)) if sd > 0 else 0.0
        per_dim[dim] = {"n": n, "delta": round(delta, 4), "sd": round(sd, 4),
                        "ci95": [round(delta - hw, 4), round(delta + hw, 4)],
                        "mdd": round(hw, 4), "t": round(t, 3),
                        "exceeds_mdd": bool(abs(delta) > hw), "sd_zero": sd == 0.0}
    # combined over all 10 dims per question
    dc = []
    for i in ids:
        a, _na = comb[("citrusqa", i)]
        b, _nb = comb[("same_base_trackA", i)]
        if a is not None and b is not None:
            dc.append(a - b)
    n = len(dc)
    if n > 0:
        delta = st.mean(dc)
        sd = st.stdev(dc) if n > 1 else 0.0
        tc = t_crit_975(n - 1)
        hw = tc * sd / math.sqrt(n) if n > 1 else 0.0
        paired_out = {"n": n, "delta": round(delta, 4), "sd": round(sd, 4),
                      "ci95": [round(delta - hw, 4), round(delta + hw, 4)],
                      "mdd": round(hw, 4),
                      "t": round(delta / (sd / math.sqrt(n)), 3) if sd else 0.0,
                      "exceeds_mdd": bool(abs(delta) > hw)}
    out["by_judge"][judge] = {"n_scored": ns, "per_dim_means": means,
                              "per_dim_nonnull_n": nonnull,
                              "A_minus_B_per_dim": per_dim,
                              "A_minus_B_combined10": paired_out}

# cross-check against the shipped aggregates
sm = {}
import csv
with (S / "per_dimension_means.csv").open(encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        sm.setdefault(row.get("config") or row.get("track"), {})[row.get("dim") or row.get("dimension")] = \
            float(row.get("mean") or row.get("value") or 0)
out["shipped_per_dimension_means_head"] = list(sm.items())[:2]

(OUT / "fig3_recomputed_stats.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                                encoding="utf-8")
print("written", OUT / "fig3_recomputed_stats.json")

# console summary (ASCII-safe numbers only)
for j, v in out["by_judge"].items():
    c = v["A_minus_B_combined10"]
    print(f"[{j}] n_scored={v['n_scored']}")
    print(f"   A-B combined10: n={c.get('n')} delta={c.get('delta')} mdd={c.get('mdd')} "
          f"ci={c.get('ci95')} exceeds={c.get('exceeds_mdd')}")
    for dim, s in v["A_minus_B_per_dim"].items():
        if s:
            print(f"   {dim}: delta={s['delta']:+.4f} mdd={s['mdd']:.4f} n={s['n']} "
                  f"exceeds={s['exceeds_mdd']}")

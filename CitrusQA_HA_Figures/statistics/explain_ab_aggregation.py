# -*- coding: utf-8 -*-
"""Explain, from the per-question scores, why the pooled A−B difference changed.

OLD (manuscript, hard-coded in fig27/28): for each question, average **each side
over that side's own available dimensions**, then subtract:
      Δ_i^old = mean_{d ∈ dims(A,i)} S_A,i,d  −  mean_{d ∈ dims(B,i)} S_B,i,d
   Track A has D1/D2 for 68 (kimi) / 78 (glm) of the 84 questions, Track B never
   has them → the two sides are averaged over DIFFERENT dimension sets.

NEW (PI ruling): restrict to the commonly comparable dimensions, per question:
      Δ_i^new = (1/8) Σ_{d=3..10} ( S_A,i,d − S_B,i,d )
"""
import json, sys, statistics as st
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from pathlib import Path

S = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\07_交付与文档_delivery_docs"
         r"\03_图件_figures\英文_figures_en\_sources")
ALL = [f"D{k}" for k in range(1, 11)]
D3_10 = [f"D{k}" for k in range(3, 11)]

for judge in ("kimi-k2.6", "glm-5.3"):
    rows = [json.loads(l) for l in (S / f"judge_scores_{judge}.jsonl").open(encoding="utf-8")]
    A = {r["id"]: r for r in rows if r["config"] == "citrusqa"}
    B = {r["id"]: r for r in rows if r["config"] == "same_base_trackA"}
    ids = sorted(set(A) & set(B))

    def num(r, d):
        v = r.get(d)
        return v if isinstance(v, (int, float)) else None

    with12 = [i for i in ids if num(A[i], "D1") is not None or num(A[i], "D2") is not None]
    a1 = [num(A[i], "D1") for i in ids if num(A[i], "D1") is not None]
    a2 = [num(A[i], "D2") for i in ids if num(A[i], "D2") is not None]
    b1 = [num(B[i], "D1") for i in ids if num(B[i], "D1") is not None]
    b2 = [num(B[i], "D2") for i in ids if num(B[i], "D2") is not None]

    old, new, a_avail, b_avail, a_common = [], [], [], [], []
    for i in ids:
        da = [num(A[i], d) for d in ALL if num(A[i], d) is not None]
        db = [num(B[i], d) for d in ALL if num(B[i], d) is not None]
        old.append(st.mean(da) - st.mean(db))
        a_avail.append(st.mean(da))
        b_avail.append(st.mean(db))
        diffs = [num(A[i], d) - num(B[i], d) for d in D3_10]
        new.append(st.mean(diffs))
        a_common.append(st.mean([num(A[i], d) for d in D3_10]))
    print(f"\n===== {judge} (n = {len(ids)} paired questions) =====")
    print(f"  questions whose Track-A record carries D1/D2 : {len(with12)} / {len(ids)}")
    print(f"  Track A D1 mean = {st.mean(a1):.4f} (n={len(a1)}), D2 mean = {st.mean(a2):.4f} (n={len(a2)})")
    print(f"  Track B D1/D2            : {'present' if b1 or b2 else 'ABSENT for every question (no retrieval)'}")
    print(f"  Track A mean over D3–D10 = {st.mean(a_common):.4f}")
    print(f"  Track A mean over its own available dims = {st.mean(a_avail):.4f} "
          f"(inflated by D1/D2 on {len(with12)} questions)")
    print(f"  Track B mean over its own available dims = {st.mean(b_avail):.4f}")
    print(f"  OLD Δ (per-side available dims) = {st.mean(old):+.4f}")
    print(f"  NEW Δ (per-question, D3–D10)    = {st.mean(new):+.4f}")
    print(f"  difference (new − old)          = {st.mean(new) - st.mean(old):+.4f}")
    # two worked questions, one with D1/D2 and one without
    ex12 = next((i for i in ids if num(A[i], "D1") is not None), None)
    exno = next((i for i in ids if num(A[i], "D1") is None), None)
    for tag, i in (("D1/D2 present", ex12), ("D1/D2 missing", exno)):
        da = [num(A[i], d) for d in ALL if num(A[i], d) is not None]
        old_i = st.mean(da) - st.mean([num(B[i], d) for d in ALL if num(B[i], d) is not None])
        new_i = st.mean([num(A[i], d) - num(B[i], d) for d in D3_10])
        print(f"  e.g. {i} ({tag}): A available dims={len(da)} → Δ_old={old_i:+.3f}, "
              f"Δ_new(D3–D10)={new_i:+.3f}")

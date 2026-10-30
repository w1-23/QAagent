# -*- coding: utf-8 -*-
"""Update Fig.3 caption / manifest / qa-evidence for the new forest definition
(8 dimension rows D3–D10 + separated 'Mean (D3–D10)', 95% paired CI, no MDD band)."""
import json
from pathlib import Path

F = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures")
F3 = F / "Fig3"

# ---- caption ----
cap = (F3 / "caption_draft_fig3.md").read_text(encoding="utf-8")
old_start = cap.index("**(d)(e)**")
old_end = cap.index("\n", cap.index("never averaged", old_start)) + 1 \
    if "never averaged" in cap[old_start:] else len(cap)
new_de = """**(d)(e)** Paired differences Track A − Track B per dimension on the common evaluation
dimensions **D3–D10**, computed per question first (positive = CitrusQA better), with the zero
reference line and 95% paired confidence intervals (Student t, df = 83). Citation dimensions D1/D2
are excluded because they are not commonly comparable between the two configurations (Track B does
not retrieve). The bottom row, **Mean (D3–D10)**, summarises the pooled effect:
Δᵢ = (1/8) Σ_{d=3..10} (S_A,i,d − S_B,i,d) per question, then the paired mean and its 95% CI —
+0.580 [+0.418, +0.743] for glm-5.3 and +0.079 [−0.070, +0.228] for kimi-k2.6. No 80 % power band
and no MDD verdict are drawn in the figure; MDD definitions remain in Methods. The two judges are
reported separately and never averaged.

> Aggregation note (2026-10-16): the previously reported ten-dimension summary (+0.6372 glm /
> +0.0173 kimi) was produced by averaging **each configuration over its own available dimensions**
> (Track A included D1/D2, Track B did not) — an inconsistent aggregation. The corrected
> paired-common-dimension value is +0.5804 / +0.0789; direction and conclusions are unchanged.
"""
cap = cap[:old_start] + new_de
(F3 / "caption_draft_fig3.md").write_text(cap, encoding="utf-8")
print("caption updated")

# ---- manifest ----
mp = F3 / "figure_manifest_fig3.json"
m = json.loads(mp.read_text(encoding="utf-8"))
m["statistical_methods"] = ["Wilson 95% CI", "exact McNemar",
                            "paired t 95% CI (Student t, df = 83)",
                            "per-question Δ over D3–D10 then paired mean (Mean (D3–D10) row)"]
m["figure_dims"] = "D3–D10 + Mean (D3–D10); D1/D2 excluded (not commonly comparable)"
m["output_dimensions"] = "174.0 × 142.0 mm"
m["verification_status"] = (
    "Round-4 (2026-10-16): forest panels rebuilt on the PI ruling — 8 dimension rows D3–D10 plus a "
    "separated 'Mean (D3–D10)' row (per-question mean over D3–D10, then paired mean + 95% t CI); "
    "MDD band and MDD verdict removed. The manuscript's old ten-dimension summary (+0.6372 glm / "
    "+0.0173 kimi) reproduces a per-side available-dimension average and therefore differs: "
    "corrected values are +0.5804 [+0.4176, +0.7432] and +0.0789 [−0.0702, +0.2280].")
mp.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
print("manifest updated")

# ---- qa evidence ----
ep = F3 / "fig3_qa_evidence.json"
ev = json.loads(ep.read_text(encoding="utf-8"))
ev["panels"]["de"] = {
    "data": "statistics/fig3_forest_stats.json (recomputed from judge_scores_*.jsonl)",
    "rows": "D3–D10 + separated Mean (D3–D10)",
    "mean_d3_d10": {j: json.loads((F / 'statistics/fig3_forest_stats.json').read_text(encoding='utf-8'))
                    ["by_judge"][j]["mean_d3_d10"] for j in ("kimi-k2.6", "glm-5.3")},
    "no_mdd_band": True,
}
ep.write_text(json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
print("qa evidence updated")

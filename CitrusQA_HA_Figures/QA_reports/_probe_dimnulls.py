# -*- coding: utf-8 -*-
"""Which configs carry D1/D2 (citation dimensions) vs D3+ (answer dimensions)?"""
import json
from pathlib import Path

S = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\07_交付与文档_delivery_docs"
         r"\03_图件_figures\英文_figures_en\_sources")
CFG = ["citrusqa", "same_base_trackA", "same_base_trackB", "mainstream_trackA", "mainstream_trackB"]
DIMS = [f"D{i}" for i in range(1, 11)]

out = []
for j in ("kimi-k2.6", "glm-5.3"):
    rows = [json.loads(l) for l in (S / f"judge_scores_{j}.jsonl").open(encoding="utf-8")]
    out.append(f"== {j}")
    for cfg in CFG:
        sub = [r for r in rows if r["config"] == cfg]
        cnt = {d: sum(1 for r in sub if isinstance(r.get(d), (int, float))) for d in DIMS}
        out.append(f"   {cfg:20s} n={len(sub):3d} " +
                   " ".join(f"{d}={cnt[d]:3d}" for d in DIMS))
Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\QA_reports\_probe_dimnulls.txt"
     ).write_text("\n".join(out), encoding="utf-8")
print("\n".join(out))

# -*- coding: utf-8 -*-
"""(a) Probe exp1 (cross-lingual) structure: denominators, cells, anchors."""
import io, json
from pathlib import Path

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")
S = ROOT / "07_交付与文档_delivery_docs/03_图件_figures/英文_figures_en/_sources"
OUT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\QA_reports\_probe_exp1.txt")

L = []
def p(*a):
    L.append(" ".join(str(x) for x in a))

d = json.loads((S / "exp1_summary.json").read_text(encoding="utf-8-sig"))
p("TOP KEYS:", list(d.keys()))
p("stage:", d.get("stage"), "| n_questions:", d.get("n_questions"), "| n_gold:", d.get("n_gold"))
p("cells:", json.dumps(d.get("cells"), ensure_ascii=False))
p("\n-- overall --")
ov = d.get("overall", {})
p("name:", ov.get("name"), "n:", ov.get("n"))
for cell, v in (ov.get("cells") or {}).items():
    p(f"  {cell}: n={v.get('n')} passed={v.get('passed_gold_rate')} ci95={v.get('passed_gold_ci95')} "
      f"R@5={v.get('R@5')} R@10={v.get('R@10')} MRR@10={v.get('MRR@10')}")

for k in ("by_anchor", "anchors", "effects", "anchor_paired_effect_within_cell"):
    if k in d:
        p(f"\n-- {k} --")
        s = json.dumps(d[k], ensure_ascii=False, indent=1)
        p(s[:2600])

# per-question file
pq = ROOT / "experiment/13_评测管道_pipeline/results/retrieval/crosslingual_20260918/per_question.jsonl"
rows = [json.loads(l) for l in pq.open(encoding="utf-8")]
p(f"\n-- per_question.jsonl rows={len(rows)} keys={list(rows[0].keys())}")
from collections import Counter
p("cells:", Counter(r["cell"] for r in rows))
p("retrieval_lang:", Counter((r["cell"][0], r["retrieval_lang"]) for r in rows))
p("pool_gold true/false by cell:", Counter((r["cell"], bool(r.get("pool_gold"))) for r in rows))
p("gold_fused_rank non-null by cell:", Counter((r["cell"], r.get("gold_fused_rank") is not None) for r in rows))

# other crosslingual files
for sub in ("paradigm_20260918",):
    dd = ROOT / "experiment/13_评测管道_pipeline/results/retrieval" / sub
    if dd.exists():
        p(f"\n-- {sub} --")
        for f in sorted(dd.glob("*")):
            p("   ", f.name, f.stat().st_size)

OUT.write_text("\n".join(L), encoding="utf-8")
print("written", OUT, OUT.stat().st_size, "bytes")

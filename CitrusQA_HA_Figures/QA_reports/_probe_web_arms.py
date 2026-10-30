# -*- coding: utf-8 -*-
"""(c) Read the web-arms experiment source to settle the five treatments and the
n=96 vs n=48 aggregation semantics."""
import io, re, json
from pathlib import Path

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")
OUT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\QA_reports\_probe_web_arms.txt")
L = []


def p(*a):
    L.append(" ".join(str(x) for x in a))


cands = []
for base in (ROOT / "experiment/09_联网五组对照_web_arms",
             ROOT / "data_archive/04_web_experiments",
             ROOT / "experiment/13_评测管道_pipeline"):
    if base.exists():
        cands += [f for f in base.rglob("*") if f.is_file() and f.suffix in (".py", ".md", ".json")
                  and "__pycache__" not in str(f)]
p("candidate files:", len(cands))
for f in sorted(cands)[:40]:
    p("  ", f.relative_to(ROOT), f.stat().st_size)

KEY = re.compile(r"(no[_ ]?web|unfiltered|receipt|model[_ ]?visible|summar|excerpt|arm|hallucin|n\s*=|n_questions|48|96)",
                 re.I)
p("\n\n===== keyword contexts in scripts =====")
shown = 0
for f in sorted(cands):
    if f.suffix != ".py":
        continue
    t = f.read_text(encoding="utf-8", errors="ignore")
    p(f"\n--- {f.relative_to(ROOT)} ({len(t.splitlines())} lines) ---")
    for m in list(KEY.finditer(t))[:14]:
        s = max(0, m.start() - 150)
        p("   …" + t[s:m.end() + 190].replace("\n", " ")[:340])
        shown += 1
    if shown > 120:
        break

p("\n\n===== web_arms_summary.json (full) =====")
w = ROOT / "07_交付与文档_delivery_docs/03_图件_figures/英文_figures_en/_sources/web_arms_summary.json"
p(json.dumps(json.loads(w.read_text(encoding="utf-8-sig")), ensure_ascii=False, indent=1))

p("\n\n===== support_offline_arms.json (full) =====")
s2 = ROOT / "data_archive/04_web_experiments/threshold/support_offline_arms.json"
if s2.exists():
    p(json.dumps(json.loads(s2.read_text(encoding="utf-8-sig")), ensure_ascii=False, indent=1)[:2500])

p("\n\n===== raw run dirs (answers.jsonl) inventory =====")
for d in (ROOT / "07_交付与文档_delivery_docs/04_数据_data/原始_raw/runs").glob("*"):
    if d.is_dir():
        n = len(list(d.glob("*.jsonl")))
        p("  ", d.name, "files:", n, [x.name for x in d.glob('*.jsonl')][:4])

OUT.write_text("\n".join(L), encoding="utf-8")
print("written", OUT, OUT.stat().st_size)

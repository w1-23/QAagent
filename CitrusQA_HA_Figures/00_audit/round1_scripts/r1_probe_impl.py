# -*- coding: utf-8 -*-
"""Round-1 final probes: implementation code paths + freeze counts + missing
judge files for pools/real batches. READ-ONLY."""
import sys, io, json, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")
SRC = ROOT / "01_源码_src"


def sep(t): print("\n" + "=" * 100 + "\n" + t)

def grep_in(dirroot, pattern, exts=(".py", ".json", ".yaml", ".yml", ".md"), max_hits=14, exclude=("__pycache__",)):
    rx = re.compile(pattern, re.I | re.S)
    hits = []
    for p in dirroot.rglob("*"):
        if p.suffix not in exts or any(x in str(p) for x in exclude):
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in rx.finditer(t):
            s = t[max(0, m.start() - 60):m.end() + 80].replace("\n", " ")
            hits.append((str(p.relative_to(ROOT)), s.strip()[:150]))
            if len(hits) >= max_hits:
                return hits
    return hits

# 1) concurrency: asyncio.gather usage in src
sep("1) asyncio.gather in 01_源码_src")
for rel, snip in grep_in(SRC, r"asyncio\.gather")[:6]:
    print(f"   {rel}: {snip}")

# 2) MinerU anywhere in code/config (parse backend)
sep("2) MinerU in src/config/docs")
for base in (SRC / "src", ROOT / "02_配置_config"):
    for rel, snip in grep_in(base, r"mineru|magic-pdf|pymupdf|pdfminer|pdfplumber", max_hits=8)[:8]:
        print(f"   {rel}: {snip}")

# 3) intent candidates (1-3?) threshold code
sep("3) intent / ambiguity candidate count in src")
for rel, snip in grep_in(SRC / "src", r"候选理解|candidate.{0,12}intent|intents?\b.{0,10}(1|2|3)|歧义", max_hits=10)[:10]:
    print(f"   {rel}: {snip}")

# 4) citation audit stage: is it separate or inside code gate?
sep("4) citation audit / code gate structure in src")
for rel, snip in grep_in(SRC / "src", r"(citation.{0,6}audit|引用.{0,6}(审计|校验)|out[-_ ]?of[-_ ]?pool|越界引用|renumber)", max_hits=12)[:12]:
    print(f"   {rel}: {snip}")

# 5) re-validate loop / minimal repair trigger
sep("5) minimal repair / revalidate in src")
for rel, snip in grep_in(SRC / "src", r"(最小返修|minimal.{0,8}repair|revali|回检|repair.{0,10}(once|cap)|critical.{0,20}(repair|fix))", max_hits=10)[:10]:
    print(f"   {rel}: {snip}")

# 6) two-section output & [Wn] namespace
sep("6) two-section output markers")
for rel, snip in grep_in(SRC / "src", r"(===== 联网小节|两段|Section.{0,3}2|# 联网获取|Wn)", max_hits=8)[:8]:
    print(f"   {rel}: {snip}")

# 7) freeze_manifest counts content
sep("7) freeze_manifest.json counts")
fm = ROOT / "07_交付与文档_delivery_docs" / "04_数据_data" / "冻结_freezed" / "freeze_manifest.json"
d = json.loads(fm.read_text(encoding="utf-8-sig"))
print(json.dumps({k: d[k] for k in d if k in ("freeze_label", "frozen_at", "corpus_fingerprint", "judge", "counts", "mutated_paths", "mutated_doc_paths", "mutated_data_paths", "appended_paths")}, ensure_ascii=False, indent=1)[:1800])

# 8) pools/real judge evaluation files hunt
sep("8) pools/real judge score file hunt")
rx = re.compile(r"(pools\d*|real\d+|e2e|pool[ABC]).{0,30}(judge|score|D1|rubric|ten\W*dim)|judge.{0,20}(pools|real|e2e)|G\d+_?(score|judge)", re.I)
seen = 0
for p in ROOT.rglob("*"):
    if "__pycache__" in str(p) or not p.is_file():
        continue
    if rx.search(p.name) and p.suffix in (".json", ".jsonl", ".csv"):
        print(f"   {p.stat().st_size:>10,}  {p.relative_to(ROOT)}")
        seen += 1
        if seen > 50:
            break

# 9) e2e jar / audit gate per-critical rows (code gate counts per batch)
sep("9) audit-gate per-batch rows hunt")
for rel_hit in grep_in(ROOT / "07_交付与文档_delivery_docs", r"代码门.{0,24}(0|1|70|118)|norm.{0,10}critical|audit_gate", max_hits=8):
    print(f"   {rel_hit[0]}: {rel_hit[1]}")

# 10) check for pools judge evals inside 13_评测管道_pipeline
sep("10) 13_评测管道_pipeline results tree (depth2)")
base = ROOT / "experiment" / "13_评测管道_pipeline"
if base.exists():
    n = 0
    for p in sorted(base.rglob("*")):
        if p.is_file() and "__pycache__" not in str(p) and n < 40:
            print(f"   {p.stat().st_size:>10,}  {p.relative_to(ROOT)}")
            n += 1

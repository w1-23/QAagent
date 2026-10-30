# -*- coding: utf-8 -*-
"""Round-1 tail probes: (a) real PDF->Markdown tool used by the corpus pipeline;
(b) local scientific-figure skills / MCP-like assets; (c) paper-freeze snapshot."""
import sys, io, re, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")

def sep(t): print("\n" + "=" * 96 + "\n" + t)

# (a) pdf -> md toolchain in ingest/parse code
sep("a) PDF->Markdown toolchain in corpus/ingest code")
pats = re.compile(r"(pymupdf4llm|docling|marker[-_ ]?pdf|unstructured|pdf2md|fitz|pdfplumber|import markdownify|MinerU|magic-pdf)", re.I)
seen = 0
for base in (ROOT / "04_脚本_scripts", ROOT / "data_archive" / "06_scripts", ROOT / "_scratch"):
    if not base.exists():
        continue
    for p in base.rglob("*"):
        if p.suffix not in (".py", ".md", ".txt") or "__pycache__" in str(p) or p.stat().st_size > 2_000_000:
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in pats.finditer(t):
            s = t[max(0, m.start() - 70):m.end() + 70].replace("\n", " ")
            print(f"   {p.relative_to(ROOT)}: {s[:160]}")
            seen += 1
            break
        if seen > 14:
            break
print("   hits:", seen)

# (b) local skills / figure skills
sep("b) local skill dirs (SKILL.md / scifig / figure skill)")
for p in ROOT.rglob("SKILL.md"):
    print("   SKILL.md:", p.relative_to(ROOT))
for kw in ("scifig", "SciFig", "scientific-figure", "matplotlib-skill"):
    for p in ROOT.rglob(f"*{kw}*"):
        print(f"   [{kw}] {p.relative_to(ROOT)}")

# (c) paper freeze snapshot: any artifact with 4,906 or 126,494 as data (not paper text)
sep("c) 4,906 / 126,494 in data artifacts (excl. paper/*.docx & my READMEs)")
pat2 = re.compile(r"126[,]?494|4[,]?906")
n = 0
for p in ROOT.rglob("*"):
    if p.suffix.lower() not in (".json", ".jsonl", ".csv", ".txt", ".md", ".yaml"):
        continue
    s = str(p)
    if "__pycache__" in s or "figures_main" in s or s.endswith(".pyc"):
        continue
    if p.stat().st_size > 5_000_000:
        continue
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    if pat2.search(t):
        print(f"   {p.relative_to(ROOT)}")
        n += 1
        if n > 15:
            break
print("   hits:", n)

# (d) L1 topic distribution under the frozen (deduped) corpus?
sep("d) topic/category counts for the frozen corpus (paper 103,089 / 10,018 / 13,137 / 250)")
pat3 = re.compile(r"103[,]?089|10[,]?018|13[,]?137|112[,]?890")
n = 0
for p in ROOT.rglob("*"):
    if p.suffix.lower() not in (".json", ".jsonl", ".csv", ".txt", ".md"):
        continue
    s = str(p)
    if "__pycache__" in s or p.stat().st_size > 5_000_000:
        continue
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    if pat3.search(t):
        print(f"   {p.relative_to(ROOT)}")
        n += 1
        if n > 15:
            break
print("   hits:", n)

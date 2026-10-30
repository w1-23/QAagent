# -*- coding: utf-8 -*-
"""Collect hard facts for the session-migration handover: every asset, data file,
script and runtime path, with existence checks."""
import sys, os, json, hashlib
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from pathlib import Path

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")
FIG = ROOT / "CitrusQA_HA_Figures"
OUT = FIG / "QA_reports" / "_migration_inventory.txt"
L = []


def p(*a):
    L.append(" ".join(str(x) for x in a))


def exists(pth):
    return "OK " if Path(pth).exists() else "MISSING"


p("=" * 100)
p("A. FIGURE ASSETS (CitrusQA_HA_Figures)")
p("=" * 100)
for d in sorted(FIG.iterdir()):
    if d.is_dir():
        files = [f for f in sorted(d.rglob("*")) if f.is_file()]
        p(f"\n[{d.name}]  {len(files)} files")
        for f in files:
            rel = f.relative_to(FIG)
            p(f"   {f.stat().st_size:>10,} B   {rel}")

p("\n" + "=" * 100)
p("B. DATA SOURCES ACTUALLY USED (existence + size)")
p("=" * 100)
DATA = [
    r"07_交付与文档_delivery_docs\03_图件_figures\英文_figures_en\_sources\judge_scores_kimi-k2.6.jsonl",
    r"07_交付与文档_delivery_docs\03_图件_figures\英文_figures_en\_sources\judge_scores_glm-5.3.jsonl",
    r"07_交付与文档_delivery_docs\03_图件_figures\英文_figures_en\_sources\exp1_summary.json",
    r"07_交付与文档_delivery_docs\03_图件_figures\英文_figures_en\_sources\web_arms_summary.json",
    r"07_交付与文档_delivery_docs\03_图件_figures\英文_figures_en\_sources\per_dimension_means.csv",
    r"07_交付与文档_delivery_docs\03_图件_figures\英文_figures_en\_sources\mdd.json",
    r"07_交付与文档_delivery_docs\03_图件_figures\英文_figures_en\_sources\agreement.json",
    r"07_交付与文档_delivery_docs\03_图件_figures\英文_figures_en\_sources\five_metrics.csv",
    r"experiment\13_评测管道_pipeline\results\retrieval\crosslingual_20260918\per_question.jsonl",
    r"experiment\2026927-python绘图latest\00_shared\data_sources.py",
    r"experiment\09_联网五组对照_web_arms\01_实验脚本_scripts\analyze_web_arms.py",
    r"data_archive\04_web_experiments\threshold\support_offline_arms.json",
    r"01_源码_src\src\core\audit.py",
    r"01_源码_src\src\graph\expert_graph.py",
]
for rel in DATA:
    f = ROOT / rel
    if f.exists():
        p(f"   OK      {f.stat().st_size:>10,} B   {rel}")
    else:
        p(f"   MISSING                    {rel}")

p("\n  -- broader search for the figure _sources dir and other candidate inputs --")
for pat in ("_sources", "webpipe_thresholds.json", "webpipe_falsekill.json", "five_arms",
            "tables_webarms.py", "exp1_summary.json"):
    hits = [h for h in ROOT.rglob(pat) if ".git" not in str(h)][:8]
    p(f"   [{pat}] {len(hits)} hit(s)")
    for h in hits:
        p(f"        {h.relative_to(ROOT)}")

p("\n" + "=" * 100)
p("C. RUNTIMES / TOOLS")
p("=" * 100)
TOOLS = [
    r"C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe",
    r"E:\anaconda\envs\rag-agent\python.exe",
    r"E:\下载\新建文件夹\resources\runtime\primary-runtime\dependencies\node\bin\node.exe",
    r"E:\下载\新建文件夹\resources\app.asar.unpacked\dsh\node_modules\@deepseek-ai\libreoffice-kit\lib\cli.js",
]
for t in TOOLS:
    p(f"   {exists(t)}   {t}")
p("\n   fonts (matplotlib will warn if Arial/Helvetica missing):")
for f in (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\Arial.ttf",
          r"C:\Windows\Fonts\Helvetica.ttf"):
    p(f"   {exists(f)}   {f}")

p("\n" + "=" * 100)
p("D. SOURCE-HASH BASELINE (Round 1) — verify nothing changed")
p("=" * 100)
hb = FIG / "00_audit" / "source_hashes_baseline.csv"
p(f"   baseline file: {exists(hb)}   {hb}")
if hb.exists():
    lines = hb.read_text(encoding="utf-8-sig").strip().splitlines()
    p(f"   rows: {len(lines) - 1}")

OUT.write_text("\n".join(L), encoding="utf-8")
print("\n".join(L))
print("\nwritten:", OUT)

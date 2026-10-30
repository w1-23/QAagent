# -*- coding: utf-8 -*-
"""Round-1 data hunt: verify data sources' actual availability and schemas.
READ-ONLY."""
import sys, io, os, json, csv, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")
PLOT = ROOT / "experiment" / "2026927-python绘图latest"

def sep(t): print("\n" + "=" * 100 + "\n" + t)

# 1) SOURCES dir from data_sources.py
SOURCES = ROOT / "07_交付与文档_delivery_docs" / "03_图件_figures" / "英文_figures_en" / "_sources"
sep("1) data_sources.py SOURCES dir: " + str(SOURCES))
print("exists:", SOURCES.exists())
if SOURCES.exists():
    for f in SOURCES.iterdir():
        print("   ", f.name, f.stat().st_size)

# 2) plot-ws 01_机制实验一 tree
d1 = PLOT / "01_机制实验一_跨语言检索_crosslingual"
sep("2) plot-ws/01_机制实验一 tree")
if d1.exists():
    for dp, dn, fn in os.walk(d1):
        for x in fn:
            print("   ", os.path.join(dp, x).replace(str(PLOT) + os.sep, ""))

# 3) exp1_summary.json real locations
sep("3) exp1_summary.json search in repo")
hits = [p for p in ROOT.rglob("exp1_summary.json") if "__pycache__" not in str(p)]
for p in hits:
    print("   ", p)

# 4) headers of the four system-layer aggregates (plot-ws copy)
for name in ("five_metrics.csv", "per_dimension_means.csv"):
    p = PLOT / "05_系统层对比五配置_system_layer" / "02_结果数据_results" / name
    sep(f"4) {name} header + first rows ({p.exists()})")
    if p.exists():
        with p.open(encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        for r in rows[:4]:
            print("   ", r)
for name in ("mdd.json", "agreement.json"):
    p = PLOT / "05_系统层对比五配置_system_layer" / "02_结果数据_results" / name
    sep(f"5) {name} structure ({p.exists()})")
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8-sig"))

        def walk(o, pre="", depth=0):
            if depth > 2:
                return
            if isinstance(o, dict):
                for k in list(o)[:8]:
                    v = o[k]
                    if isinstance(v, (dict, list)):
                        print("   ", pre + k, "->", type(v).__name__, ("len=" + str(len(v))) if hasattr(v, '__len__') else "")
                        walk(v, pre + k + ".", depth + 1)
                    else:
                        print("   ", pre + k, "=", str(v)[:60])
            elif isinstance(o, list) and o:
                print("   ", pre + "[0] =", str(o[0])[:80])
        walk(d)

# 6) per-question score hunt: filename patterns for question-level judge scores
sep("6) per-question score file hunt")
pat = re.compile(r"(per[_-]?q|question|perq|row|pertrack|per_track|catch|rubric|ten[_-]?dim|D1[_]?D?10|scores?[_-]?(84|all|detail)|detail|answers.*84|84.*answers)", re.I)
exts = (".json", ".jsonl", ".csv")
seen = 0
for p in ROOT.rglob("*"):
    if "__pycache__" in str(p) or p.suffix.lower() not in exts:
        continue
    if pat.search(p.name):
        s = p.stat().st_size
        # question-level candidates are usually the big detail files
        rel = str(p.relative_to(ROOT))
        print(f"   {s:>10,}  {rel}")
        seen += 1
        if seen > 60:
            break

# 7) where are the big per-question JSONLs system-wide (incl answers_final)?
sep("7) all *_answers*.jsonl / *.jsonl > 100KB across repo")
cands = []
for p in ROOT.rglob("*.jsonl"):
    if "__pycache__" in str(p):
        continue
    s = p.stat().st_size
    if s > 100_000:
        cands.append((s, str(p.relative_to(ROOT))))
for s, rel in sorted(cands, reverse=True)[:30]:
    print(f"   {s:>10,}  {rel}")

# 8) web arms + evidence support files
sep("8) web_arms_summary.json fields")
w = PLOT / "09_联网五组对照_web_arms" / "02_结果数据_results" / "web_arms_summary.json"
if w.exists():
    d = json.loads(w.read_text(encoding="utf-8-sig"))
    print(json.dumps(d, ensure_ascii=False, indent=1)[:1500])
wp = PLOT / "09_联网五组对照_web_arms" / "02_结果数据_results" / "webpipe_thresholds.json"
sep("9) webpipe_thresholds.json")
if wp.exists():
    print(wp.read_text(encoding="utf-8-sig")[:600])
sep("10) support_offline_arms.json search")
for p in ROOT.rglob("*offline_arms*.json"):
    print("   ", p.relative_to(ROOT), p.stat().st_size)

# -*- coding: utf-8 -*-
"""Round-1 last probes: MinerU repo-wide, exp1 fields, fig5_pools_judge keys,
87/84 denominators in crosslingual, ten-dim combined provenance search."""
import sys, io, json, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")

def sep(t): print("\n" + "=" * 96 + "\n" + t)

# 1) MinerU repo-wide
sep("1) MinerU repo-wide")
hit = 0
for p in ROOT.rglob("*"):
    if p.suffix not in (".py", ".md", ".yaml", ".yml", ".json", ".txt") or "__pycache__" in str(p):
        continue
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    if re.search(r"mineru|magic[-_]?pdf", t, re.I):
        m = re.search(r".{60}mineru.{80}", t, re.I | re.S)
        print(f"   {p.relative_to(ROOT)}: {(m.group(0) if m else 'hit')[:150]}")
        hit += 1
        if hit > 6:
            break
print("  (total files with hit, capped 6):", hit)

# 2) exp1_summary.json structure (Hit rates & denominators)
SOURCES = ROOT / "07_交付与文档_delivery_docs" / "03_图件_figures" / "英文_figures_en" / "_sources"
sep("2) exp1_summary.json")
d = json.loads((SOURCES / "exp1_summary.json").read_text(encoding="utf-8-sig"))
s = json.dumps(d, ensure_ascii=False, indent=1)
print(s[:2200])

# 3) exp1 per-question & denominators
sep("3) crosslingual per_question.jsonl first-line keys")
pq = ROOT / "experiment" / "13_评测管道_pipeline" / "results" / "retrieval" / "crosslingual_20260918" / "per_question.jsonl"
with pq.open(encoding="utf-8") as f:
    d2 = json.loads(next(f))
print("  keys:", list(d2.keys()))
print("  sample:", json.dumps(d2, ensure_ascii=False)[:400])

# 4) fig5_pools_judge.json (both copies)
sep("4) fig5_pools_judge.json (rerun_v11 e2e)")
p = ROOT / "07_交付与文档_delivery_docs" / "05_实验_rerun_v11" / "e2e" / "runs" / "pools144" / "stats" / "figures_data" / "fig5_pools_judge.json"
d3 = json.loads(p.read_text(encoding="utf-8-sig"))
s3 = json.dumps(d3, ensure_ascii=False, indent=1)
print(s3[:1200])

# 5) combined ten-dim paired values provenance: search +0.6372 / 0.1596 / 0.0173 / 0.1643
sep("5) provenance hunt for +0.6372 / MDD 0.1596 / +0.0173 / 0.1643")
for val in ("0.6372", "0.1596", "0.0173", "0.1643", "0.594", "0.467"):
    hits = 0
    for p in ROOT.rglob("*"):
        if p.suffix not in (".json", ".jsonl", ".csv", ".md", ".txt", ".py") or "__pycache__" in str(p) or p.stat().st_size > 4_000_000:
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if val in t:
            print(f"   [{val}] {p.relative_to(ROOT)}")
            hits += 1
            if hits >= 3:
                break
    if hits == 0:
        print(f"   [{val}] NOT FOUND")

# 6) exp1 sample sizes / denominators: grep 76/84/96 in exp1_summary
sep("6) exp1 n/denominator mentions")
t = os = None
def find_denominator(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (int, float)) and k.lower() in ("n", "n_questions", "denominator", "total", "count"):
                print(f"   {path}.{k} = {v}")
            find_denominator(v, f"{path}.{k}")
d = json.loads((SOURCES / "exp1_summary.json").read_text(encoding="utf-8-sig"))
find_denominator(d)

# 7) real132 QA example availability for Fig5 cases (search 黄龙病/砧木/残留)
sep("7) Fig5 candidate Q&A: search real132 jsonl for the three scenarios")
rx = re.compile(r"黄龙病|砧木|残留")
p = ROOT / "experiment" / "2026927-python绘图latest" / "10_端到端测试_e2e" / "02_结果数据_results" / "real132_answers_final.jsonl"
scene = {"黄龙病": [], "砧木": [], "残留": []}
with p.open(encoding="utf-8") as f:
    for ln in f:
        d4 = json.loads(ln)
        q = str(d4.get("question_zh") or d4.get("question") or "")
        for k in scene:
            if k in q and len(scene[k]) < 2:
                scene[k].append({"id": d4.get("id"), "q": q[:80], "ans_chars": len(str(d4.get("answer") or ""))})
for k, v in scene.items():
    print(f"   {k}: {v}")

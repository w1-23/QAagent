# -*- coding: utf-8 -*-
"""Round-1 deep verification: per-question judge score schema + track mapping +
mdd cross-check + implementation spot checks. READ-ONLY."""
import sys, io, json, csv, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")
SOURCES = ROOT / "07_交付与文档_delivery_docs" / "03_图件_figures" / "英文_figures_en" / "_sources"

def sep(t): print("\n" + "=" * 100 + "\n" + t)

# 1) judge_scores schema: first 2 lines (keys + values truncated)
for j in ("kimi-k2.6", "glm-5.3"):
    p = SOURCES / f"judge_scores_{j}.jsonl"
    sep(f"1) judge_scores_{j}.jsonl: {p.stat().st_size:,} bytes")
    with p.open(encoding="utf-8") as f:
        lines = [next(f) for _ in range(3)]
    for i, ln in enumerate(lines):
        d = json.loads(ln)
        print(f"  line{i} keys:", list(d.keys()))
        s = json.dumps(d, ensure_ascii=False)
        print(f"  line{i} sample:", s[:420])
    # count lines and distinct configs
    configs = {}
    qids = set()
    n = 0
    with p.open(encoding="utf-8") as f:
        for ln in f:
            n += 1
            d = json.loads(ln)
            c = str(d.get("config") or d.get("track") or d.get("arm") or "?")
            configs[c] = configs.get(c, 0) + 1
            qid = str(d.get("question_id") or d.get("qid") or d.get("q_id") or d.get("id") or "?")
            if qid != "?":
                qids.add(qid)
    print(f"  total rows: {n}; distinct configs: {configs}; distinct qids: {len(qids)}")

# 2) full five_metrics.csv (all rows, config names)
sep("2) five_metrics.csv full config list")
p = SOURCES / "five_metrics.csv"
with p.open(encoding="utf-8-sig") as f:
    rows = list(csv.reader(f))
hdr = rows[0]
for r in rows[1:]:
    d = dict(zip(hdr, r))
    print(f"   {d['config']:<22} n={d['n']:<3} kimi mean={d['mean_all_kimi-k2.6']:<8} glm mean={d['mean_all_glm-5.3']:<8} D3(kimi/glm)={d['D3_kimi-k2.6']}/{d['D3_glm-5.3']}")

# 3) mdd.json: same_base A-B values (kimi+glm)
sep("3) mdd.json paired structure")
d = json.loads((SOURCES / "mdd.json").read_text(encoding="utf-8-sig"))
for judge, cfgs in d["by_judge"].items():
    ref = cfgs.get("same_base_trackA")
    print(f"  {judge}: keys={list(cfgs)}")
    if ref:
        print("   A-config dims:", {k: ref[k] for k in list(ref)[:20]})

# 4) web arms denominators: 96 vs 48 — count questions in e2e jsonl referencing web experiment?
sep("4) e2e pools144 / real132 first-line keys")
for name in ("pools144_answers_final.jsonl", "real132_answers_final.jsonl"):
    p = ROOT / "experiment" / "2026927-python绘图latest" / "10_端到端测试_e2e" / "02_结果数据_results" / name
    with p.open(encoding="utf-8") as f:
        d = json.loads(next(f))
    print(f"  {name}: keys={list(d.keys())[:20]}")

# 5) freeze_manifest.json: entries count
sep("5) freeze_manifest.json")
fm = ROOT / "07_交付与文档_delivery_docs" / "04_数据_data" / "冻结_freezed" / "freeze_manifest.json"
d = json.loads(fm.read_text(encoding="utf-8-sig"))
if isinstance(d, dict):
    print("  top keys:", list(d.keys())[:10])
    items = d.get("files") or d.get("items") or []
    print("  entries:", len(items))
    for it in items[:5]:
        print("   ", str(it)[:120])
else:
    print("  list length:", len(d), "first:", str(d[0])[:120])

# 6) corpus keyed under new口径? quick probe of agent gateway log line "Loaded"
sep("6) probe: any file stating 126,494 besides paper docs? (grep experiment+delivery, text files)")
pat = re.compile(r"126[,]?494|4[,]?906\s*份?")
strx = [ROOT / "07_交付与文档_delivery_docs" / "06_文档_docs" / "RUNTIME_TRUTH.md"]
hitcount = 0
for p in ROOT.rglob("*.md"):
    if "__pycache__" in str(p) or p.stat().st_size > 3_000_000:
        continue
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    if pat.search(t) and ("126,494" in t or "126494" in t):
        hits = pat.findall(t)
        print(f"   {p.relative_to(ROOT)}  hits={len(hits)}")
        hitcount += 1
        if hitcount > 12:
            break

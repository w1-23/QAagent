# -*- coding: utf-8 -*-
"""深挖 C48-new-33：检索记录结构、引用台账、以及 pool C 的 expected_behavior 分布。"""
from __future__ import annotations

import collections
import json
import pathlib
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

R = pathlib.Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")
D = R / "07_交付与文档_delivery_docs"
P = D / "05_实验_rerun_v11" / "e2e" / "runs" / "pools144"
T = "C48-new-33"
H1 = re.compile(r"(?m)^#\s*联网获取信息\s*$")
WN = re.compile(r"\[W(\d+)\]")


def load(p):
    return [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]


qs = {q["id"]: q for q in load(P / "questions.jsonl")}
print("=== pool C 的 expected_behavior 分布 ===")
beh = collections.Counter(q["expected_behavior"] for q in qs.values() if q["pool"] == "C")
for k, v in beh.items():
    print(f"  {v:2d}  {k!r}")
print("\n  含 reject 语义的题：",
      [q["id"] for q in qs.values() if q["pool"] == "C" and "reject" in (q["expected_behavior"] or "")])

print("\n" + "=" * 100)
print(f"=== {T} 的 retrieval.jsonl 完整结构 ===")
rec = next(r for r in load(P / "retrieval.jsonl") if r["id"] == T)
for k, v in rec.items():
    if isinstance(v, list):
        print(f"  {k}: list({len(v)})")
        for item in v[:3]:
            print(f"      {json.dumps(item, ensure_ascii=False)[:200]}")
    elif isinstance(v, dict):
        print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:300]}")
    else:
        print(f"  {k}: {v}")

print("\n" + "=" * 100)
print(f"=== 对照一题正常带 [Wn] 的 C 池记录（取 C48-time-001） ===")
try:
    other = next(r for r in load(P / "retrieval.jsonl") if r["id"] == "C48-time-001")
    print(f"  counts: {json.dumps(other.get('counts'), ensure_ascii=False)}")
    print(f"  cited 条数: {len(other.get('cited') or [])}")
    for c in (other.get("cited") or [])[:4]:
        print(f"    [{c.get('ref_id')}] {str(c.get('title'))[:60]} source={c.get('source')}")
except StopIteration:
    print("  未找到")

print("\n" + "=" * 100)
print("=== C 池逐题：联网段 [Wn] 数 vs counts.web_citation_count ===")
ans = {a["id"]: a for a in load(P / "answers_final.jsonl")}
ret = {r["id"]: r for r in load(P / "retrieval.jsonl")}
print(f"{'题号':<18}{'联网段[Wn]':>10}{'web_citation_count':>20}{'citation_count':>16}")
for x in sorted([a for a in ans.values() if a["pool"] == "C"], key=lambda z: z["id"]):
    t = x.get("answer") or ""
    m = H1.search(t)
    na = len(WN.findall(t[m.start():])) if m else 0
    cnt = (ret.get(x["id"], {}).get("counts") or {})
    flag = "  ← 无 [Wn]" if na == 0 else ""
    print(f"{x['id']:<18}{na:>10}{cnt.get('web_citation_count', '-'):>20}"
          f"{cnt.get('citation_count', '-'):>16}{flag}")

# -*- coding: utf-8 -*-
"""专项核查：C48-new-33 —— 题面 / expected_behavior / 最终回答 / 检索记录 / 判分。

目的：判断该题"未附联网引用"是否属于**合理地纠正错误前提**，从而不应被当作隔离失败。
同时复核 48/48 与 47/48 的精确口径。
"""
from __future__ import annotations

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
POOLS = D / "04_数据_data" / "原始_raw" / "pools"
TARGET = "C48-new-33"
H1 = re.compile(r"(?m)^#\s*联网获取信息\s*$")
WN = re.compile(r"\[W\d+\]")


def load(p):
    return [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]


qs = {q["id"]: q for q in load(P / "questions.jsonl")}
ans = {a["id"]: a for a in load(P / "answers_final.jsonl")}
ret = {r["id"]: r for r in load(P / "retrieval.jsonl")}
rawC = {q["id"]: q for q in load(POOLS / "poolC_48.jsonl")}

q = qs[TARGET]
print("=" * 100)
print(f"### {TARGET} —— 题面与预期行为（v11 题集）")
for k in ("pool", "level", "question_used", "requires_web", "expected_behavior",
          "expected_error", "gold_id", "gold_note", "web"):
    print(f"  {k}: {q.get(k)}")
rq = rawC.get(TARGET)
if rq:
    print(f"\n  池定义（poolC_48.jsonl）额外字段：")
    for k in ("type", "source", "domain", "origin_file", "origin_id"):
        if k in rq:
            print(f"    {k}: {rq[k]}")

a = ans[TARGET]
t = a.get("answer") or ""
print("\n" + "=" * 100)
print("### 最终回答（v11 answers_final.jsonl）")
print(f"  长度 {len(t)} 字符；questions 字段 answer_chars={a.get('answer_chars')}")
m = H1.search(t)
if m:
    local, web = t[:m.start()], t[m.start():]
    print(f"  本地段长度 {len(local)}；联网段长度 {len(web)}")
    print(f"  本地段 [Wn] 次数：{len(WN.findall(local))}（应为 0）")
    print(f"  联网段 [Wn] 次数：{len(WN.findall(web))}（应为 0 才是本题特征）")
else:
    print("  ⚠ 无 `# 联网获取信息` 段")
print("\n  --- 全文 ---")
print(t)

print("\n" + "=" * 100)
print("### 检索与引用记录")
r = ret[TARGET]
print(f"  web={r.get('web')}  counts={json.dumps(r.get('counts'), ensure_ascii=False)}")
cited = r.get("cited") or r.get("cited_sse") or []
print(f"  被引条目 {len(cited)}：")
for c in cited[:8]:
    print(f"    [{c.get('ref_id')}] {str(c.get('title'))[:70]}  (source={c.get('source')}, year={c.get('year')})")
print(f"  dropped: {r.get('dropped') or r.get('dropped_sse')}")
print(f"  ref_pool: {r.get('ref_pool')}")

print("\n" + "=" * 100)
print("### 判分（两判官）")
for jf in ("judge_scores_kimi-k2.6.jsonl", "judge_scores_glm-5.3.jsonl"):
    for row in load(P / "judge" / jf):
        if row.get("id") == TARGET:
            print(f"  {jf.split('_')[-1].replace('.jsonl','')}: "
                  + " ".join(f"{k}={row.get(k)}" for k in
                             ("D1", "D2", "D3", "D4", "D6", "D7", "D8", "D9", "D10")))

print("\n" + "=" * 100)
print("### 口径复核：Pool C 的 48/48 与 47/48")
sub = [a for a in ans.values() if a.get("pool") == "C"]
sec = ok = used = 0
for x in sub:
    tx = x.get("answer") or ""
    mm = H1.search(tx)
    if mm:
        sec += 1
        nb, na = len(WN.findall(tx[:mm.start()])), len(WN.findall(tx[mm.start():]))
        if nb == 0:
            ok += 1
        if na > 0:
            used += 1
print(f"  C 池 n={len(sub)}")
print(f"  ① 有独立联网段：{sec}/{len(sub)}")
print(f"  ② 有联网段 且 本地段零 [Wn]（＝分段引用合规）：{ok}/{len(sub)}")
print(f"  ③ 联网段实际使用联网引用：{used}/{len(sub)}")
print(f"  ④ 合规但未使用联网引用的题：{ok - used} 条")
notused = [x["id"] for x in sub
           if H1.search(x.get("answer") or "")
           and len(WN.findall((x.get("answer") or "")[H1.search(x["answer"]).start():])) == 0]
print(f"     题号：{notused}")

# 三池合计
tot_bad = 0
for x in ans.values():
    tx = x.get("answer") or ""
    mm = H1.search(tx)
    bad = len(WN.findall(tx[:mm.start()])) if mm else len(WN.findall(tx))
    tot_bad += bad
print(f"\n  三池 144 条：本地段 [Wn] 越界总数 = {tot_bad}")

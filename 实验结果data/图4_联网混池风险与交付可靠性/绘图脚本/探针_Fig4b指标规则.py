# -*- coding: utf-8 -*-
"""Fig.4b 结构审计指标 —— 规则确定用的只读探针（不落盘、不改原始数据）

必须先确定三件事，才能写进审计表：
  1) v11 三池 144 条答案里，`# 联网获取信息` 小节长什么样（web=off 的题是不是也有一段？）
  2) 「来源边界合规」的精确规则与分子/分母（回答级）
  3) 系统层 84 题「引用标记 / 可解析 / 池外」的精确规则（标记级）
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
S84 = D / "05_实验_rerun_v11" / "system_layer"
HEAD = "联网获取信息"
WN = re.compile(r"\[W(\d+)\]")
MARK = re.compile(r"\[(\d+)\]")


def load(p):
    return [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]


print("=" * 104)
print("1) `联网获取信息` 小节长什么样：按池抽样看该小节后的头 90 字")
print("=" * 104)
ans = load(P / "answers_final.jsonl")
seen = set()
for a in ans:
    pool = a["pool"]
    t = a.get("answer") or ""
    i = t.find(HEAD)
    if i < 0 or pool in seen:
        continue
    seen.add(pool)
    seg = t[i:i + 200].replace("\n", " / ")
    print(f"  池 {pool} · web={a.get('web')} · id={a['id']}")
    print(f"    {seg}")
    print()

print("=" * 104)
print("2) 来源边界规则（回答级）：逐池三项计数")
print("=" * 104)
print(f"{'池':<4}{'n':>4}{'有联网小节':>11}{'本地段零[Wn]':>14}{'联网段含[Wn]':>14}{'本地段含[Wn]':>14}{'联网段[Wn]总数':>16}")
for pool in ("A", "B", "C"):
    sub = [a for a in ans if a["pool"] == pool]
    sec = before0 = after_has = before_has = after_total = 0
    for a in sub:
        t = a.get("answer") or ""
        i = t.find(HEAD)
        if i >= 0:
            sec += 1
            b, aft = t[:i], t[i:]
            nb, na = len(WN.findall(b)), len(WN.findall(aft))
            before0 += 1 if nb == 0 else 0
            before_has += 1 if nb > 0 else 0
            after_has += 1 if na > 0 else 0
            after_total += na
        else:
            nb = len(WN.findall(t))
            before_has += 1 if nb > 0 else 0
    print(f"{pool:<4}{len(sub):>4}{sec:>11}{before0:>14}{after_has:>14}{before_has:>14}{after_total:>16}")

print()
print("=" * 104)
print("3) 合规口径对比：三种候选规则各得多少")
print("=" * 104)
C = [a for a in ans if a["pool"] == "C"]
r1 = r2 = r3 = 0
detail = []
for a in C:
    t = a.get("answer") or ""
    i = t.find(HEAD)
    has_sec = i >= 0
    nb = len(WN.findall(t[:i])) if has_sec else len(WN.findall(t))
    na = len(WN.findall(t[i:])) if has_sec else 0
    if has_sec and nb == 0:
        r1 += 1                                   # 规则1：有联网段 + 本地段零 [Wn]
    if has_sec and nb == 0 and na > 0:
        r2 += 1                                   # 规则2：再加"联网段含 [Wn]"
    if has_sec:
        r3 += 1                                   # 规则3：只要有联网段
    if has_sec and (nb > 0 or na == 0):
        detail.append((a["id"], nb, na))
print(f"  C 池 n=48")
print(f"    规则1 有联网小节 且 本地段零 [Wn]            : {r1}/48")
print(f"    规则2 规则1 且 联网段含 [Wn]（即验收 A4 口径）: {r2}/48")
print(f"    规则3 只要有独立联网小节                      : {r3}/48")
print(f"    不符合规则1或2的题：{detail}")

print()
print("=" * 104)
print("4) 系统层 84 题：引用标记级指标（精确规则）")
print("=" * 104)
a84 = load(S84 / "answers_final.jsonl")
r84 = {d["id"]: d for d in load(S84 / "retrieval.jsonl")}
tot = res = unres = 0
dropped = 0
for a in a84:
    t = a.get("answer_final") or ""
    marks = MARK.findall(t)
    tot += len(marks)
    rr = r84.get(a["id"], {})
    final_ids = {str(c["ref_id"]) for c in rr.get("cited", [])}
    dropped += len(rr.get("dropped", []) or [])
    for m in marks:
        if m in final_ids:
            res += 1
        else:
            unres += 1
print(f"  答案条数 {len(a84)}")
print(f"  正文引用标记总数        : {tot}")
print(f"  可解析（指向被引池内）  : {res}  ({res/tot*100:.2f}%)")
print(f"  不可解析                : {unres}")
print(f"  交付前剥离/重编号（dropped）合计: {dropped}（与交付文本内越界不同层）")

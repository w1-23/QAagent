# -*- coding: utf-8 -*-
"""核查：D 臂（excerpt）有效判定到底是 96 还是 95；以及五臂与报告 27 表格是否一致。"""
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
RUNS = R / "07_交付与文档_delivery_docs" / "04_数据_data" / "原始_raw" / "runs"
J = ("kimi-k2.6", "glm-5.3")
ARMS = [("A", "e2e_web_off"), ("B", "e2e_web_on_unfiltered"), ("C", "e2e_web_on_filtered"),
        ("C'", "e2e_web_on_filtered_v2"), ("D", "e2e_web_on_filtered_excerpt")]
REPORT27 = {"A": 3.1, "B": 24.0, "C": 33.3, "C'": 18.8, "D": 23.2}


def load(p):
    return [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]


print("=" * 100)
print("1) 两个判分文件在 D 臂上的记录数与逐判官判定数")
print("=" * 100)
for fn in ("judge_C_final.jsonl", "judge_C_final_evFull.jsonl"):
    p = RUNS / "e2e_web_on_filtered_excerpt" / fn
    rows = load(p)
    per = {j: 0 for j in J}
    err = 0
    for d in rows:
        for j in J:
            v = d.get(j)
            if isinstance(v, dict):
                per[j] += 1
    print(f"  {fn}: 记录 {len(rows)}  逐判官判定 {per}  合计 {sum(per.values())}")
    ids = [d["id"] for d in rows]
    dup = [i for i in set(ids) if ids.count(i) > 1]
    print(f"    重复题号: {dup or '无'}   题号数 {len(set(ids))}")

print()
print("=" * 100)
print("2) 逐臂：本题号数 / 判定合计 / 幻觉数 / 率 / 与报告 27 对照")
print("=" * 100)
print(f"{'臂':<4}{'题号数':>7}{'判定合计':>9}{'幻觉数':>7}{'率(合并)':>10}{'报告27':>8}{'差':>7}   逐判官 幻觉/判定")
for tag, arm in ARMS:
    rows = load(RUNS / arm / "judge_C_final.jsonl")
    ids = {d["id"] for d in rows}
    h = n = 0
    per = {j: [0, 0] for j in J}
    for d in rows:
        for j in J:
            v = d.get(j)
            if isinstance(v, dict):
                n += 1
                bad = bool(v.get("hallucinated"))
                h += bad
                per[j][0] += bad
                per[j][1] += 1
    rate = h / n * 100 if n else float("nan")
    r27 = REPORT27[tag]
    print(f"{tag:<4}{len(ids):>7}{n:>9}{h:>7}{rate:>9.2f}%{r27:>8.1f}{rate-r27:>+7.2f}   "
          f"kimi {per['kimi-k2.6'][0]}/{per['kimi-k2.6'][1]} · glm {per['glm-5.3'][0]}/{per['glm-5.3'][1]}")

print()
print("=" * 100)
print("3) 冻结汇总件 web_arms_summary.json 的口径")
print("=" * 100)
w = json.loads((R / "实验结果data" / "09_联网五组对照_数据" / "02_结果数据_results"
                / "web_arms_summary.json").read_text(encoding="utf-8"))
for tag in ("A", "B", "C", "C'", "D"):
    a = w["arms"][tag]
    print(f"  {tag:<3} {a['hallucinated']}/{a['n']} = {a['rate_pooled']*100:.2f}%")
print("  ⇒ 冻结件 D 记 22/96；报告 27 与用户规格记 22/95（= 23.2%）")

print()
print("=" * 100)
print("4) D 臂逐题判定完整性（找那 1 条缺失）")
print("=" * 100)
rows = load(RUNS / "e2e_web_on_filtered_excerpt" / "judge_C_final.jsonl")
ans = {a["id"]: a for a in load(RUNS / "e2e_web_on_filtered_excerpt" / "answers.jsonl")}
missing = []
for d in rows:
    for j in J:
        if not isinstance(d.get(j), dict):
            missing.append((d["id"], j))
print(f"  判分文件中缺判官的条目: {missing or '无'}")
# 答案侧：有多少题、是否有 error 或空答案
errs = [(i, a.get("error")) for i, a in ans.items() if a.get("error")]
empt = [i for i, a in ans.items() if not (a.get("answer_final") or "").strip()]
print(f"  答案侧题数 {len(ans)}  有 error 的 {errs or '无'}  空答案 {empt or '无'}")
# 判分文件题号 vs 答案文件题号
jids = {d["id"] for d in rows}
print(f"  判分题号 {len(jids)}；答案题号 {len(ans)}；差集 {sorted(jids ^ set(ans)) or '无'}")

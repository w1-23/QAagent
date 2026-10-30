# -*- coding: utf-8 -*-
"""核查（修正版）：五臂数据是否来自「来源分离」架构之后 + A 臂幻觉的性质

用正确的字段 answer_final 重新统计输出形态，并与 v11 pools144 批次对照。
"""
from __future__ import annotations

import datetime
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
RUNS = D / "04_数据_data" / "原始_raw" / "runs"
V11 = D / "05_实验_rerun_v11" / "e2e" / "runs" / "pools144"

ARMS = [("A", "e2e_web_off"), ("B", "e2e_web_on_unfiltered"), ("C", "e2e_web_on_filtered"),
        ("C'", "e2e_web_on_filtered_v2"), ("D", "e2e_web_on_filtered_excerpt")]
WN = re.compile(r"\[W\d+\]")
TWO = "【联网获取信息】"
H1_LOCAL = "# 本地文献库"


def load(p):
    return [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]


def fmt_stats(answers, field):
    n = len(answers)
    has_wn = sum(1 for a in answers if WN.search(a.get(field) or ""))
    has_two = sum(1 for a in answers if TWO in (a.get(field) or ""))
    has_h1 = sum(1 for a in answers if (a.get(field) or "").lstrip().startswith(H1_LOCAL))
    local_dirty = 0
    for a in answers:
        t = a.get(field) or ""
        head = t.split(TWO)[0]                     # 联网段之前 = 本地段
        if WN.search(head):
            local_dirty += 1
    wn_total = sum(len(WN.findall(a.get(field) or "")) for a in answers)
    return dict(n=n, has_wn=has_wn, has_two=has_two, has_h1=has_h1,
                local_dirty=local_dirty, wn_total=wn_total)


def when(p: pathlib.Path) -> str:
    mt = [x.stat().st_mtime for x in p.iterdir()]
    if not mt:
        return "-"
    return (datetime.datetime.fromtimestamp(min(mt)).strftime("%m-%d %H:%M")
            + " ~ " + datetime.datetime.fromtimestamp(max(mt)).strftime("%m-%d %H:%M"))


print("=" * 104)
print("1) 五臂 positions：answer_final 的输出形态（是否含分离架构标记）")
print("=" * 104)
print(f"{'臂':<4}{'n':>4}{'含[Wn]题数':>11}{'[Wn]总数':>9}{'含【联网获取信息】':>18}{'以# 本地文献库开头':>18}{'本地段含[Wn]':>13}  文件时间")
for tag, arm in ARMS:
    p = RUNS / arm
    ans = load(p / "answers.jsonl")
    s = fmt_stats(ans, "answer_final")
    print(f"{tag:<4}{s['n']:>4}{s['has_wn']:>11}{s['wn_total']:>9}{s['has_two']:>18}{s['has_h1']:>18}{s['local_dirty']:>13}  {when(p)}")

print()
print("=" * 104)
print("2) 对照：v11 pools144（2026-09 重跑）的输出形态")
print("=" * 104)
v11 = load(V11 / "answers_final.jsonl")
s = fmt_stats(v11, "answer")
print(f"  n={s['n']}  含[Wn]题数={s['has_wn']}  [Wn]总数={s['wn_total']}  "
      f"含【联网获取信息】={s['has_two']}  以# 本地文献库开头={s['has_h1']}  本地段含[Wn]={s['local_dirty']}")
print(f"  文件时间: {when(V11)}")

print()
print("=" * 104)
print("3) 五臂答案里是否带 Stage A / Stage B 审计字段（新流水线标志）")
print("=" * 104)
for tag, arm in ARMS:
    ans = load(RUNS / arm / "answers.jsonl")
    has_audit = sum(1 for a in ans if isinstance(a.get("audit"), dict))
    has_sa = sum(1 for a in ans if isinstance(a.get("audit"), dict) and "stage_a" in a["audit"])
    has_sb = sum(1 for a in ans if isinstance(a.get("audit"), dict) and "stage_b" in a["audit"])
    rep = sum(1 for a in ans if a.get("audit_repaired"))
    print(f"  {tag:<4} n={len(ans):<3} 有 audit={has_audit:<3} stage_a={has_sa:<3} stage_b={has_sb:<3} audit_repaired={rep}")

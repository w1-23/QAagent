# -*- coding: utf-8 -*-
"""核查（第三次修正）：用正确的分离段标题判定五臂与 v11 是否已启用「来源分离」输出。

分离架构的两个可观测标志（见 src/core/web_output_separator.py）：
  1) 答案里出现独立的联网小节标题（v10.15 起为 Markdown H1 `# 联网获取信息`）
  2) 该标题**之前**的本地段不含 [Wn]；[Wn] 只出现在联网段内
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
RUNS = D / "04_数据_data" / "原始_raw" / "runs"
V11 = D / "05_实验_rerun_v11" / "e2e" / "runs" / "pools144"
WEB_HEAD = "联网获取信息"
WN = re.compile(r"\[W\d+\]")
ARMS = [("A", "e2e_web_off"), ("B", "e2e_web_on_unfiltered"), ("C", "e2e_web_on_filtered"),
        ("C'", "e2e_web_on_filtered_v2"), ("D", "e2e_web_on_filtered_excerpt")]


def load(p):
    return [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]


def analyse(texts):
    n = len(texts)
    head_any = head_h1 = 0
    wn_before = wn_after = wn_total = 0
    split_ok = 0
    for t in texts:
        t = t or ""
        if WEB_HEAD in t:
            head_any += 1
            if re.search(r"(?m)^#\s*" + WEB_HEAD, t):
                head_h1 += 1
            i = t.find(WEB_HEAD)
            before, after = t[:i], t[i:]
            b, a = len(WN.findall(before)), len(WN.findall(after))
            wn_before += b
            wn_after += a
            if b == 0 and a > 0:
                split_ok += 1
        wn_total += len(WN.findall(t))
    return dict(n=n, head_any=head_any, head_h1=head_h1, wn_total=wn_total,
                wn_before=wn_before, wn_after=wn_after, split_ok=split_ok)


print("=" * 100)
print("五臂（2026-09-23/24）")
print("=" * 100)
print(f"{'臂':<4}{'n':>4}{'有联网小节':>11}{'其中H1':>8}{'[Wn]总数':>10}{'节前[Wn]':>10}{'节后[Wn]':>10}{'本地段无[Wn]且联网段有':>22}")
for tag, arm in ARMS:
    ans = load(RUNS / arm / "answers.jsonl")
    s = analyse([a.get("answer_final") for a in ans])
    print(f"{tag:<4}{s['n']:>4}{s['head_any']:>11}{s['head_h1']:>8}{s['wn_total']:>10}"
          f"{s['wn_before']:>10}{s['wn_after']:>10}{s['split_ok']:>22}")

print()
print("=" * 100)
print("v11 pools144（2026-09-26）")
print("=" * 100)
ans = load(V11 / "answers_final.jsonl")
s = analyse([a.get("answer") for a in ans])
print(f"  n={s['n']}  有联网小节={s['head_any']}  其中H1={s['head_h1']}  [Wn]总数={s['wn_total']}  "
      f"节前[Wn]={s['wn_before']}  节后[Wn]={s['wn_after']}  本地段无[Wn]且联网段有={s['split_ok']}")
for pool in ("A", "B", "C"):
    sub = [a.get("answer") for a in ans if a.get("pool") == pool]
    ss = analyse(sub)
    print(f"    池 {pool}: n={ss['n']} 有联网小节={ss['head_any']} 节前[Wn]={ss['wn_before']} "
          f"节后[Wn]={ss['wn_after']} 合规={ss['split_ok']}")

print()
print("=" * 100)
print("结论要件：v11 C 池（唯一联网池）逐题是否合规")
print("=" * 100)
ok = bad = 0
samples = []
for a in ans:
    if a.get("pool") != "C":
        continue
    t = a.get("answer") or ""
    i = t.find(WEB_HEAD)
    if i < 0:
        bad += 1
        samples.append((a["id"], "无联网小节"))
        continue
    b = len(WN.findall(t[:i]))
    if b == 0:
        ok += 1
    else:
        bad += 1
        samples.append((a["id"], f"本地段含 [Wn]×{b}"))
print(f"  C 池 48 题：本地段无 [Wn] 且含联网小节 = {ok}；异常 = {bad}")
for s in samples[:8]:
    print("   ", s)

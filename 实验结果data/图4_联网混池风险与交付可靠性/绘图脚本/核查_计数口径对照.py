# -*- coding: utf-8 -*-
"""厘清三个量：交付文本 [Wn] 标记数 / cited_sse 条数 / counts.web_citation_count。"""
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

D = pathlib.Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\07_交付与文档_delivery_docs")
P = D / "05_实验_rerun_v11" / "e2e" / "runs" / "pools144"
H1 = re.compile(r"(?m)^#\s*联网获取信息\s*$")
WN = re.compile(r"\[W(\d+)\]")


def load(p):
    return [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]


ans = {a["id"]: a for a in load(P / "answers_final.jsonl")}
ret = {r["id"]: r for r in load(P / "retrieval.jsonl")}

print(f"{'题号':<16}{'[Wn]':>5}{'cited_sse':>10}{'其中web':>8}{'dropped_sse':>12}"
      f"{'counts.web':>11}{'counts.cit':>11}   备注")
rows = []
for x in sorted([a for a in ans.values() if a["pool"] == "C"], key=lambda z: z["id"]):
    t = x.get("answer") or ""
    m = H1.search(t)
    na = len(WN.findall(t[m.start():])) if m else 0
    r = ret.get(x["id"], {})
    cs = r.get("cited_sse") or []
    web = sum(1 for c in cs if c.get("type") == "web" or c.get("source") == "web")
    ds = len(r.get("dropped_sse") or [])
    cnt = r.get("counts") or {}
    note = ""
    if na == 0:
        note = "★ 唯一无 [Wn]"
    elif abs(na - (cnt.get("web_citation_count") or 0)) > 3:
        note = "差值较大"
    rows.append((x["id"], na, len(cs), web, ds, cnt.get("web_citation_count"), cnt.get("citation_count"), note))
    print(f"{x['id']:<16}{na:>5}{len(cs):>10}{web:>8}{ds:>12}"
          f"{cnt.get('web_citation_count', '-'):>11}{cnt.get('citation_count', '-'):>11}   {note}")

print()
print("=== 相关性检查（[Wn] 标记数 vs cited_sse 条数）===")
same = sum(1 for r in rows if r[1] == r[3])
print(f"  [Wn] 数 == cited_sse 中 web 条数 的题：{same}/{len(rows)}")
tot_wn = sum(r[1] for r in rows)
tot_web = sum(r[3] for r in rows)
print(f"  合计：[Wn] {tot_wn} 个；cited_sse 中 web 条目 {tot_web} 个")
print(f"  ⇒ 两者{'一致' if tot_wn == tot_web else '不一致'}")

print("\n=== C48-new-33 的联网检索留痕 ===")
r33 = ret["C48-new-33"]
print(f"  session_count（检索到的联网条目）: {r33.get('session_count')}")
print(f"  cited_sse: {len(r33.get('cited_sse') or [])} 条")
print(f"  dropped_sse: {r33.get('dropped_sse')}")
web_titles = [g.get("title") for g in (r33.get("session_groups") or []) if g.get("group") == "web"]
print(f"  联网检索到的来源标题（前 8）:")
for t in web_titles[:8]:
    print(f"    - {str(t)[:90]}")
print(f"  …… 共 {len(web_titles)} 条")

# -*- coding: utf-8 -*-
"""来源归属审计：**没有 `[Wn]` 的联网信息落在哪一段？**

背景（PI 提出的三种情形）：
  ① 联网信息仅在独立 Web section 但无 `[Wn]` → 编号边界合规，但缺逐条来源追溯
  ② 联网信息进入 Local section 却无 `[Wn]`     → 编号检查可能通过，但语义层可能来源混入
  ③ 系统仅纠正错误前提、未陈述额外联网事实      → 可合理不附联网引用

本脚本对 **全部 144 条**回答做逐段扫描，判定其落入哪一种情形：

  · 硬信号：本地段出现 URL/域名、本地段出现 `[Wn]`（后者已由结构审计为 0）
  · 软信号（代理指标，非证明）：本地段出现**外部权威源名称**（USDA / FiBL / Fundecitrus …）
      再把命中句按语境分为「缺口声明型」（含未检索到/不覆盖/无法…）与「断言型」（需人工复核）
  · 对 C48-new-33 做逐句完整呈现
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

D = pathlib.Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\07_交付与文档_delivery_docs")
P = D / "05_实验_rerun_v11" / "e2e" / "runs" / "pools144"
H1 = re.compile(r"(?m)^#\s*联网获取信息\s*$")
WN = re.compile(r"\[W\d+\]")
LOCAL_REF = re.compile(r"\[(\d+)\]")
URL = re.compile(r"https?://|www\.|\.gov\b|\.org\b|\.com\b|\.cn\b")

# 外部权威源名称（时效题常见）——出现即说明该信息不可能来自本地学术库
EXT = ["USDA", "AMS", "NASS", "Organic INTEGRITY", "OID", "FiBL", "IFOAM", "TRACES",
       "Fundecitrus", "CPVO", "FAO", "Eurostat", "欧盟委员会", "农业农村部", "海关总署",
       "国家统计局", "品种权", "官方公告", "年度报告", "年报", "官网", "路透", "报道称"]

GAP_WORDS = ["未检索到", "未收录", "不覆盖", "无法", "缺口", "无本地", "未包含", "未涉及",
             "不在本地", "缺乏", "未提供", "未给出", "不能作为", "不展开", "未命中的",
             "本地文献库未", "本地库未", "未找到", "没有收录", "未见于"]


def load(p):
    return [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]


def sentences(txt: str):
    parts = re.split(r"(?<=[。；\n])", txt)
    return [s.strip() for s in parts if s.strip()]


ans = load(P / "answers_final.jsonl")
print(f"总回答 {len(ans)} 条\n")
print(f"{'题号':<16}{'段':<6}{'[Wn]':>5}{'[n]':>5}{'URL':>5}{'外部源':>7}  判定")
rows = []
for a in ans:
    t = a.get("answer") or ""
    m = H1.search(t)
    local = t[:m.start()] if m else t
    web = t[m.start():] if m else ""
    for name, seg in (("local", local), ("web", web)):
        n_wn = len(WN.findall(seg))
        n_ref = len(LOCAL_REF.findall(seg))
        n_url = len(URL.findall(seg))
        ext_hits = [e for e in EXT if e in seg]
        rows.append(dict(id=a["id"], pool=a["pool"], seg=name, wn=n_wn, ref=n_ref,
                         url=n_url, ext=ext_hits, text=seg))

print("=" * 100)
print("① 硬信号：本地段出现 URL / 域名")
bad_url = [r for r in rows if r["seg"] == "local" and r["url"] > 0]
print(f"  命中 {len(bad_url)} 条：" + (str([r['id'] for r in bad_url]) if bad_url else "无"))

print("\n② 硬信号：本地段出现 `[Wn]`")
bad_wn = [r for r in rows if r["seg"] == "local" and r["wn"] > 0]
print(f"  命中 {len(bad_wn)} 条：" + (str([r['id'] for r in bad_wn]) if bad_wn else "无（与结构审计一致）"))

print("\n③ 软信号：本地段出现外部权威源名称（代理指标，需按语境复核）")
cand = [r for r in rows if r["seg"] == "local" and r["ext"]]
print(f"  命中 {len(cand)} 条回答（共 {len({r['id'] for r in cand})} 题）")
assertive = []
for r in cand:
    sents = [s for s in sentences(r["text"]) if any(e in s for e in r["ext"])]
    is_gap = all(any(g in s for g in GAP_WORDS) for s in sents) if sents else True
    tag = "缺口声明型" if is_gap else "★断言型（需人工复核）"
    if not is_gap:
        assertive.append((r["id"], sents))
    print(f"    {r['id']:<16} 源={','.join(r['ext'])[:38]:<38} {tag}")
    for s in sents[:2]:
        print(f"        · {s[:110]}")

print("\n" + "=" * 100)
print("④ ★断言型候选逐条呈现（本地段里给出外部源事实的句子）")
if not assertive:
    print("  无")
for qid, sents in assertive:
    print(f"\n--- {qid} ---")
    for s in sents:
        print(f"    {s[:240]}")

print("\n" + "=" * 100)
print("⑤ C48-new-33 逐段完整呈现（关键个案）")
r33 = next(r for r in rows if r["id"] == "C48-new-33")
for name in ("local", "web"):
    seg = next(r for r in rows if r["id"] == "C48-new-33" and r["seg"] == name)
    print(f"\n----- {name} 段（{len(seg['text'])} 字；[Wn]={seg['wn']}；[n]={seg['ref']}；"
          f"URL={seg['url']}；外部源={seg['ext']}）-----")
    print(seg["text"].strip())

print("\n" + "=" * 100)
print("⑥ 汇总")
print(f"  144 条回答中：本地段 URL = {len(bad_url)}；本地段 [Wn] = {len(bad_wn)}；"
      f"本地段含外部源名 = {len({r['id'] for r in cand})}")
print(f"  其中语境为「断言型」需人工复核 = {len(assertive)} 题")

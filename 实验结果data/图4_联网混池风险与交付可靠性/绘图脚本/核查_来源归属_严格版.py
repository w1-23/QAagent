# -*- coding: utf-8 -*-
"""来源归属审计·严格版：本地段是否出现**外部来源的具体事实/数值**？

上一版的缺陷：缺口词表不全（漏了"不收录/未收录/未给出…"），把大量**缺口声明**误判为断言型。
本版改用更保守的判据：
  对本地段中「提到外部权威源」的句子分三类
    A 缺口声明：含 未/不/无/缺/超出/覆盖 等否定或边界词，且**不含非年份数值**
    B 中性指路：不含否定词，但**也不含非年份数值**（只是告诉读者该去哪查）
    C ★疑似外部事实：含**非年份数值**（数字+% / 千分位 / 小数 / 单位），即本地段里给出了具体数据
  只有 C 类才是"语义层面可能来源混入"的候选。
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
URL = re.compile(r"https?://|www\.|\.gov\b|\.org\b|\.com\b|\.cn\b")

EXT = ["USDA", "AMS", "NASS", "APHIS", "ARS", "FAS", "Organic INTEGRITY", "OID",
       "FiBL", "IFOAM", "TRACES", "FAOSTAT", "OECD", "Fundecitrus", "CEMADEN",
       "CPVO", "EFSA", "FAO", "Eurostat", "GACC", "海关总署", "农业农村部",
       "国家统计局", "品种权", "官方公告", "年度报告", "年报", "官网"]

NEG = ["未", "不", "无", "缺", "超出", "覆盖范围外", "不在", "仅", "没有", "非", "之外"]
# 非年份数值：小数、百分比、千分位、带单位数字（排除 19xx/20xx 年份与 [n]/[Wn] 引用号）
NUM = re.compile(r"(?<![\d\[])(?:\d+\.\d+|\d{1,3}(?:,\d{3})+|\d+\s*(?:%|％|ha|公顷|万吨|百万|亿|万|吨|million|tons|boxes|Mt))\b")
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def load(p):
    return [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]


def sents(txt):
    return [s.strip() for s in re.split(r"(?<=[。；\n])", txt) if s.strip()]


def nonyear_numbers(s: str):
    s2 = YEAR.sub(" ", s)
    s2 = re.sub(r"\[W?\d+\]", " ", s2)
    return NUM.findall(s2)


ans = load(P / "answers_final.jsonl")
cat = {"A": [], "B": [], "C": []}
for a in ans:
    t = a.get("answer") or ""
    m = H1.search(t)
    local = t[:m.start()] if m else t
    hit_sents = [s for s in sents(local) if any(e in s for e in EXT)]
    if not hit_sents:
        continue
    for s in hit_sents:
        nums = nonyear_numbers(s)
        has_neg = any(n in s for n in NEG)
        if nums and not URL.search(s):
            cat["C"].append((a["id"], a["pool"], nums[:4], s))
        elif has_neg:
            cat["A"].append((a["id"], s))
        else:
            cat["B"].append((a["id"], s))

print("=== 本地段「提到外部权威源」的句子分类 ===")
print(f"  A 缺口声明型（含否定/边界词、无具体数值）: {len(cat['A'])} 句"
      f"（{len({x[0] for x in cat['A']})} 题）")
print(f"  B 中性指路型（无否定词、也无具体数值）  : {len(cat['B'])} 句"
      f"（{len({x[0] for x in cat['B']})} 题）")
print(f"  C ★含非年份数值（疑似外部事实）        : {len(cat['C'])} 句"
      f"（{len({x[0] for x in cat['C']})} 题）")

print("\n" + "=" * 100)
print("=== C 类逐条（本地段里出现了具体数值的句子）—— 需人工判定 ===")
for qid, pool, nums, s in cat["C"]:
    print(f"\n[{pool}] {qid}  数值={nums}")
    print(f"    {s[:220]}")

print("\n" + "=" * 100)
print("=== 硬信号复核 ===")
n_url = sum(1 for a in ans if URL.search((a.get("answer") or "").split("# 联网获取信息")[0]))
n_wn = sum(1 for a in ans if WN.search((a.get("answer") or "").split("# 联网获取信息")[0]))
print(f"  本地段含 URL/域名的回答：{n_url}/144")
print(f"  本地段含 [Wn] 的回答  ：{n_wn}/144")

print("\n=== C48-new-33 归类 ===")
print("  local 段：无数值、无 URL、无 [Wn]；仅「未检索到/未覆盖/不收录」类缺口声明 + 指明数据出处（无取值）")
print("  web 段  ：全部外部事实（Certifier Survey 结构、OID 字段缺失、FAS 墨西哥 2,600 家、FiBL 各国 ha…）都在此段内")
print("  ⇒ 落入 PI 所列情形 ①：联网信息仅出现在独立联网段，但未附 [Wn] 编号")

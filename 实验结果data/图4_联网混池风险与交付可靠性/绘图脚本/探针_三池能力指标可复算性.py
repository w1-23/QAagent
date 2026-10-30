# -*- coding: utf-8 -*-
"""端到端三池「能力指标」可复算性探针（只读，不出图）

回答一个问题：用户想要的三类压力场景能力指标，能否从 pools144 的题集/答案/检索记录直接算出？
  池 A 库内事实  → 金标证据使用情况（可复算？）
  池 B 对抗诱饵  → 错误前提识别率 / 盲从率（可复算？还是只能语义判定？）
  池 C 库外强时效 → 缺口声明率 / 来源边界遵守率（可复算？）
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import unicodedata

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

R = pathlib.Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")
P = R / "07_交付与文档_delivery_docs" / "05_实验_rerun_v11" / "e2e" / "runs" / "pools144"


def load(p):
    return [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s).strip()


def main() -> int:
    qs = {q["id"]: q for q in load(P / "questions.jsonl")}
    ans = {a["id"]: a for a in load(P / "answers_final.jsonl")}
    ret = {r["id"]: r for r in load(P / "retrieval.jsonl")}
    print(f"题集 {len(qs)} · 答案 {len(ans)} · 检索记录 {len(ret)}")

    # ── 池 A：金标证据使用
    A = [i for i, q in qs.items() if q["pool"] == "A"]
    preamble = hit = miss = 0
    samples = []
    for i in A:
        gnote = (qs[i].get("gold_note") or "").strip()
        if not gnote or gnote.startswith("__"):
            preamble += 1
            continue
        gt = norm(gnote)
        titles = [norm(c.get("title", "")) for c in (ret.get(i, {}).get("cited_sse") or [])]
        ok = any(gt and (gt in t or t in gt or len(set(gt.split()) & set(t.split())) >= 3) for t in titles)
        if ok:
            hit += 1
        else:
            miss += 1
            if len(samples) < 3:
                samples.append((i, gnote[:60], [t[:50] for t in titles[:3]]))
    print(f"\n[A 库内事实] 可判定 {hit+miss}/48（gold_note 为 __preamble__ 等不可判定 {preamble} 条）")
    print(f"  金标来源出现在被引证据中：{hit}  未出现：{miss}")
    for s in samples:
        print("   例:", s)

    # ── 池 B：错误前提识别（客观可判的部分）
    B = [i for i, q in qs.items() if q["pool"] == "B"]
    types = {}
    for i in B:
        t = (qs[i].get("expected_error") or "").split("（")[0]
        types[t] = types.get(t, 0) + 1
    print(f"\n[B 对抗诱饵] expected_error 类型分布（设计信息）：")
    for t, n in sorted(types.items(), key=lambda kv: -kv[1]):
        print(f"   {n:2d}  {t}")
    off = [i for i in B if "语料外" in (qs[i].get("expected_error") or "")]
    print(f"  其中「语料外」类（检测规则=来源是否真实存在）：{len(off)} 条")
    inl = [i for i in B if "语料外" not in (qs[i].get("expected_error") or "")]
    print(f"  「错位」类（对象/技术/时间错位，需语义判定）：{len(inl)} 条")

    # ── 池 C：缺口声明 / 联网引用 / 边界
    C = [i for i, q in qs.items() if q["pool"] == "C"]
    gap_pat = re.compile(r"(证据不足|未检索到|未查到|无法(离线)?核实|未能(找到|检索)|本地(文献|证据|资料)(未|没有|不)|"
                         r"缺口|无本地证据|不包含|未收录|超出(本地|语料)|难以核实|无法给出|暂无)")
    wn_pat = re.compile(r"\[W\d+\]")
    both = only_w = only_g = neither = 0
    local_dirty = 0
    for i in C:
        a = ans[i]["answer"]
        head = a.split("##")[0] if "##" in a else a
        # 本地段 = 第一个二级标题之前的正文（粗略）
        local_txt = a.split("##")[0]
        if wn_pat.search(local_txt):
            local_dirty += 1
        has_gap = bool(gap_pat.search(a))
        has_w = bool(wn_pat.search(a))
        if has_gap and has_w:
            both += 1
        elif has_w:
            only_w += 1
        elif has_gap:
            only_g += 1
        else:
            neither += 1
    ok = both + only_w + only_g
    print(f"\n[C 库外强时效] n=48")
    print(f"  缺口声明 且 有[Wn]：{both}   仅[Wn]：{only_w}   仅缺口声明：{only_g}   两者皆无：{neither}")
    print(f"  达标率（[Wn] 或 缺口声明）= {ok}/48 = {ok/48*100:.1f}%")
    print(f"  本地段（首个二级标题前）出现 [Wn] 的题数：{local_dirty}（边界越界）")

    # ── 相邻版本的对照（说明为何不能混用）
    csvp = R / "07_交付与文档_delivery_docs" / "04_数据_data" / "原始_raw" / "system_level" / "three_pools_benchmark_final.csv"
    print(f"\n[版本核对] 早期批次汇总件存在：{csvp.exists()}")
    print("  该件记 A 池 D3=3.844 / D4=3.156；而 v11 pools144 记 A 池 D3=4.2917/4.50、D4=4.25/4.0208（Kimi/GLM）")
    print("  ⇒ 两者不是同一批次，行为率不可与 v11 的 ARE 均值混画。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

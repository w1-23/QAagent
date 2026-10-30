# -*- coding: utf-8 -*-
"""核查：五臂联网实验的 A 臂（no web）为何仍有 3.1% 幻觉率，以及这批数据是否来自「分离架构上线之后」。

只读，不出图。检查四件事：
  1. A 臂被判 hallucinated 的具体题目、判官理由、答案片段
  2. 五臂答案的**输出形态**：是否出现分离架构的标记（【联网获取信息】/ 两段式 / 本地段无 [Wn]）
  3. 五臂运行的时间戳与来源（meta / provenance）
  4. 与仓库中「分离架构上线」相关记录对照
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
ARMS = [("A", "e2e_web_off"), ("B", "e2e_web_on_unfiltered"), ("C", "e2e_web_on_filtered"),
        ("C'", "e2e_web_on_filtered_v2"), ("D", "e2e_web_on_filtered_excerpt")]
JUDGES = ("kimi-k2.6", "glm-5.3")


def load(p):
    return [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]


def main() -> int:
    print("=" * 96)
    print("1) A 臂（no web）被判 hallucinated 的逐条明细")
    print("=" * 96)
    ans = {a["id"]: a for a in load(RUNS / "e2e_web_off" / "answers.jsonl")}
    jud = load(RUNS / "e2e_web_off" / "judge_C_final.jsonl")
    hits = []
    for d in jud:
        for j in JUDGES:
            v = d.get(j)
            if isinstance(v, dict) and v.get("hallucinated"):
                hits.append((d["id"], j, v))
    print(f"A 臂被判 hallucinated 的判定数：{len(hits)}（题数 {len({h[0] for h in hits})}）\n")
    for qid, j, v in hits:
        a = ans.get(qid, {})
        txt = a.get("answer", "")
        print(f"--- {qid} · {j} ---")
        print(f"  honest_gap={v.get('honest_gap')}  web_used={v.get('web_used')}  "
              f"cite_correct={v.get('cite_correct')}  source_credibility={v.get('source_credibility')}")
        print(f"  理由: {str(v.get('reason'))[:420]}")
        print(f"  答案前 260 字: {txt[:260].replace(chr(10),' / ')}")
        has_wn = bool(re.search(r"\[W\d+\]", txt))
        has_two = "【联网获取信息】" in txt
        print(f"  答案长度 {a.get('answer_chars')} 字符；含 [Wn]？ {has_wn}；含【联网获取信息】？ {has_two}")
        print()

    print("=" * 96)
    print("2) 五臂答案的输出形态（分离架构标记是否出现）")
    print("=" * 96)
    pat_wn = re.compile(r"\[W\d+\]")
    key_two = "【联网获取信息】"
    print(f"{'臂':<4}{'n':>4}{'含[Wn]':>9}{'含【联网获取信息】':>18}{'本地段含[Wn]':>14}{'含##二级标题':>13}")
    for tag, arm in ARMS:
        rows = load(RUNS / arm / "answers.jsonl")
        n = len(rows)
        has_wn = sum(1 for r in rows if pat_wn.search(r.get("answer", "")))
        has_two = sum(1 for r in rows if key_two in r.get("answer", ""))
        local_dirty = 0
        has_h2 = 0
        for r in rows:
            t = r.get("answer", "")
            head = t.split("\n## ")[0]
            if pat_wn.search(head):
                local_dirty += 1
            if "\n## " in t:
                has_h2 += 1
        print(f"{tag:<4}{n:>4}{has_wn:>9}{has_two:>18}{local_dirty:>14}{has_h2:>13}")

    print()
    print("=" * 96)
    print("3) 五臂的来源与时间")
    print("=" * 96)
    for tag, arm in ARMS:
        p = RUNS / arm
        files = sorted(x.name for x in p.iterdir())
        mt = [x.stat().st_mtime for x in p.iterdir()]
        import datetime
        newest = datetime.datetime.fromtimestamp(max(mt)).strftime("%Y-%m-%d %H:%M") if mt else "-"
        oldest = datetime.datetime.fromtimestamp(min(mt)).strftime("%Y-%m-%d %H:%M") if mt else "-"
        print(f"  {tag:<4} {arm:<32} 文件 {files}")
        print(f"       文件时间范围 {oldest} ~ {newest}")
    # meta/provenance
    for cand in ("meta", "provenance.json", "run.json"):
        for tag, arm in ARMS:
            p = RUNS / arm / cand
            if p.exists():
                print(f"  发现 {arm}/{cand}")
    print("\n  答案记录内自带字段（A 臂首条）:")
    print("   ", json.dumps({k: v for k, v in list(ans.values())[0].items() if k != "answer"},
                            ensure_ascii=False)[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

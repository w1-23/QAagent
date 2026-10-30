# -*- coding: utf-8 -*-
"""三池原始数据实况清点（只读，不出图）——供人工核对

回答：每个池现在到底有哪些原始文件、哪些字段、哪些能直接支撑
「Gold-supported / False-premise rejection / Boundary-respecting」这类任务型终点。
"""
from __future__ import annotations

import csv
import json
import pathlib
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

R = pathlib.Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")
D = R / "07_交付与文档_delivery_docs" / "04_数据_data" / "原始_raw"
V11 = R / "07_交付与文档_delivery_docs" / "05_实验_rerun_v11" / "e2e" / "runs" / "pools144"


def show_jsonl(p: pathlib.Path, label: str, n=1):
    print(f"\n### {label}\n    {p}")
    if not p.exists():
        print("    （不存在）"); return
    with p.open(encoding="utf-8") as f:
        for i, ln in enumerate(f):
            if i >= n: break
            d = json.loads(ln)
            print(f"    字段({len(d)}): {list(d.keys())}")
            print(f"    样例: {json.dumps(d, ensure_ascii=False)[:600]}")


def show_csv(p: pathlib.Path, label: str, n=2):
    print(f"\n### {label}\n    {p}")
    if not p.exists():
        print("    （不存在）"); return
    with p.open(encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        cols = rd.fieldnames or []
        print(f"    列({len(cols)}): {cols}")
        for i, row in enumerate(rd):
            if i >= n: break
            print(f"    行{i+1}: {json.dumps(row, ensure_ascii=False)[:400]}")


print("=" * 100)
print("A. 最新批次 v11（pools144，2026-09 重跑）—— ARE 十维判分")
print("=" * 100)
show_jsonl(V11 / "judge" / "judge_scores_kimi-k2.6.jsonl", "v11 判分（Kimi）")
show_jsonl(V11 / "questions.jsonl", "v11 题集")
show_jsonl(V11 / "retrieval.jsonl", "v11 检索记录")
show_jsonl(V11 / "answers_final.jsonl", "v11 最终答案")

print("\n" + "=" * 100)
print("B. 上一批次 v10_3（runs/pools144_v10_3）—— 逐池判分，含行为标签")
print("=" * 100)
for pool in ("A", "B", "C"):
    show_jsonl(D / "runs" / "pools144_v10_3" / f"judge_{pool}_final.jsonl", f"v10_3 池 {pool} 判分")

print("\n" + "=" * 100)
print("C. system_level 逐池判分表（CSV，v10 批次汇总）")
print("=" * 100)
for pool in ("A", "B", "C"):
    show_csv(D / "system_level" / f"judge_pool{pool}_final.csv", f"judge_pool{pool}_final.csv")
show_csv(D / "system_level" / "three_pools_benchmark_final.csv", "three_pools_benchmark_final.csv")
show_csv(D / "system_level" / "five_metrics_poolA_48_final.csv", "five_metrics_poolA_48_final.csv")
show_csv(D / "system_level" / "poolC_web_vs_noWeb_final.csv", "poolC_web_vs_noWeb_final.csv")

print("\n" + "=" * 100)
print("D. 五臂联网实验（runs/e2e_web_*）—— 用的就是 C 池 48 题，含逐题行为标签")
print("=" * 100)
show_jsonl(D / "runs" / "e2e_web_off" / "judge_C_final.jsonl", "五臂 A(web off) 池C判分")
show_jsonl(D / "runs" / "e2e_web_on_filtered_excerpt" / "judge_C_final.jsonl", "五臂 D(excerpt) 池C判分")

print("\n" + "=" * 100)
print("E. 池定义文件（题面 + 金标 + 期望行为）")
print("=" * 100)
show_jsonl(D / "pools" / "poolA_48.jsonl", "poolA_48.jsonl（含 gold_paper_id / key_facts）")
show_jsonl(D / "pools" / "poolB_48.jsonl", "poolB_48.jsonl（含 expected_error 检测点）")
show_jsonl(D / "pools" / "poolC_48.jsonl", "poolC_48.jsonl（含 expected_behavior）")
show_csv(D / "pools" / "poolB_48.csv", "poolB_48.csv")
show_csv(D / "pools" / "poolC_48.csv", "poolC_48.csv")

#!/usr/bin/env python3
"""Create a run plan without exposing target chunk/Gold fields to runtime."""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
from pathlib import Path

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-dir", default="03_benchmarks/citation60")
    parser.add_argument("--out", default="06_runs/manifests/citation_run_plan.csv")
    parser.add_argument("--meta", default="06_runs/manifests/citation_run_plan_meta.json")
    parser.add_argument("--seed", type=int, default=20261104)
    args = parser.parse_args()
    bench = (ROOT / args.benchmark_dir).resolve()
    qpath = bench / "questions_FROZEN.jsonl"
    if not qpath.is_file():
        raise SystemExit(f"frozen questions missing: {qpath}")
    out = (ROOT / args.out).resolve()
    meta = (ROOT / args.meta).resolve()
    if out.exists() or meta.exists():
        raise SystemExit("refuse overwrite of run plan/meta")
    questions = [json.loads(line) for line in qpath.open("r", encoding="utf-8") if line.strip()]
    plan: list[dict] = []
    scope_label = {
        "cqa_literature": "柑橘科研文献",
        "cqa_methods": "柑橘实验方法",
        "cqa_deng_book": "中国柑橘品种志",
        "cqa_translated": "柑橘科研文献中文译本",
        "cqa_ucr_varieties": "UCR 柑橘品种记录",
    }
    for q in questions:
        corpus = q["target_corpus_id"]
        prompt = (
            f"仅使用本地语料“{scope_label.get(corpus, corpus)}”回答：{q['question']}\n"
            "请在答案中引用所依据的原文本地来源，并给出可核验的原文位置。"
            "不要使用联网或其他语料。"
        )
        for condition in ("B0", "B1", "B2"):
            plan.append({
                "run_id": f"{q['question_id']}_{condition}_T1",
                "kind": "citation",
                "case_id": q["question_id"],
                "condition": condition,
                "trial": 1,
                "prompt": prompt,
                "source_scope": f"仅本地:{corpus}",
                "attachment": "",
                "required_groups": "" if condition == "B0" else "local",
                "forbidden_groups": "web|read_file|inspect_table|audit_data|verify_refs|export",
            })
    random.Random(args.seed).shuffle(plan)
    for i, row in enumerate(plan, 1):
        row["run_order"] = i
    fields = list(plan[0])
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(plan)
    import hashlib
    meta_obj = {
        "status": "FROZEN",
        "seed": args.seed,
        "questions_file": str(qpath.relative_to(ROOT)).replace("\\", "/"),
        "questions_sha256": hashlib.sha256(qpath.read_bytes()).hexdigest(),
        "run_plan_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "total": len(plan),
        "expected": {"questions": len(questions), "conditions": 3, "runs": len(questions) * 3},
        "leakage_guard": "runtime plan contains prompt/source scope only, never target_chunk_id or chunk text",
    }
    meta.write_text(json.dumps(meta_obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(meta_obj, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


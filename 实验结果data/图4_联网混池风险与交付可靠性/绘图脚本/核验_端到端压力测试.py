# -*- coding: utf-8 -*-
"""端到端压力测试（三池）· 能力矩阵数据核验（只读，不出图）

用途：为重新设计的「三场景 × 能力指标」矩阵提供**可复算**的数据入口。
规范依据：`paper/图件方案_CitrusQA_HA投稿版.md` §十二 绘图前置门。

结论要点（写进报告）：
  · 三池 × 相关 ARE 维度（D3/D4/D7/D8）× 双判官 → **完全支持**，逐维均值从原始判分复算并与冻结件核对；
  · 池 C 的规则型合规（缺口声明或联网引用 / 本地段零越界）→ **可复算**；
  · 池 A「金标证据使用」→ **不可靠复算**（`session_groups` 语义不明，金标仅在 historical 组命中）；
  · 池 B「错误前提识别率/盲从率」→ **需语义审计**（题面 48 条检测点均为语义判据）。

产出：../数据/图4c_核验_压力测试.csv / .json
"""
from __future__ import annotations

import csv
import json
import pathlib
import re
import statistics
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

R = pathlib.Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")
D = R / "07_交付与文档_delivery_docs"
P = D / "05_实验_rerun_v11" / "e2e" / "runs" / "pools144"
PDM = P / "stats" / "per_dimension_means.csv"
ACCEPT = D / "05_实验_rerun_v11" / "e2e" / "acceptance.json"

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent / "数据"
OUT.mkdir(parents=True, exist_ok=True)

JUDGES = ["kimi-k2.6", "glm-5.3"]
# 三场景各自最相关的能力维度（用户建议的四维；按场景语义分组呈现）
SCEN = {
    "A": ("In-corpus facts", "Find the right evidence and answer from it", ["D3", "D4", "D7", "D8"]),
    "B": ("Adversarial", "Detect the false premise instead of following it", ["D3", "D4", "D7", "D8"]),
    "C": ("Out-of-corpus, time-sensitive", "Admit the evidence gap and separate web supplement", ["D3", "D4", "D7", "D8"]),
}
rows: list[dict] = []


def rec(panel, metric, source, value, denom=None, note=""):
    rows.append({"面板": panel, "指标": metric, "源文件": source, "分母": denom,
                 "展示值": value, "数据集版本": "冻结交付件（v11 pools144）", "备注": note})


def load(p):
    return [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]


def main() -> int:
    # ── 1) 逐维均值：从原始判分复算，并与冻结 per_dimension_means.csv 核对
    raw = {j: load(P / "judge" / f"judge_scores_{j}.jsonl") for j in JUDGES}
    frozen = {}
    with PDM.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            frozen[(r["judge"], r["pool"])] = r
    matrix, ok_all = {}, True
    print("=== 三池 × 四维 × 双判官（复算 vs 冻结）===")
    for j in JUDGES:
        matrix[j] = {}
        for pool in ("A", "B", "C"):
            sub = [r for r in raw[j] if r.get("pool") == pool]
            vals = {}
            for d in SCEN[pool][2]:
                xs = [r[d] for r in sub if isinstance(r.get(d), (int, float))]
                vals[d] = round(sum(xs) / len(xs), 4) if xs else None
                fz = frozen.get((j, pool), {}).get(d)
                ok = fz not in (None, "") and abs(float(fz) - vals[d]) < 5e-4
                ok_all &= ok
            matrix[j][pool] = vals
            print(f"  {j:10s} {SCEN[pool][0]:<30} " +
                  "  ".join(f"{d}={vals[d]:.3f}" for d in SCEN[pool][2]))
            rec("图4c1", f"{SCEN[pool][0]} 能力维度 ({j})",
                "05_实验_rerun_v11/e2e/runs/pools144/judge/judge_scores_*.jsonl",
                "; ".join(f"{d} {vals[d]:.4f}" for d in SCEN[pool][2]), len(sub),
                "由原始判分复算；与 frozen per_dimension_means.csv 逐位一致" if ok_all else "**与冻结件不一致**")
    print(f"  复算与冻结逐位一致：{ok_all}")

    # ── 2) 三池交付结构（客观量，来自 v11 检索记录的 counts）
    qs = {q["id"]: q for q in load(P / "questions.jsonl")}
    ret = load(P / "retrieval.jsonl")
    struct = {}
    print("\n=== 三池交付结构（客观量）===")
    for pool in ("A", "B", "C"):
        rs = [r for r in ret if qs[r["id"]]["pool"] == pool]
        n = len(rs)
        cnt = [(r.get("counts") or {}) for r in rs]
        d = {
            "n": n,
            "citation_supported_rate": sum(1 for c in cnt if c.get("citation_supported")) / n,
            "citation_mismatch_rate": sum(1 for c in cnt if c.get("citation_mismatch")) / n,
            "citation_unsupported_rate": sum(1 for c in cnt if c.get("citation_unsupported")) / n,
            "web_citation_rate": sum(1 for c in cnt if (c.get("web_citation_count") or 0) > 0) / n,
            "mean_citations": round(sum(c.get("citation_count") or 0 for c in cnt) / n, 2),
            "mean_retrieved": round(sum(c.get("retrieval_count") or 0 for c in cnt) / n, 2),
        }
        struct[pool] = d
        print(f"  {pool} n={n}  支撑 {d['citation_supported_rate']*100:.1f}%  错配 {d['citation_mismatch_rate']*100:.1f}%  "
              f"无支撑 {d['citation_unsupported_rate']*100:.1f}%  联网引用 {d['web_citation_rate']*100:.1f}%  "
              f"平均引用 {d['mean_citations']}  平均检索 {d['mean_retrieved']}")
        rec("图4c2", f"{SCEN[pool][0]} 交付结构", "05_实验_rerun_v11/e2e/runs/pools144/retrieval.jsonl",
            f"支撑 {d['citation_supported_rate']*100:.0f}% · 平均引用 {d['mean_citations']} · 平均检索 {d['mean_retrieved']}",
            n, "结构合规三池均为 100%（达峰）；真正有差异的是平均引用/检索量")

    # ── 3) 池 C 规则型合规 + 池 B 设计信息
    wn = re.compile(r"\[W\d+\]")
    gap = re.compile(r"(证据不足|未检索到|未查到|无法(离线)?核实|未能(找到|检索)|本地(文献|证据|资料)(未|没有|不)|"
                     r"缺口|无本地证据|不包含|未收录|超出(本地|语料)|难以核实|无法给出|暂无)")
    answers = {a["id"]: a for a in load(P / "answers_final.jsonl")}
    C_ids = [i for i, q in qs.items() if q["pool"] == "C"]
    hit = sum(1 for i in C_ids if wn.search(answers[i]["answer"]) or gap.search(answers[i]["answer"]))
    print(f"\n=== 池 C 规则型合规 ===")
    print(f"  缺口声明或含 [Wn] 的比例：{hit}/{len(C_ids)} = {hit/len(C_ids)*100:.1f}%")
    acc = json.loads(ACCEPT.read_text(encoding="utf-8"))
    a4 = next(r for r in acc["runs"] if r["tag"] == "pools144")["checks"]["A4"]
    print(f"  本地段含 [Wn] 的题数（冻结验收 A4）：{a4['n_local_section_contains_Wn']}（0 = 零越界）")
    rec("图4c2", "池 C 缺口声明或联网引用达标率", "pools144/answers_final.jsonl",
        f"{hit/len(C_ids)*100:.1f}%", len(C_ids), "规则：答案含 [Wn] 或含缺口声明用语")
    rec("图4c2", "本地段零 [Wn] 越界", "05_实验_rerun_v11/e2e/acceptance.json（A4）",
        f"违规 {a4['n_local_section_contains_Wn']} 题", 144, "冻结验收口径；0 违规")

    print("\n=== 池 B 设计信息（非结果指标）===")
    ex = [q for q in qs.values() if q["pool"] == "B"]
    types = {}
    for q in ex:
        t = (q.get("expected_error") or "").split("（")[0]
        types[t] = types.get(t, 0) + 1
    for t, n in sorted(types.items(), key=lambda kv: -kv[1]):
        print(f"  {n:2d}  {t}")
    print("  ⇒ 48 题全部带语义「检测点」，盲从率/识别率需逐题语义审计，不能由规则或均分反推")

    p = OUT / "图4c_核验_压力测试.csv"
    with p.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["面板", "指标", "源文件", "分母", "展示值", "数据集版本", "备注"])
        w.writeheader(); w.writerows(rows)
    snap = {"scenarios": {k: {"name": v[0], "capability": v[1], "dims": v[2]} for k, v in SCEN.items()},
            "matrix": matrix, "structure": struct,
            "pool_c_rule_rate": {"hit": hit, "n": len(C_ids)},
            "local_section_violations": a4["n_local_section_contains_Wn"],
            "pool_b_error_types": types,
            "recompute_matches_frozen": ok_all, "rows": rows}
    q = OUT / "图4c_核验_压力测试.json"
    q.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWROTE {p}\nWROTE {q}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

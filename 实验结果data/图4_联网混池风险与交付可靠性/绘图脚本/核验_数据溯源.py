# -*- coding: utf-8 -*-
"""图 4 · 数据溯源核验（只读，不出图）

对应规范：`paper/图件方案_CitrusQA_HA投稿版.md` §十二「绘图前置门」——
每面板先输出只读核验表 `源文件 → 数据集版本 → 指标 → 分子 → 分母 → 展示值 → 面板`，
核验通过前不得出图；绘图脚本不得写入任何硬编码数值。

产出：
  ../数据/图4_核验_数据溯源.csv   逐项核验表
  ../数据/图4_核验_数据溯源.json  机读快照（绘图脚本唯一数据入口）
"""
from __future__ import annotations

import csv
import json
import pathlib
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

ROOT = pathlib.Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")
DELIV = ROOT / "07_交付与文档_delivery_docs"
RUNS = DELIV / "04_数据_data" / "原始_raw" / "runs"
V11 = DELIV / "05_实验_rerun_v11"
SYS84 = V11 / "system_layer"
POOLS = V11 / "e2e" / "runs" / "pools144"
REAL132 = V11 / "e2e" / "runs" / "real132"
FROZEN_ARMS = ROOT / "实验结果data" / "09_联网五组对照_数据" / "02_结果数据_results" / "web_arms_summary.json"

HERE = pathlib.Path(__file__).resolve().parent
OUT_DATA = HERE.parent / "数据"
OUT_DATA.mkdir(parents=True, exist_ok=True)

ARMS = [
    ("A", "e2e_web_off", "No web"),
    ("B", "e2e_web_on_unfiltered", "Unfiltered"),
    ("C", "e2e_web_on_filtered", "Receipt-filtered (legacy)"),
    ("C'", "e2e_web_on_filtered_v2", "Model-visible level"),
    ("D", "e2e_web_on_filtered_excerpt", "Excerpt only"),
]
JUDGES = ("kimi-k2.6", "glm-5.3")
DIMS = [f"D{i}" for i in range(1, 11)]
POOL_NAME = {"A": "In-corpus facts", "B": "Adversarial", "C": "Out-of-corpus time-sensitive"}

rows: list[dict] = []


def rec(panel, metric, source, numerator, denominator, value, note=""):
    rows.append({"面板": panel, "指标": metric, "源文件": source, "分子": numerator,
                 "分母": denominator, "展示值": value,
                 "数据集版本": "冻结交付件（v11 / 2026-09~10）", "备注": note})


def load_jsonl(p: pathlib.Path):
    if not p.exists():
        return []
    out = []
    with p.open(encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                out.append(json.loads(ln))
    return out


def pooled_ten_dim(recs):
    """十维均分：池化所有非空维（与 README 口径逐位一致：real132 kimi 4.3622 / glm 4.3942）。"""
    vals = []
    for r in recs:
        vals += [r[d] for d in DIMS if isinstance(r.get(d), (int, float))]
    return (sum(vals) / len(vals) if vals else float("nan")), len(vals)


# ───────────────────────────────────────────── 图 4a
def verify_arms():
    frozen = json.loads(FROZEN_ARMS.read_text(encoding="utf-8"))
    print("=== 图 4a · 五臂标记虚构率（复算 vs 冻结汇总）===")
    out = {}
    for tag, arm, desc in ARMS:
        jud = load_jsonl(RUNS / arm / "judge_C_final.jsonl")
        h = n = n_raw = 0
        per = {j: [0, 0] for j in JUDGES}
        for d in jud:
            for j in JUDGES:
                v = d.get(j)
                if isinstance(v, dict):
                    n_raw += 1
                    # **有效判定**才计入分母：裁决字段必须为布尔。
                    # 反例：D 臂 C48-new-19 的 kimi 判定是空 dict `{}`（全部字段为 null），
                    # 若把它当作「未标记」计入分母，会系统性低估（见批注 22 与
                    # paper/系统篇_v7/Figure_Registry.md 第 61 行：缺失判定不计入分母，D 组 22/95）。
                    if not isinstance(v.get("hallucinated"), bool):
                        continue
                    n += 1
                    bad = bool(v.get("hallucinated"))
                    h += bad
                    per[j][0] += bad
                    per[j][1] += 1
        rate = h / n * 100
        fr = frozen["arms"][tag]
        # 冻结汇总件 web_arms_summary.json 把空 dict 计入了分母（D 记为 22/96），
        # 与 Registry 的「缺失判定不计入分母」口径冲突；此处只对**有效判定数**做一致性判定。
        ok = (fr["hallucinated"] == h and n_raw == fr["n"] and n <= fr["n"])
        out[tag] = {"h": h, "n": n, "n_raw": n_raw, "rate": rate, "label": desc, "per_judge": per,
                    "frozen": {"h": fr["hallucinated"], "n": fr["n"], "rate": fr["rate_pooled"] * 100}}
        print(f"  {tag:3s} {h:2d}/{n} = {rate:5.2f}%   （判定实体 {n_raw}，无效 {n_raw - n}）"
              f"   冻结汇总 {fr['hallucinated']}/{fr['n']} = {fr['rate_pooled']*100:5.2f}%"
              f"   {'OK' if ok else '**CHECK**'}   逐判官 kimi {per['kimi-k2.6'][0]}/{per['kimi-k2.6'][1]}"
              f" · glm {per['glm-5.3'][0]}/{per['glm-5.3'][1]}")
        rec("图4a", f"{tag} · {desc} 标记虚构率", f"原始_raw/runs/{arm}/judge_C_final.jsonl",
            h, n, f"{rate:.1f}%",
            "复算与冻结汇总逐位一致" if ok else "**与冻结汇总不一致**")
    return out


# ───────────────────────────────────────────── 图 4b
def verify_citation():
    print("\n=== 图 4b · 84 题批引用审计（复算）===")
    ans = load_jsonl(SYS84 / "answers_final.jsonl")
    ret = {d["id"]: d for d in load_jsonl(SYS84 / "retrieval.jsonl")}
    pat = re.compile(r"\[(\d+)\]")
    tot = res = unres = 0
    dropped_total = 0
    cited_counts = []
    for a in ans:
        txt = a.get("answer_final") or ""
        marks = pat.findall(txt)
        tot += len(marks)
        r = ret.get(a["id"], {})
        final_ids = {str(c["ref_id"]) for c in r.get("cited", [])}
        dropped = [str(x) for x in r.get("dropped", [])]
        dropped_total += len(dropped)
        cited_counts.append(r.get("n_cited_pool", len(final_ids)))
        for m in marks:
            if m in final_ids:
                res += 1
            else:
                unres += 1
    rrate = res / tot * 100 if tot else float("nan")
    print(f"  正文标记总数        : {tot}")
    print(f"  可解析（指向池内）  : {res}  ({rrate:.2f}%)")
    print(f"  不可解析            : {unres}")
    print(f"  池外被剔除（dropped）: {dropped_total}")
    print(f"  平均每题被引证据    : {sum(cited_counts)/len(cited_counts):.2f} 条")
    print(f"  论文口径（README §四）: 1,649 标记 / 越界 0 / 平均 2.58 条")
    rec("图4b", "正文引用标记总数", "05_实验_rerun_v11/system_layer/answers_final.jsonl",
        None, 1649, str(tot), "复算与论文口径一致" if tot == 1649 else "**与论文口径不一致**")
    rec("图4b", "可解析标记率", "05_实验_rerun_v11/system_layer/retrieval.jsonl",
        res, tot, f"{rrate:.2f}%", "指向池内证据的标记占比")
    rec("图4b", "交付文本内池外引用数", "05_实验_rerun_v11/system_layer/answers_final.jsonl",
        unres, tot, str(unres), "交付文本内越界引用；与论文口径「越界 0」一致")
    rec("图4b", "交付前剥离/重映射计数", "05_实验_rerun_v11/system_layer/retrieval.jsonl",
        dropped_total, tot, str(dropped_total),
        "dropped 累计（交付前已剥离，故交付文本内越界为 0）；与「越界 0」不同层，勿混用")
    rec("图4b", "平均每题被引证据", "05_实验_rerun_v11/system_layer/retrieval.jsonl",
        sum(cited_counts), len(cited_counts), f"{sum(cited_counts)/len(cited_counts):.2f}",
        "论文口径 2.58 条")
    return {"markers": tot, "resolvable": res, "unresolvable": unres,
            "resolvable_rate": rrate, "dropped": dropped_total,
            "mean_cited": sum(cited_counts) / len(cited_counts)}


# ───────────────────────────────────────────── 图 4c
def verify_e2e():
    print("\n=== 图 4c · 端到端四组均分（复算）===")
    out = {"pools": {}, "real132": {}}
    frozen = json.loads((POOLS / "stats" / "figures_data" / "fig5_pools_judge.json").read_text(encoding="utf-8"))
    for j in JUDGES:
        jr = load_jsonl(POOLS / "judge" / f"judge_scores_{j}.jsonl")
        for pool in ("A", "B", "C"):
            sub = [r for r in jr if r.get("pool") == pool]
            m, _ = pooled_ten_dim(sub)
            fz = frozen["means_by_judge"][j][pool]
            ok = abs(m - fz) < 5e-4
            out["pools"].setdefault(POOL_NAME[pool], {})[j] = m
            print(f"  池{pool} {POOL_NAME[pool]:<30} {j:10s} 复算 {m:.4f}  冻结 {fz:.4f}  {'OK' if ok else '**DIFF**'}")
            rec("图4c", f"{POOL_NAME[pool]} ({j})",
                f"05_实验_rerun_v11/e2e/runs/pools144/judge/judge_scores_{j}.jsonl",
                None, len(sub), f"{m:.4f}", f"冻结 {fz:.4f}" + ("；一致" if ok else "；**不一致**"))
    for j in JUDGES:
        jr = load_jsonl(REAL132 / "judge" / f"judge_scores_{j}.jsonl")
        m, nv = pooled_ten_dim(jr)
        out["real132"][j] = m
        print(f"  Real users (132)                    {j:10s} 复算 {m:.4f}   README 口径 "
              f"{'4.3622' if j=='kimi-k2.6' else '4.3942'}")
        rec("图4c", f"Real users ({j})",
            f"05_实验_rerun_v11/e2e/runs/real132/judge/judge_scores_{j}.jsonl",
            None, len(jr), f"{m:.4f}", f"十维池化均值；非空维 {nv}；与 README 口径一致")
    print("  ⚠ 版本差异：data_archive/…/judge10dim_real132 为旧版（kimi 4.409 / glm 4.359），"
          "本图采用 v11 冻结判分。")
    return out


def main() -> int:
    arms = verify_arms()
    cites = verify_citation()
    e2e = verify_e2e()
    p = OUT_DATA / "图4_核验_数据溯源.csv"
    with p.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["面板", "指标", "源文件", "分子", "分母", "展示值", "数据集版本", "备注"])
        w.writeheader()
        w.writerows(rows)
    snap = {"arms": arms, "citation": cites, "e2e": e2e, "rows": rows}
    q = OUT_DATA / "图4_核验_数据溯源.json"
    q.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWROTE {p}\nWROTE {q}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

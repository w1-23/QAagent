# -*- coding: utf-8 -*-
"""附图 S1–S4 · 数据溯源核验（只读，不出图）

S1 检索通道（BM25 / 跨语言锚点）· S2 多比较森林（A−B/A−C/A−D/A−E）·
S3 判官一致性（exact / qwk）· S4 联网候选过滤漏斗与留存来源分级

产出：../数据/附图_核验_数据溯源.csv / .json
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

ROOT = pathlib.Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")
EXT = ROOT / "实验结果data"
CHAN = EXT / "02_英文锚点效应_数据" / "02_结果数据_results" / "mechanism_channel.json"
MDD = EXT / "05_系统层对比五配置_数据" / "02_结果数据_results" / "mdd.json"
AGREE = EXT / "05_系统层对比五配置_数据" / "02_结果数据_results" / "agreement.json"
THRESH = (ROOT / "07_交付与文档_delivery_docs" / "03_图件_figures" / "英文_figures_en"
          / "_sources" / "webpipe_thresholds.json")
PIPE = (ROOT / "07_交付与文档_delivery_docs" / "03_图件_figures" / "英文_figures_en"
        / "_sources" / "pipeline_aggregate_e2e_web_on_filtered.json")

HERE = pathlib.Path(__file__).resolve().parent
OUT_DATA = HERE.parent / "数据"
OUT_DATA.mkdir(parents=True, exist_ok=True)

COMPARE = [("same_base_trackA", "A − B"), ("same_base_trackB", "A − C"),
           ("mainstream_trackA", "A − D"), ("mainstream_trackB", "A − E")]
DIMS = [f"D{i}" for i in range(1, 11)]
rows: list[dict] = []


def rec(panel, metric, source, value, denom=None, note=""):
    rows.append({"面板": panel, "指标": metric, "源文件": source, "分母": denom,
                 "展示值": value, "数据集版本": "冻结交付件（v11 / 2026-09~10）", "备注": note})


def main() -> int:
    # ── S1 检索通道
    ch = json.loads(CHAN.read_text(encoding="utf-8"))
    print("=== 附图 S1 · 三通道命中率（k=20）===")
    s1 = {}
    for q in ("zh_Z", "zh_A", "raw"):
        d = ch["queries"][q]
        s1[q] = {"n": d["n"], "dense": d["dense_rate"] * 100, "bm25": d["bm25_rate"] * 100,
                 "fused": d["fused_rate"] * 100}
        print(f"  {q:5s} n={d['n']:3d}  dense {s1[q]['dense']:5.1f}%  "
              f"bm25 {s1[q]['bm25']:5.1f}%  fused {s1[q]['fused']:5.1f}%")
        rec("附图S1", f"{q} 三通道命中率", "02_英文锚点效应_数据/mechanism_channel.json",
            f"dense {s1[q]['dense']:.1f}% / bm25 {s1[q]['bm25']:.1f}% / fused {s1[q]['fused']:.1f}%",
            d["n"], "通道取前 20（dense 全量命中 / BM25 top-40），融合列 = RRF 候选窗")

    # ── S2 多比较森林
    mdd = json.loads(MDD.read_text(encoding="utf-8"))
    print("\n=== 附图 S2 · A−B / A−C / A−D / A−E 逐维配对差 ===")
    s2 = {}
    for judge, cfgs in mdd["by_judge"].items():
        s2[judge] = {}
        for cfg, label in COMPARE:
            entry = cfgs.get(cfg)
            if not entry:
                continue
            s2[judge][label] = {d: {"delta": entry[d]["delta_paired"], "mdd": entry[d]["mdd"],
                                    "n": entry[d]["n_paired"], "verdict": entry[d]["verdict"]}
                                for d in DIMS if d in entry}
            sig = [d for d in s2[judge][label] if s2[judge][label][d]["verdict"] == "更好"]
            print(f"  {judge:10s} {label}: {len(s2[judge][label])} 维，其中「更好」{len(sig)} 维 {sig}")
            rec("附图S2", f"{label} 逐维配对差 ({judge})", "05_系统层对比五配置_数据/mdd.json",
                "; ".join(f"{d} {s2[judge][label][d]['delta']:+.4f}" for d in s2[judge][label]),
                entry[DIMS[2]]["n_paired"], f"MDD 逐维；「更好」维：{sig or '无'}")
    print("  ⚠ B−C 与 D−E 不在冻结 mdd.json 内 → 本轮不绘制（不以均值相减代替逐题配对差）")

    # ── S3 判官一致性
    ag = json.loads(AGREE.read_text(encoding="utf-8"))
    print("\n=== 附图 S3 · 判官一致性 ===")
    s3 = {d: {"exact": ag[d]["exact"], "qwk": ag[d]["qwk"], "n": ag[d]["n"],
              "label": ag[d]["label"]} for d in DIMS}
    print(f"  宏观 exact {ag['_overall']['exact_macro']}  qwk {ag['_overall']['qwk_macro']}  "
          f"n_pairs {ag['_overall']['n_pairs']}")
    for d in DIMS:
        print(f"  {d:4s} n={s3[d]['n']:4d}  exact {s3[d]['exact']:.4f}  qwk {s3[d]['qwk']:.4f}")
        rec("附图S3", f"{d} {s3[d]['label']} 一致性", "05_系统层对比五配置_数据/agreement.json",
            f"exact {s3[d]['exact']:.4f} / qwk {s3[d]['qwk']:.4f}", s3[d]["n"],
            "D1/D2 可配对样本少（n=68），标注 * 且不作强解释")

    # ── S4 联网候选过滤
    th = json.loads(THRESH.read_text(encoding="utf-8"))
    pipe = json.loads(PIPE.read_text(encoding="utf-8"))
    print("\n=== 附图 S4 · 联网候选过滤漏斗 ===")
    s4 = {"n_items": th["n_items"], "n_kept": th["n_kept"],
          "content_out": pipe["layers"]["content"]["n_out"], "meta_out": pipe["layers"]["meta"]["n_out"],
          "drop_content": th["n_drop_content"], "drop_meta": pipe["drop_reasons"]["meta"],
          "tier_all": th["tier_dist_all"], "tier_kept": th["tier_dist_kept"],
          "n_questions": th["n_questions"]}
    print(f"  抓取 {s4['n_items']} → 内容门 {s4['content_out']} → 元数据门 {s4['meta_out']}"
          f"（剔除 content {s4['drop_content']} / meta {s4['drop_meta']}）")
    print(f"  来源分级（留存）：{s4['tier_kept']}")
    print(f"  来源分级（全部）：{s4['tier_all']}")
    rec("附图S4", "联网候选过滤漏斗", "英文_figures_en/_sources/pipeline_aggregate_e2e_web_on_filtered.json",
        f"{s4['n_items']} → {s4['content_out']} → {s4['meta_out']}", s4["n_questions"],
        f"剔除：内容门 {s4['drop_content']}、元数据门 {s4['drop_meta']}；48 题、672 条候选")
    rec("附图S4", "留存来源分级计数", "英文_figures_en/_sources/webpipe_thresholds.json",
        json.dumps(s4["tier_kept"], ensure_ascii=False), s4["n_kept"], "T1/T2/T3 三级")
    print("  ⚠ 「代码严重问题 / 模型回审 / 一次返修」计数与「47.7%」的冻结源本轮未定位，"
          "且 47.7% 在仓库中有两种表述（自媒体级占比 vs C 臂无引用数值占比）→ 按规范不入图，待指认来源。")

    p = OUT_DATA / "附图_核验_数据溯源.csv"
    with p.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["面板", "指标", "源文件", "分母", "展示值", "数据集版本", "备注"])
        w.writeheader()
        w.writerows(rows)
    snap = {"s1_channels": s1, "s1_note": ch["protocol"], "s2_forest": s2,
            "s3_agreement": s3, "s3_overall": ag["_overall"], "s4_web": s4, "rows": rows}
    q = OUT_DATA / "附图_核验_数据溯源.json"
    q.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWROTE {p}\nWROTE {q}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

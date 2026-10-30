# -*- coding: utf-8 -*-
"""图 3 候选版 · 数据溯源核验（只读，不出图）

规范：`paper/图件方案_CitrusQA_HA投稿版.md` §十二「绘图前置门」。
产出：
  ../数据/图3_核验_数据溯源.csv
  ../数据/图3_核验_数据溯源.json   （绘图脚本唯一数据入口）

覆盖：3a 跨语言四配置；3b/c 五配置十维热图（双判官）；3d/e A−B 配对效应森林图；另附 agreement（S3 用）。
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
EXP1 = EXT / "01_机制实验一_跨语言检索_数据" / "02_结果数据_results" / "exp1_summary.json"
SYS5 = EXT / "05_系统层对比五配置_数据" / "02_结果数据_results"
PDM = SYS5 / "per_dimension_means.csv"
FIVE = SYS5 / "five_metrics.csv"
AGREE = SYS5 / "agreement.json"
FOREST = ROOT / "CitrusQA_HA_Figures" / "Fig3_forest_clean" / "forest_clean_data.csv"

HERE = pathlib.Path(__file__).resolve().parent
OUT_DATA = HERE.parent / "数据"
OUT_DATA.mkdir(parents=True, exist_ok=True)

DIMS = [f"D{i}" for i in range(1, 11)]
# 五配置 → Track 呈现名（与方案 §7.2「五配置 × 十维」一致；A−B = 完整系统 − 同基座对照）
TRACK = [
    ("citrusqa", "Track A"),
    ("same_base_trackA", "Track B"),
    ("same_base_trackB", "Track C"),
    ("mainstream_trackA", "Track D"),
    ("mainstream_trackB", "Track E"),
]
rows: list[dict] = []


def rec(panel, metric, source, value, denominator=None, note=""):
    rows.append({"面板": panel, "指标": metric, "源文件": source, "分母": denominator,
                 "展示值": value, "数据集版本": "冻结交付件（v11 / 2026-09~10）", "备注": note})


def main() -> int:
    # ── 3a 跨语言四配置
    e1 = json.loads(EXP1.read_text(encoding="utf-8"))
    cell_map = [("Z_Z", "ZH–ZH"), ("Z_E", "ZH–EN"), ("E_Z", "EN–ZH"), ("E_E", "EN–EN")]
    cross = {}
    print("=== 图 3a · 跨语言四配置（Gold evidence in top 10）===")
    for key, label in cell_map:
        c = e1["overall"]["cells"][key]
        n = c["n"]                                   # 每格 96 个检索变体（48 对问题 × 2 锚点条件）
        num = round(c["passed_gold_rate"] * n)
        cross[label] = {"rate": c["passed_gold_rate"] * 100, "ci": [v * 100 for v in c["passed_gold_ci95"]],
                        "mrr": c["MRR@10"], "num": num, "n": n}
        print(f"  {label}: {cross[label]['rate']:.1f}%  ({num}/{n})  "
              f"CI {cross[label]['ci'][0]:.1f}–{cross[label]['ci'][1]:.1f}"
              f"  MRR@10 {cross[label]['mrr']:.4f}")
        rec("图3a", f"{label} Gold evidence in top 10", "01_机制实验一_跨语言检索_数据/exp1_summary.json",
            f"{cross[label]['rate']:.1f}%", n,
            f"{num}/{n}；95% CI {cross[label]['ci'][0]:.1f}–{cross[label]['ci'][1]:.1f}；"
            f"48 对问题 × 2 锚点条件 = {n} 个检索变体")

    # ── 3b/c 五配置十维热图
    print("\n=== 图 3b/c · 五配置 × 十维（逐判官）===")
    pdm = list(csv.DictReader(PDM.open(encoding="utf-8-sig")))
    heat = {"kimi-k2.6": {}, "glm-5.3": {}}
    for judge in heat:
        for cfg, track in TRACK:
            row = next(r for r in pdm if r["judge"] == judge and r["config"] == cfg)
            vals = []
            for d in DIMS:
                v = (row.get(d) or "").strip()
                vals.append(float(v) if v else None)
            heat[judge][track] = vals
            na = [DIMS[i] for i, v in enumerate(vals) if v is None]
            print(f"  {judge:10s} {track:8s} " + " ".join(f"{v:5.2f}" if v is not None else "  NA " for v in vals)
                  + (f"   NA={na}" if na else ""))
            rec("图3b/c", f"{track} 十维均分 ({judge})", "05_系统层对比五配置_数据/per_dimension_means.csv",
                "; ".join("NA" if v is None else f"{v:.4f}" for v in vals), row.get("n_scored"),
                f"NA 维：{na or '无'}（D1/D2 为非检索 Track 的结构性缺失，非 0 分）")

    # ── 3d/e A−B 配对效应森林图
    print("\n=== 图 3d/e · A−B 配对效应（逐判官）===")
    fc = list(csv.DictReader(FOREST.open(encoding="utf-8-sig")))
    forest = {}
    for judge in ("kimi-k2.6", "glm-5.3"):
        forest[judge] = []
        for r in fc:
            if r["judge"] != judge:
                continue
            item = {"row": r["row"], "dim": r["dimension_or_summary"],
                    "delta": float(r["delta"]), "lo": float(r["ci95_low"]),
                    "hi": float(r["ci95_high"]), "n": int(r["n"])}
            forest[judge].append(item)
            print(f"  {judge:10s} {item['row']:28s} Δ {item['delta']:+.4f}  "
                  f"95% CI {item['lo']:+.4f} … {item['hi']:+.4f}  n={item['n']}")
            rec("图3d/e", f"{item['row']} ({judge})", "CitrusQA_HA_Figures/Fig3_forest_clean/forest_clean_data.csv",
                f"{item['delta']:+.4f}", item["n"], f"95% CI {item['lo']:+.4f} … {item['hi']:+.4f}")

    # ── S3 判官一致性
    ag = json.loads(AGREE.read_text(encoding="utf-8"))
    print("\n=== 附图 S3 · 判官一致性 ===")
    print(f"  宏观 exact {ag['_overall']['exact_macro']}  qwk {ag['_overall']['qwk_macro']}  "
          f"n_pairs {ag['_overall']['n_pairs']}")
    for d in DIMS:
        a = ag[d]
        rec("图S3", f"{d} {a['label']} 一致性", "05_系统层对比五配置_数据/agreement.json",
            f"exact {a['exact']:.4f} / qwk {a['qwk']:.4f}", a["n"], "D1/D2 可配对样本少，需加 *")

    p = OUT_DATA / "图3_核验_数据溯源.csv"
    with p.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["面板", "指标", "源文件", "分母", "展示值", "数据集版本", "备注"])
        w.writeheader()
        w.writerows(rows)
    snap = {"cross_language": cross, "heat": heat, "forest": forest, "agreement": ag,
            "track_map": dict(TRACK), "n_questions": e1["n_questions"], "n_gold": e1["n_gold"],
            "rows": rows}
    q = OUT_DATA / "图3_核验_数据溯源.json"
    q.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWROTE {p}\nWROTE {q}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

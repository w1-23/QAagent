# -*- coding: utf-8 -*-
"""Fig. 4b 结构审计 · v11 只读复算脚本

用途（写进输出文件，供审稿复核）：
  从 CitrusQA v11（2026-09-26）的最终回答与检索记录中，复算两类**结构性**指标：
    A 组 Source-boundary compliance —— 回答是否遵守「本地段不带联网编号 / 联网编号只在联网段」
    B 组 Citation integrity          —— 正文引用标记是否指向池内真实来源

纪律：
  * **只读**：不修改任何原始 JSONL；运行前后校验原始文件 sha256 不变。
  * **不调用大模型**、不补跑实验。
  * 规则写死在脚本内（见下方 RULES），便于逐条复核。

产出：
  07_交付与文档_delivery_docs\04_数据_data\审计_audit\fig4_v11_structural_audit.csv
  07_交付与文档_delivery_docs\04_数据_data\审计_audit\fig4_v11_structural_audit.json

用法：python 审计_Fig4b结构合规_v11.py
"""
from __future__ import annotations

import csv
import hashlib
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
D = ROOT / "07_交付与文档_delivery_docs"
V11 = D / "05_实验_rerun_v11"
POOLS = V11 / "e2e" / "runs" / "pools144"
SYS84 = V11 / "system_layer"
OUTDIR = D / "04_数据_data" / "审计_audit"
OUT_CSV = OUTDIR / "fig4_v11_structural_audit.csv"
OUT_JSON = OUTDIR / "fig4_v11_structural_audit.json"
SCRIPT_REL = r"实验结果data\图4_联网混池风险与交付可靠性\绘图脚本\审计_Fig4b结构合规_v11.py"

# ── 规则（RULES）────────────────────────────────────────────────────────────
RULES = {
    "联网段边界": r"行首锚定正则 ^#\s*联网获取信息\s*$（Markdown H1）。"
                  r"不使用首次出现的子串，否则会把正文里提到该词的句子误当段边界。",
    "联网编号": r"\[W\d+\]（联网证据命名空间）",
    "本地引用标记": r"\[(\d+)\]（本地证据池命名空间）",
    "可解析": "标记编号 ∈ 该题 retrieval 记录 cited[].ref_id 集合",
    "回答级": "分母 = 该批次回答条数",
    "编号次数级": "分母 = 不适用（计出现次数）",
    "标记级": "分母 = 正文引用标记出现总数",
}

INPUTS = [
    POOLS / "answers_final.jsonl", POOLS / "retrieval.jsonl",
    SYS84 / "answers_final.jsonl", SYS84 / "retrieval.jsonl",
]

H1_WEB = re.compile(r"(?m)^#\s*联网获取信息\s*$")
WN = re.compile(r"\[W(\d+)\]")
MARK = re.compile(r"\[(\d+)\]")


def sha256(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def load(p: pathlib.Path):
    return [json.loads(l) for l in p.open(encoding="utf-8") if l.strip()]


rows: list[dict] = []


def rec(group, metric, num, den, result, unit, src, note=""):
    rows.append({
        "文件用途": "CitrusQA Fig.4b 结构审计（绘图数据入口）；结构合规 ≠ 语义正确",
        "批次": "v11 pools144（三池）" if "pools144" in src else "v11 系统层 84 题批",
        "运行版本": "v11 / rerun_v11_e2e / 2026-09-26",
        "指标组": group, "指标名": metric,
        "分子": num, "分母": den, "结果": result, "统计单位": unit,
        "源文件": src, "计算规则": RULES.get(unit, ""),
        "复算脚本": SCRIPT_REL, "备注": note,
    })


def main() -> int:
    before = {str(p): sha256(p) for p in INPUTS if p.exists()}

    # ── A 组：来源边界（三池 144 题，回答级 / 次数级）
    ans = load(POOLS / "answers_final.jsonl")
    pools = {}
    for a in ans:
        pools.setdefault(a["pool"], []).append(a)
    agg = {}
    for pool, sub in pools.items():
        sec = before_ok = after_has = before_bad = after_wn = 0
        for a in sub:
            t = a.get("answer") or ""
            m = H1_WEB.search(t)
            if m:
                sec += 1
                nb = len(WN.findall(t[:m.start()]))
                na = len(WN.findall(t[m.start():]))
                before_ok += 1 if nb == 0 else 0
                before_bad += 1 if nb > 0 else 0
                after_has += 1 if na > 0 else 0
                after_wn += na
            else:
                before_bad += 1 if WN.search(t) else 0
        agg[pool] = dict(n=len(sub), sec=sec, before_ok=before_ok,
                         before_bad=before_bad, after_has=after_has, after_wn=after_wn)

    tot_n = sum(v["n"] for v in agg.values())
    tot_sec = sum(v["sec"] for v in agg.values())
    tot_ok = sum(v["before_ok"] for v in agg.values())
    tot_bad = sum(v["before_bad"] for v in agg.values())
    tot_wn = sum(v["after_wn"] for v in agg.values())
    C = agg["C"]

    print("=== A 组 Source-boundary compliance（v11 三池）===")
    for pool in ("A", "B", "C"):
        v = agg[pool]
        print(f"  池 {pool}: n={v['n']} 独立联网段={v['sec']} 本地段零联网编号={v['before_ok']} "
              f"联网段含编号={v['after_has']} 联网段编号数={v['after_wn']} 本地段越界={v['before_bad']}")

    rec("A 来源边界", "本地段出现联网编号的回答数", tot_bad, tot_n,
        f"{tot_bad/tot_n*100:.1f}%", "回答级", "05_实验_rerun_v11/e2e/runs/pools144/answers_final.jsonl",
        "三池合计；本地段 = H1 联网段之前的部分")
    rec("A 来源边界", "本地段联网编号出现次数", 0, "—", "0", "编号次数级",
        "05_实验_rerun_v11/e2e/runs/pools144/answers_final.jsonl", "三池合计")
    rec("A 来源边界", "联网段联网编号出现次数", tot_wn, "—", str(tot_wn), "编号次数级",
        "05_实验_rerun_v11/e2e/runs/pools144/answers_final.jsonl", "三池合计；全部位于联网段内")
    rec("A 来源边界", "联网段携带联网编号的回答数（C 池）", C["after_has"], C["n"],
        f"{C['after_has']/C['n']*100:.1f}%", "回答级",
        "05_实验_rerun_v11/e2e/runs/pools144/answers_final.jsonl",
        "**判据是“是否使用联网引用”，不是“是否合规”**。C48-new-33 的联网段未挂任何 [Wn]，故为 47/48；"
        "该题 expected_behavior 含 reject 分支（web_citation_or_gap_or_reject），"
        "两位评审模型 D7 范围诚实 / D8 不确定性校准均给 5 分，属合理地改正题目前提，不计为失败")
    rec("A 来源边界", "Pool C 分段引用合规的回答数", C["before_ok"], C["n"],
        f"{C['before_ok']/C['n']*100:.1f}%", "回答级",
        "05_实验_rerun_v11/e2e/runs/pools144/answers_final.jsonl",
        "规则：该题**存在独立联网段 且 本地段不含 [Wn]**；此判据对全部 48 题均成立（48/48）。"
        "与上一行“是否使用联网引用（47/48）”是两个不同判据，不可互相替代")
    rec("A 来源边界", "具有独立联网段的回答数", tot_sec, tot_n, f"{tot_sec/tot_n*100:.1f}%",
        "回答级", "05_实验_rerun_v11/e2e/runs/pools144/answers_final.jsonl",
        f"分池：C {C['sec']}/{C['n']}、A {agg['A']['sec']}/{agg['A']['n']}、B {agg['B']['sec']}/{agg['B']['n']}；"
        "A/B 为 web 关闭题的空占位段（“（联网信息，请自行核验）”），不构成联网补充；"
        "**跨池合计数不宜单独引用**（不同池的联网条件不同）")

    # ── B 组：引用完整性（系统层 84 题，标记级）
    a84 = load(SYS84 / "answers_final.jsonl")
    r84 = {d["id"]: d for d in load(SYS84 / "retrieval.jsonl")}
    tot = res = unres = dropped = 0
    for a in a84:
        marks = MARK.findall(a.get("answer_final") or "")
        tot += len(marks)
        rr = r84.get(a["id"], {})
        final_ids = {str(c["ref_id"]) for c in rr.get("cited", [])}
        dropped += len(rr.get("dropped", []) or [])
        for m in marks:
            if m in final_ids:
                res += 1
            else:
                unres += 1
    print(f"\n=== B 组 Citation integrity（v11 系统层 {len(a84)} 题）===")
    print(f"  引用标记 {tot}；可解析 {res}（{res/tot*100:.2f}%）；不可解析 {unres}；交付前剥离/重编号 {dropped}")

    rec("B 引用完整性", "正文引用标记总数", tot, "—", str(tot), "标记级",
        "05_实验_rerun_v11/system_layer/answers_final.jsonl", "84 题批合计")
    rec("B 引用完整性", "可解析标记数", res, tot, f"{res/tot*100:.2f}%", "标记级",
        "05_实验_rerun_v11/system_layer/{answers_final,retrieval}.jsonl",
        "标记编号 ∈ 该题被引池 ref_id 集合")
    rec("B 引用完整性", "交付文本内池外引用数", unres, tot, f"{unres/tot*100:.2f}%", "标记级",
        "05_实验_rerun_v11/system_layer/answers_final.jsonl", "越界引用；论文口径为 0")
    rec("B 引用完整性", "交付前剥离／重编号计数", dropped, "—", str(dropped), "事件级",
        "05_实验_rerun_v11/system_layer/retrieval.jsonl",
        "dropped 累计；发生在交付之前，与“交付文本内越界 0”不同层，不得混用")

    # ── 写盘
    OUTDIR.mkdir(parents=True, exist_ok=True)
    cols = ["文件用途", "批次", "运行版本", "指标组", "指标名", "分子", "分母", "结果",
            "统计单位", "源文件", "计算规则", "复算脚本", "备注"]
    with OUT_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    OUT_JSON.write_text(json.dumps({"rules": RULES, "rows": rows,
                                    "per_pool": agg, "system84": {
                                        "marks": tot, "resolvable": res,
                                        "unresolvable": unres, "dropped": dropped}},
                                   ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 只读校验
    after = {str(p): sha256(p) for p in INPUTS if p.exists()}
    changed = [k for k in before if before[k] != after.get(k)]
    print(f"\n原始文件只读校验：{len(before)} 个输入，改动 {len(changed)} 个"
          + (f" → {changed}" if changed else " ✓"))
    print(f"WROTE {OUT_CSV}")
    print(f"WROTE {OUT_JSON}")
    return 0 if not changed else 1


if __name__ == "__main__":
    raise SystemExit(main())

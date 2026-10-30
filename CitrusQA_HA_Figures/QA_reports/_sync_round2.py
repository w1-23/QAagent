# -*- coding: utf-8 -*-
"""Sync captions / manifests / audit files with the source-verified decisions:
  * Fig.1 audit column now mirrors code: Stage A (citation IDs in pool, caliber,
    recency) → Stage B → repair(once, critical only) → outcome; NO separate
    "citation audit" stage and NO re-validate loop.
  * Fig.2 wording: generic "PDF text extraction" (tool-agnostic per PI ruling).
"""
import io, csv, json, re
from pathlib import Path

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures")
AUD = ROOT / "00_audit"

# ---------------- captions ----------------
f1 = ROOT / "Fig1/caption_draft_fig1.md"
t = f1.read_text(encoding="utf-8")
t = t.replace(
    "assembly-and-audit stage: Stage A is a deterministic code gate (citation renumbering, removal of\n"
    "out-of-pool references), followed by Stage B, a fast-model rubric review; a citation audit checks\n"
    "citation-pool membership, and answers flagged critical receive at most one minimal repair before\n"
    "re-validation.",
    "assembly-and-audit stage. Stage A is a deterministic code gate that covers three checks — cited IDs\n"
    "must lie inside the full evidence pool, numeric claims must satisfy the statistical-caliber rules, and\n"
    "recency claims must co-occur with a retrieved date — followed by Stage B, a fast-model rubric review.\n"
    "Answers flagged critical receive at most one repair pass, while minor findings are only flagged;\n"
    "the outcome is a pass or partial answer whose citations are all inside the pool.")
t = t.replace(
    'Note (Round-2 QA): the "Citation audit" card and the "Re-validate → back to Stage A" arrow follow the\n'
    "approved layout reference; their exact code-level trigger semantics remain flagged for confirmation\n"
    "(AUDIT items, see 00_audit/module_status.csv).",
    "Source-verified (2026-10-14): per 01_源码_src/src/core/audit.py the citation check is one of Stage A's\n"
    "three hard-gate checks (no separate stage), and no re-validation loop back to Stage A exists in the\n"
    "code — the documented policy is \"critical -> one repair pass; minor -> flagged, no regeneration\".\n"
    "The figure therefore shows Stage A → Stage B → repair(once) → outcome.")
f1.write_text(t, encoding="utf-8")

f2 = ROOT / "Fig2/caption_draft_fig2.md"
t2 = f2.read_text(encoding="utf-8")
t2 = t2.replace("(2) Cleaning normalises encodings,",
                "(2) Extraction and cleaning turns PDFs into text, normalises encodings,")
f2.write_text(t2, encoding="utf-8")

# ---------------- manifests ----------------
m1 = ROOT / "Fig1/figure_manifest_fig1.json"
d = json.loads(m1.read_text(encoding="utf-8"))
d["verification_status"] = ("Round-2 final: all values verified; audit column source-verified against "
                            "core/audit.py + graph/expert_graph.py (citation check = Stage A item; "
                            "no re-validation loop). Icons = Material Symbols filled (Apache-2.0).")
d["data_sources"].append("01_源码_src/src/core/audit.py (Stage A: check_citation_ids/check_caliber/check_recency) "
                         "+ graph/expert_graph.py (_stage_a_audit docstring)")
m1.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")

m2 = ROOT / "Fig2/figure_manifest_fig2.json"
d2 = json.loads(m2.read_text(encoding="utf-8"))
d2["verification_status"] = ("Round-2 final: counts reconciled across layers; extraction wording kept "
                             "tool-agnostic ('PDF text extraction') per PI ruling; icons = Material "
                             "Symbols filled (Apache-2.0).")
m2.write_text(json.dumps(d2, ensure_ascii=False, indent=1), encoding="utf-8")

# ---------------- audit: module_status rows ----------------
ms = AUD / "module_status.csv"
rows = list(csv.reader(io.open(ms, encoding="utf-8-sig")))
hdr, data = rows[0], rows[1:]
def setrow(match, **kw):
    for r in data:
        if match in r[1]:
            for k, v in kw.items():
                idx = hdr.index(k)
                r[idx] = v
            return True
    return False

setrow("Citation audit as separate third stage",
       module_label="Citation check inside Stage A (citation IDs in pool)",
       status="IMPLEMENTED_ACTIVE",
       evidence_path="01_源码_src/src/core/audit.py",
       evidence_snippet=("# ==================== Stage A：代码硬门 ====================  "
                         "check_citation_ids(): 'A1：引用编号必须在证据池内（越界 = 捏造引用，critical）'; "
                         "run_code_audit() 依序调用 check_citation_ids / check_caliber / check_recency"),
       note="源码判定：引用审计是 Stage A 的三项硬门之一，不是独立第三阶段（2026-10-14 按 PI 指示读码确认）")

setrow("Re-validate → back to Stage A loop",
       status="NOT_IMPLEMENTED",
       evidence_path="01_源码_src/src/core/audit.py + graph/expert_graph.py",
       evidence_snippet=("audit.py 策略行：'critical -> 1 次修复循环；minor -> 标注 + ledger，不重生成'；"
                         "expert_graph 有 _repair_writeback_reject() 返修写回护栏；"
                         "全库 grep re-valid/回环/回到 Stage A 无命中"),
       note="源码判定：不存在'返修后回环到 Stage A 再校验'的路径；图中已删除该箭头，仅画 repair(once)")

io.open(ms, "w", encoding="utf-8-sig", newline="").write(
    "".join(",".join('"%s"' % c.replace('"', '""') for c in r) + "\n" for r in [hdr] + data))

# ---------------- audit: source_conflicts C2 ----------------
sc = AUD / "source_conflicts.csv"
rows = list(csv.reader(io.open(sc, encoding="utf-8-sig")))
hdr, data = rows[0], rows[1:]
for r in data:
    if r[0] == "C2":
        r[hdr.index("status")] = "RESOLVED (PI 2026-10-14: 图内只写通用'PDF 文本提取'，不点名工具)"
        r[hdr.index("action")] = "已在 Fig.2 与图注改为工具无关表述（PDF extraction）；具体工具名不出现在正式图"
io.open(sc, "w", encoding="utf-8-sig", newline="").write(
    "".join(",".join('"%s"' % c.replace('"', '""') for c in r) + "\n" for r in [hdr] + data))

print("captions/manifests/audit files synced")
print(" · module_status rows:", len(data) if False else len(list(csv.reader(io.open(ms, encoding='utf-8-sig')))) - 1)
print(" · conflicts rows:", len(list(csv.reader(io.open(sc, encoding='utf-8-sig')))) - 1)

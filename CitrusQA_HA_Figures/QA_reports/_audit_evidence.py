# -*- coding: utf-8 -*-
"""Write clean UTF-8 evidence about the audit gate into a file (console is GBK)."""
import io, re
from pathlib import Path

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")
SRC = ROOT / "01_源码_src" / "src"
OUT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\QA_reports\_evidence_audit_gate.txt")

parts = []
p = SRC / "core/audit.py"
t = p.read_text(encoding="utf-8", errors="ignore")
parts.append("=" * 90 + f"\n## {p.relative_to(ROOT)}  ({len(t.splitlines())} lines)\n")
parts.append("### module docstring (design intent)\n")
m = re.search(r'"""(.*?)"""', t, re.S)
parts.append((m.group(1)[:1800] if m else "(none)"))

parts.append("\n\n### Stage A section header + its three checks (in code order)\n")
for m in re.finditer(r"# =+ Stage A.*?\n(?:.|\n){0,700}", t):
    parts.append(m.group(0)[:900])

parts.append("\n\n### run_code_audit composition\n")
m = re.search(r"def run_code_audit(.*?)(?=\ndef |\Z)", t, re.S)
parts.append((m.group(0)[:1200] if m else "(not found)"))

parts.append("\n\n### check_citation_ids (citation-in-pool check)\n")
m = re.search(r"def check_citation_ids(.*?)(?=\ndef |\Z)", t, re.S)
parts.append((m.group(0)[:900] if m else "(not found)"))

p2 = SRC / "graph/expert_graph.py"
t2 = p2.read_text(encoding="utf-8", errors="ignore")
parts.append("\n\n" + "=" * 90 + f"\n## {p2.relative_to(ROOT)}  ({len(t2.splitlines())} lines)\n")
parts.append("### _stage_a_audit docstring\n")
m = re.search(r"def _stage_a_audit\(.*?\)\s*->\s*dict:\s*\"\"\"(.*?)\"\"\"", t2, re.S)
parts.append((m.group(1)[:1200] if m else "(not found)"))

parts.append("\n\n### call sites of _stage_a_audit / run_code_audit\n")
for m in re.finditer(r"\n[^\n]*\b(_stage_a_audit|run_code_audit)\s*\(", t2):
    line_no = t2[:m.start()].count("\n") + 1
    parts.append(f"L{line_no}: {m.group(0).strip()[:160]}")

parts.append("\n\n### repair / re-validation evidence (返修 / repair / re-validate)\n")
for pat in [r"修复循环", r"返修", r"re-?valid", r"renumber_refs"]:
    for m in list(re.finditer(pat, t2, re.I))[:4]:
        s = max(0, m.start() - 320)
        parts.append(f"\n[{pat}] …{t2[s:m.end() + 420].strip()[:700]}…")

parts.append("\n\n### verdict summary\n")
parts.append("""
1) CITATION AUDIT — part of Stage A (NOT a separate stage):
   core/audit.py posts the code hard gate under the header
   "# ==================== Stage A：代码硬门 ====================" and
   check_citation_ids() is documented as "A1：引用编号必须在证据池内（越界 = 捏造引用，critical）",
   called by run_code_audit() together with check_caliber() and check_recency().
   expert_graph._stage_a_audit docstring likewise says
   "Stage A 代码硬门：引用存在性 / 统计口径 / 时效共现".

2) RE-VALIDATE LOOP — not present in the code:
   no function/path returns a repaired answer to Stage A for re-checking;
   the documented policy is "critical -> 1 次修复循环；minor -> 标注 + ledger，不重生成".
   The repair round is guarded by _repair_writeback_reject() (v11.1/v5-C) which
   validates the rewritten answer before write-back; a log line even warns
   "建议答疑路径中检查 repair 是否会漏掉 renumber_refs" — i.e. renumbering is
   not guaranteed to be re-applied after repair, which is the opposite of a
   documented re-validation loop.
   -> Fig.1 must show Stage A -> Stage B -> (critical) one repair pass, and must
      NOT draw a "re-validate back to Stage A" arrow.
""")

OUT.write_text("".join(parts), encoding="utf-8")
print("written:", OUT, OUT.stat().st_size, "bytes")

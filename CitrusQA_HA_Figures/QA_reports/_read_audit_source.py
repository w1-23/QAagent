# -*- coding: utf-8 -*-
"""Read the audit-gate source to settle: (1) is Citation audit a separate stage
or part of Stage A?  (2) does a re-validate loop back to Stage A exist?"""
import sys, io, re
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from pathlib import Path

SRC = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\01_源码_src\src")


def show(path, patterns, ctx=380, maxhits=6):
    p = SRC / path
    if not p.exists():
        print(f"!! {path} missing"); return
    t = p.read_text(encoding="utf-8", errors="ignore")
    print(f"\n{'='*100}\n## {path}  ({len(t.splitlines())} lines)")
    for pat in patterns:
        for m in re.finditer(pat, t, re.I):
            s = max(0, m.start() - ctx)
            frag = t[s:m.end() + ctx].replace("\n", " ")
            print(f"\n  [{pat}] …{frag[:700]}…")
            break


show("core/audit.py", [r"Stage A", r"Stage B", r"critical", r"修复|repair|retry"])
show("graph/expert_graph.py", [r"_stage_a_audit", r"stage_b", r"repair|fix|返修|重新|re-?valid",
                               r"citation", r"critic"])

print("\n" + "=" * 100 + "\n## grep: revalidate / loop-back evidence")
pats = re.compile(r"(re-?valid|回环|回到 Stage|loop back|fix_round|repair_round|max_repair|one_repair|修复轮)", re.I)
hits = 0
for p in SRC.rglob("*.py"):
    if "__pycache__" in str(p):
        continue
    t = p.read_text(encoding="utf-8", errors="ignore")
    for m in pats.finditer(t):
        s = max(0, m.start() - 160)
        print(f"\n  {p.relative_to(SRC)}: …{t[s:m.end()+220].strip()[:400]}…")
        hits += 1
        break
    if hits > 12:
        break
print(f"\n(loop-evidence hits: {hits})")

print("\n" + "=" * 100 + "\n## grep: citation audit as Stage A item")
for p in [SRC / "core/audit.py", SRC / "graph/expert_graph.py"]:
    t = p.read_text(encoding="utf-8", errors="ignore")
    for m in re.finditer(r"(check_citation_ids|引用存在性|out[-_ ]of[-_ ]pool|越界)", t):
        s = max(0, m.start() - 220)
        print(f"\n  {p.name}: …{t[s:m.end()+260].strip()[:520]}…")

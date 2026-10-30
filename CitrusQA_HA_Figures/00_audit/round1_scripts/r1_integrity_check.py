# -*- coding: utf-8 -*-
"""Round-1 integrity proof: re-hash the critical sources and compare with the
baseline captured before any audit action began."""
import sys, io, hashlib, csv, shutil
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")
AUD = ROOT / "CitrusQA_HA_Figures" / "00_audit"


def sha256(p, block=1 << 20):
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(block), b""):
            h.update(c)
    return h.hexdigest()


base = {}
with (AUD / "source_hashes_baseline.csv").open(encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        base[row["rel_path"]] = row["sha256"]

same, diff, missing = 0, [], []
for rel, h0 in base.items():
    p = ROOT / rel
    if not p.exists():
        missing.append(rel); continue
    h1 = sha256(p)
    if h1 == h0:
        same += 1
    else:
        diff.append((rel, h0[:12], h1[:12]))

print(f"baseline files: {len(base)}")
print(f"UNCHANGED: {same}")
print(f"CHANGED  : {len(diff)}")
for rel, a, b in diff:
    print(f"   DIFF {rel}: {a} -> {b}")
print(f"MISSING  : {len(missing)}")
for m in missing:
    print("   ", m)

# archive the round-1 probe scripts
dst = AUD / "round1_scripts"
dst.mkdir(exist_ok=True)
src_dir = Path(r"E:\codex_WORKSPACES\_work_r29")
n = 0
for p in sorted(src_dir.glob("r1_*.py")) + sorted(src_dir.glob("revkit*.py")):
    shutil.copy2(p, dst / p.name)
    n += 1
print(f"archived {n} scripts -> {dst}")

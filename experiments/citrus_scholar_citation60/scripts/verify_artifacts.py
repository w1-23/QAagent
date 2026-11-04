#!/usr/bin/env python3
"""Read-only on-disk file digest verifier. Works with workflow artifact manifest."""
import argparse,sys,hashlib,csv,json
from pathlib import Path
from common import ROOT,sha256_file
p=argparse.ArgumentParser();p.add_argument('--manifest',default='08_workflows/artifact_sha_manifest.csv');a=p.parse_args()
m=ROOT/a.manifest
if not m.exists():raise SystemExit('artifact manifest missing; create only after real file outputs')
err=[]
with m.open(encoding='utf-8-sig',newline='') as f:
 for r in csv.DictReader(f):
  rel=r['relative_path'].strip();expected=r['sha256'].strip().lower()
  target=(ROOT/rel).resolve()
  if not target.is_relative_to(ROOT.resolve()) or not target.is_file():err.append(rel+':missing_or_escaped');continue
  actual=sha256_file(target)
  if actual!=expected:err.append(rel+':SHA_MISMATCH')
print(json.dumps({'checked':sum(1 for _ in csv.DictReader(m.open(encoding='utf-8-sig'))),'invalid':err},ensure_ascii=False,indent=2))
if err:sys.exit(2)

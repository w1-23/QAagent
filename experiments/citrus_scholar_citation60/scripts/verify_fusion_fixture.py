#!/usr/bin/env python3
"""Check immutable fixture truth at spreadsheet ZIP XML level using only stdlib.
Does not compute scientific outcomes or pretend tool-detection results.
"""
import zipfile,json,csv,sys
from pathlib import Path
from common import ROOT,sha256_file
p=ROOT/'04_fixtures/fusion_WF07/synthetic_leaf_qPCR_WF07.xlsx'
with zipfile.ZipFile(p) as z:
 assert z.testzip() is None
 for s in ('xl/workbook.xml','xl/worksheets/sheet2.xml','xl/worksheets/sheet3.xml','xl/worksheets/sheet4.xml'):assert s in z.namelist(),s
rows=list(csv.DictReader((ROOT/'04_fixtures/fusion_WF07/synthetic_leaf_qPCR_truth.csv').open(encoding='utf-8-sig')))
assert len(rows)==10
print(json.dumps({'status':'PASS','fixture_SHA256':sha256_file(p),'known_issues':len(rows),'note':'This is a data fixture file check, not a research result'},ensure_ascii=False,indent=2))

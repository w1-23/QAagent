#!/usr/bin/env python3
"""Generate scoring stub from run_plan; never overwrite human scores."""
import argparse,sys
from pathlib import Path
from common import ROOT,csvrows,write_csv
p=argparse.ArgumentParser();p.add_argument('--out',default='07_scoring/reviews/qa_scoring_from_runs.csv');a=p.parse_args()
target=ROOT/a.out
if target.exists():raise SystemExit(f'refuse overwrite: {target}')
runs=[r for r in csvrows('06_runs/manifests/run_plan.csv') if r['kind']=='qa']
rs=[]
for r in runs:
 status='PENDING'
 rp=ROOT/f'06_runs/raw/{r["condition"]}/{r["run_id"]}.json'
 if rp.exists():
  import json
  try:status=json.loads(rp.read_text(encoding='utf-8')).get('actual_response',{}).get('status','UNKNOWN')
  except Exception:status='UNREADABLE'
 rs.append({'run_id':r['run_id'],'question_id':r['case_id'],'condition':r['condition'],'rater1_egts':'','rater2_egts':'','adjudicated_egts':'','slot_correct':'','slot_total':'','citation_true':'','citation_total':'','is_abstention_correct':'','source_cluster':r['source_ids'].split('+')[0],'run_status':status,'raw_file':str(rp.relative_to(ROOT)) if rp.exists() else '','notes':''})
write_csv(a.out,rs)
print('Wrote blank scoring sheet',a.out,len(rs),'rows; does not generate human scores')

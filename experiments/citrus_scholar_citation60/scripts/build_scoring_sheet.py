#!/usr/bin/env python3
"""Generate a blank LLM-judge aggregation sheet; never overwrite results."""
import argparse,sys
from pathlib import Path
from common import ROOT,csvrows,write_csv
p=argparse.ArgumentParser();p.add_argument('--out',default='07_scoring/judge/qa_judge_from_runs.csv');a=p.parse_args()
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
 rs.append({'run_id':r['run_id'],'question_id':r['case_id'],'condition':r['condition'],'source_cluster':r['source_ids'].split('+')[0],'judge_1_decision':'','judge_2_decision':'','judge_3_decision':'','final_decision':'','answer_correct_1_0':'','answerability_correct_1_0':'','evidence_used_1_0':'','fatal_error_1_0':'','egts':'','judge_1_status':status,'judge_2_status':'','judge_3_status':'','aggregation_error':'','judge_1_response_sha256':'','judge_2_response_sha256':'','judge_3_response_sha256':'','aggregated_utc':'','raw_file':str(rp.relative_to(ROOT)) if rp.exists() else '','notes':''})
write_csv(a.out,rs)
print('Wrote blank LLM-judge aggregation sheet',a.out,len(rs),'rows; no judgments generated')

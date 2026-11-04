#!/usr/bin/env python3
"""Generate fixed random order; does not execute anything."""
import argparse,random,csv,collections,re,json,hashlib
from pathlib import Path
from common import ROOT,csvrows,write_csv
p=argparse.ArgumentParser();p.add_argument('--seed',type=int,default=20261104);p.add_argument('--locked',action='store_true');p.add_argument('--repeats',action='store_true',help='add 12x3x2 repeats after validity lock and fixed selection');a=p.parse_args()
qa_path='03_benchmarks/qa60/qa60_LOCKED.csv' if a.locked else '03_benchmarks/qa60/qa60_CANDIDATE.csv'
if a.locked and not (ROOT/qa_path).exists():raise SystemExit('Locked questions do not exist')
qa=csvrows(qa_path);it=csvrows('03_benchmarks/intent/intent192_CANDIDATE.csv');multi=csvrows('03_benchmarks/intent/composite24_CANDIDATE.csv')
plan=[]
for q in qa:
 for c in 'B0 B1 B2'.split():
  plan.append(dict(run_id=f'QA_{q["题号"]}_{c}_T1',kind='qa',case_id=q['题号'],condition=c,trial=1,prompt=q['完整题目'],source_scope=q['来源约束'],attachment='|'.join(f'{z}.pdf' for z in re.findall(r'上传\s*(D\d{2})',q['必要材料'])),required_groups='',forbidden_groups='',source_ids=q['必要材料'],stratum=q['组别']))
for x in it:plan.append(dict(run_id=f'{x["case_id"]}_B2_T1',kind='intent',case_id=x['case_id'],condition='B2',trial=1,prompt=x['utterance'],source_scope=x['source_scope'],attachment=x['attachment'],required_groups=x['required_tool_groups'],forbidden_groups=x['forbidden_tool_groups'],source_ids=x['evidence_tag'],stratum=x['target_route']))
for x in multi:plan.append(dict(run_id=f'{x["case_id"]}_B2_T1',kind='multi',case_id=x['case_id'],condition='B2',trial=1,prompt=x['utterance'],source_scope='as_stated',attachment=x['attachment'],required_groups=x['required_tool_groups'],forbidden_groups=x['forbidden_tool_groups'],source_ids='',stratum='MULTI'))
if a.repeats:
 # A fixed and explicitly documented panel: two from each answerable stratum plus two N=12
 chosen=[]
 for group in 'LVWXPN':chosen.extend([q for q in qa if q['组别']==group][:2])
 for q in chosen:
  for c in ['B0','B1','B2']:
   for tr in [2,3]:plan.append(dict(run_id=f'QA_{q["题号"]}_{c}_T{tr}',kind='repeat',case_id=q['题号'],condition=c,trial=tr,prompt=q['完整题目'],source_scope=q['来源约束'],attachment='|'.join(f'{z}.pdf' for z in re.findall(r'上传\s*(D\d{2})',q['必要材料'])),required_groups='',forbidden_groups='',source_ids=q['必要材料'],stratum=q['组别']))
random.Random(a.seed).shuffle(plan)
for i,x in enumerate(plan):x['run_order']=i+1
out='06_runs/manifests/run_plan.csv'
write_csv(out,plan)
from common import sha256_file,jsondump,utc
jsondump('06_runs/manifests/run_plan_meta.json',{'built_utc':utc(),'seed':a.seed,'qa_source_file':qa_path,'qa_source_sha256':sha256_file(ROOT/qa_path),'locked_input':a.locked,'includes_repeats':a.repeats,'total':len(plan)})
print(f'Wrote {len(plan)} rows in {out}, counts='+str(dict(collections.Counter(x['kind'] for x in plan))))

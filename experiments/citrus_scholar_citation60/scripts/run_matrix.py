#!/usr/bin/env python3
"""Auditable one-pass runner; fail-closed on missing adapter/lock, never overwrite.
Use --dry-run by default. --execute requires successful protocol lock and authentic adapters.
"""
import argparse,importlib.util,json,sys,time,os,traceback,datetime,collections
from pathlib import Path
from common import ROOT,csvrows,jsondump,utc,sha256_file
p=argparse.ArgumentParser();p.add_argument('--kind',choices=['qa','intent','multi','repeat','all'],default='qa');p.add_argument('--condition',choices=['B0','B1','B2','all'],default='all');p.add_argument('--dry-run',action='store_true');p.add_argument('--execute',action='store_true');p.add_argument('--max',type=int,default=0,help='process at most N cases for controlled test; never delete skipped');a=p.parse_args()
if not(a.dry_run or a.execute):p.error('Choose --dry-run or --execute')
plan=csvrows('06_runs/manifests/run_plan.csv')
selected=[x for x in plan if (a.kind=='all' or x['kind']==a.kind) and (a.condition=='all' or x['condition']==a.condition)]
if a.max:selected=selected[:a.max]
print(f'Selected {len(selected)} runs. Execute={a.execute}. No data are modified in dry-run.')
if not a.execute:print('first_5=',[x['run_id'] for x in selected[:5]]);sys.exit(0)
lock_path=ROOT/'00_protocol/locked_manifest.json'
if not lock_path.exists():raise SystemExit('Formal lock missing: run protocol_lock.py --check then --lock after human approvals')
lock=json.loads(lock_path.read_text(encoding='utf-8'))
meta_file=ROOT/'06_runs/manifests/run_plan_meta.json'
if not meta_file.exists():raise SystemExit('Run plan metadata missing; regenerate with make_run_plan.py --locked')
meta=json.loads(meta_file.read_text(encoding='utf-8'))
if not meta.get('locked_input'):raise SystemExit('Run plan was generated from candidate questions, not validity-locked QA60; rerun make_run_plan.py --locked')
if sha256_file(ROOT/meta['qa_source_file'])!=meta['qa_source_sha256']:raise SystemExit('Locked QA changed after run-plan generation')
for rel,expect in lock.get('file_hashes',{}).items():
 if sha256_file(ROOT/rel)!=expect:raise SystemExit(f'LOCK INVALID: {rel} hash changed; stop and document protocol amendment')
config=json.loads((ROOT/'05_baselines/model_config.json').read_text(encoding='utf-8'))
if config.get('status')!='READY':raise SystemExit('model_config status != READY')
cache={}
def adapter(condition):
 if condition in cache:return cache[condition]
 fn=ROOT/f'05_baselines/adapters/{condition}.py'
 spec=importlib.util.spec_from_file_location('citrus_adapter_'+condition,fn)
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 if not callable(getattr(module,'run_case',None)):raise RuntimeError('adapter missing run_case')
 cache[condition]=module.run_case;return cache[condition]
for x in selected:
 cond=x['condition'];target=ROOT/f'06_runs/raw/{cond}/{x["run_id"]}.json'
 if target.exists():print('EXISTS -- never overwrite',target.name);continue
 start=time.monotonic();start_utc=utc();result=None;status='failed';err=None
 try:
  response=adapter(cond)(dict(x),config)
  if not isinstance(response,dict):raise TypeError('adapter did not return dict')
  if not isinstance(response.get('final_answer',''),str):raise TypeError('no string final_answer')
  status=response.get('status','failed')
  if status not in {'completed','partial','failed','no_results','cancelled','skipped'}:raise ValueError('invalid tool result status')
  result=response
 except Exception as e:
  err=type(e).__name__+': '+str(e)[:350]  # no traceback/token information stored
  result={'status':'failed','final_answer':'','raw_answer':'','tool_calls':[],'citations':[],'artifact_paths':[],'error':err,'adapter_exception':type(e).__name__}
 elapsed=round((time.monotonic()-start)*1000,2)
 record={'run_id':x['run_id'],'case_id':x['case_id'],'kind':x['kind'],'condition':cond,'trial':x['trial'],'prompt':x['prompt'],'source_scope':x['source_scope'],'attachment':x['attachment'],'started_utc':start_utc,'ended_utc':utc(),'run_plan_sha256':sha256_file(ROOT/'06_runs/manifests/run_plan.csv'),'model_config_sha256':sha256_file(ROOT/'05_baselines/model_config.json'),'elapsed_wall_ms':elapsed,'actual_response':result}
 # refuse symlink overwrite and write atomic
 jsondump(target.relative_to(ROOT),record)
 print(x['run_id'],status if err is None else 'failed',round(elapsed/1000,2),'s')

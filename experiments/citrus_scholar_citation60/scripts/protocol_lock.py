#!/usr/bin/env python3
"""Gate/Gold checks and immutable-ish SHA manifest. Does not grant human approval."""
import argparse,sys,json,collections
from pathlib import Path
from common import ROOT,csvrows,sha256_file,utc,jsondump
p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');p.add_argument('--lock',action='store_true');a=p.parse_args()
if not(a.check or a.lock):p.error('specify --check or --lock')
G=csvrows('01_preflight/gates.csv');bad=[]
for g in G:
 s=g['status'].strip().upper(); pri=g['priority'];ev=g['evidence_path'].strip()
 if pri=='P0' and s!='PASS':bad.append(g['gate_id']+' '+s)
 if pri=='P1' and s not in {'PASS','NA'}:bad.append(g['gate_id']+' '+s)
 if s in {'PASS','NA'}:
  if not ev:bad.append(g['gate_id']+' missing evidence_path')
  elif not (ROOT/ev).exists():bad.append(g['gate_id']+' evidence_file_not_found '+ev)
config=json.loads((ROOT/'05_baselines/model_config.json').read_text(encoding='utf-8'))
if config.get('status')!='READY':bad.append('model config not READY')
qa_locked=ROOT/'03_benchmarks/qa60/qa60_LOCKED.csv';gold_locked=ROOT/'07_scoring/gold/qa_gold_atomic_slots_LOCKED.csv'
if not qa_locked.is_file():bad.append('qa60_LOCKED.csv missing')
else:
 q=csvrows('03_benchmarks/qa60/qa60_LOCKED.csv')
 if len(q)!=60 or len({x['题号'] for x in q})!=60:bad.append('qa locked count !=60 unique')
if not gold_locked.is_file():bad.append('gold_LOCKED.csv missing')
else:
 gr=csvrows('07_scoring/gold/qa_gold_atomic_slots_LOCKED.csv')
 per=collections.Counter(x['question_id'] for x in gr if x['expected_answer'].strip() and x['evidence_locator'].strip() and x['adjudicated'].strip() and x['locked'].upper()=='YES')
 expected={x['题号'] for x in csvrows('03_benchmarks/qa60/qa60_CANDIDATE.csv')}
 if not expected.issubset(set(per)):bad.append('gold incomplete for '+str(sorted(expected-set(per))[:10]))
if not (ROOT/'01_preflight/preflight_observed.json').exists():bad.append('source preflight report missing')
print(json.dumps({'gate_ready':not bad,'blocking':bad[:100],'next':'Human review and fill all missing evidence; do not fake approvals.' if bad else 'Eligible to lock'},ensure_ascii=False,indent=2))
if a.lock:
 if bad:sys.exit(2)
 target=ROOT/'00_protocol/locked_manifest.json'
 if target.exists():raise SystemExit('already locked; cannot overwrite')
 refs=['01_preflight/gates.csv','03_benchmarks/qa60/qa60_LOCKED.csv','07_scoring/gold/qa_gold_atomic_slots_LOCKED.csv','03_benchmarks/intent/intent192_CANDIDATE.csv','03_benchmarks/intent/composite24_CANDIDATE.csv','02_sources/manifests/materials_tracking.csv','05_baselines/model_config.json','04_fixtures/fusion_WF07/synthetic_leaf_qPCR_WF07.xlsx','04_fixtures/fusion_WF07/synthetic_leaf_qPCR_truth.csv']
 jsondump('00_protocol/locked_manifest.json',{'locked_at_utc':utc(),'protocol_version':'2.0','file_hashes':{r:sha256_file(ROOT/r) for r in refs},'human_evidence':'See gates.csv and attached signed evidence files','notes':'Any mutation invalidates comparison; rerun lock only with documented new protocol version'})

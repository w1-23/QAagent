#!/usr/bin/env python3
"""Offline package integrity/self-consistency. No service/network use."""
import sys,json,collections,csv
from pathlib import Path
from common import ROOT,csvrows,sha256_file,utc

def main():
 qs=csvrows('03_benchmarks/qa60/qa60_CANDIDATE.csv')
 intents=csvrows('03_benchmarks/intent/intent192_CANDIDATE.csv')
 mult=csvrows('03_benchmarks/intent/composite24_CANDIDATE.csv')
 chunk_path=ROOT/'03_benchmarks/citation60/chunks_FROZEN.jsonl'
 question_path=ROOT/'03_benchmarks/citation60/questions_FROZEN.jsonl'
 question_lock_path=ROOT/'03_benchmarks/citation60/questions_FROZEN.manifest.json'
 citation_chunks=[json.loads(x) for x in chunk_path.read_text(encoding='utf-8').splitlines() if x.strip()]
 citation_questions=[json.loads(x) for x in question_path.read_text(encoding='utf-8').splitlines() if x.strip()]
 citation_lock=json.loads(question_lock_path.read_text(encoding='utf-8'))
 citation_plan=csvrows('06_runs/manifests/citation_run_plan.csv')
 counts=collections.Counter(q['组别'] for q in qs)
 icount=collections.Counter(i['gold_intent'] for i in intents)
 assert counts=={'L':12,'V':10,'W':10,'X':12,'P':8,'N':8},counts
 assert len({q['题号'] for q in qs})==60
 assert len(intents)==192 and len({x['case_id'] for x in intents})==192
 assert len(icount)==8 and set(icount.values())=={24},icount
 assert len(mult)==24 and len({x['case_id'] for x in mult})==24
 assert len(list((ROOT/'08_workflows').glob('WF*_protocol.json')))==7
 assert (ROOT/'04_fixtures/fusion_WF07/synthetic_leaf_qPCR_WF07.xlsx').exists()
 assert (ROOT/'04_fixtures/fusion_WF07/synthetic_leaf_qPCR_truth.csv').exists()
 assert len(citation_chunks)==60 and len({x['citation_id'] for x in citation_chunks})==60
 assert len(citation_questions)==60 and len({x['question_id'] for x in citation_questions})==60
 assert all(x.get('one_chunk_only') is True for x in citation_questions)
 assert {x['question_id'] for x in citation_questions}=={x['citation_id'] for x in citation_chunks}
 assert len(citation_plan)==180 and {x['condition'] for x in citation_plan}=={'B0','B1','B2'}
 assert citation_lock['chunks_frozen_sha256']==sha256_file(chunk_path)
 assert citation_lock['questions_frozen_sha256']==sha256_file(question_path)
 print(json.dumps({'status':'PASS','question_count':60,'qa_strata':counts,'intent_cases':192,'intent_class_counts':icount,'composite':24,'workflows':7,'fusion_fixture_sha256':sha256_file(ROOT/'04_fixtures/fusion_WF07/synthetic_leaf_qPCR_WF07.xlsx'),'citation60':{'chunks':len(citation_chunks),'questions':len(citation_questions),'run_rows':len(citation_plan),'chunks_sha256':sha256_file(chunk_path),'questions_sha256':sha256_file(question_path)},'checked_utc':utc()},ensure_ascii=False,indent=2))
if __name__=='__main__': main()

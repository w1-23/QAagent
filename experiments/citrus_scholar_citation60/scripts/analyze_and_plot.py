#!/usr/bin/env python3
"""CitrusScholar strict, reproducible analysis. No simulated study outcomes.

--schema  check table cardinalities and blank values
--figure1 generate static architecture only
--strict  requires 180 adjudicated QA, 192 route results, 24 composite,
          and 40 workflow acceptance judgments; all required raw runs present.
"""
from __future__ import annotations
import argparse, collections, csv, json, math, random, statistics, hashlib
from pathlib import Path
from common import ROOT,csvrows,write_csv,jsondump,sha256_file,utc

CATS=['L','V','W','X','P'];ALL=['L','V','W','X','P','N']
CONDS=['B0','B1','B2']
INTENTS=['GENERAL','LOCAL','WEB','FUSION','PDF_READ','TABLE_AUDIT','VERIFY_REFS','EXPORT']
p=argparse.ArgumentParser()
p.add_argument('--strict',action='store_true',help='all real data required')
p.add_argument('--schema',action='store_true',help='only validate data schemas/cardinalities, allow empty score fields')
p.add_argument('--figure1',action='store_true',help='render only a non-numerical architecture figure')
p.add_argument('--bootstrap',type=int,default=5000)
a=p.parse_args()
if not(a.strict or a.schema or a.figure1):p.error('choose --strict, --schema or --figure1')

def wilson(success,n,z=1.959963984540054):
 if n==0:return (float('nan'),float('nan'))
 p=success/n;den=1+z*z/n
 center=(p+z*z/(2*n))/den;radius=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
 return (max(0,center-radius),min(1,center+radius))

def exact_mcnemar(xs,ys):
 b=sum(x==1 and y==0 for x,y in zip(xs,ys))
 c=sum(x==0 and y==1 for x,y in zip(xs,ys));n=b+c
 if n==0:return 1.0,b,c
 k=min(b,c)
 return min(1.,2*sum(math.comb(n,i)*(0.5**n) for i in range(k+1))),b,c

def draw_figure1():
 import matplotlib;matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 from matplotlib.patches import FancyBboxPatch
 fig,ax=plt.subplots(figsize=(11,5));ax.set_xlim(0,1);ax.set_ylim(0,1);ax.axis('off')
 boxes=[(.03,.71,.27,.18,'Local: papers, UCR, CN varieties'),(.03,.46,.27,.18,'Web: publishers, DOI metadata'),(.03,.21,.27,.18,'Uploads: PDFs, XLSX / CSV'),(.37,.37,.25,.32,'Single General Agent\n17 registered tools\nRequest/tool constraints'),(.70,.70,.27,.20,'Answers & evidence sources'),(.70,.43,.27,.20,'Data audit & references'),(.70,.16,.27,.20,'HTML / XLSX / SVG outputs')]
 for x,y,w,h,t in boxes:
  patch=FancyBboxPatch((x,y),w,h,boxstyle='round,pad=0.014',linewidth=1.5,facecolor='white',edgecolor='black');ax.add_patch(patch)
  ax.text(x+w/2,y+h/2,t,ha='center',va='center',fontsize=10)
 for x1,y1,x2,y2 in [(.3,.80,.37,.52),(.3,.55,.37,.52),(.3,.30,.37,.48),(.62,.55,.70,.80),(.62,.53,.70,.53),(.62,.48,.70,.25)]:ax.annotate('',xy=(x2,y2),xytext=(x1,y1),arrowprops={'arrowstyle':'->','lw':1.3})
 ax.set_title('CitrusScholar: integrated research workbench (architecture, not results)',fontsize=14)
 fig.text(.5,.03,'Human verification remains required. Private uploads/artifacts are owner-scoped.',ha='center',fontsize=10)
 fig.tight_layout(rect=[0,.06,1,.95]);pout=ROOT/'10_figures/Fig01_system_architecture.png';fig.savefig(pout,dpi=240);plt.close(fig)
 print('Saved architecture schematic:',pout)

def validate_cardinalities():
 qa=csvrows('07_scoring/reviews/qa_scores_adjudicated.csv');intent=csvrows('07_scoring/reviews/intent_observations.csv');comp=csvrows('07_scoring/reviews/composite_observations.csv');wf=csvrows('07_scoring/reviews/workflow_acceptance.csv')
 qa_keys=[(x['question_id'],x['condition']) for x in qa]
 if len(qa)!=180 or len(set(qa_keys))!=180:raise ValueError('QA adjudication expected 180 unique question-condition rows')
 if len(intent)!=192 or len({x['case_id'] for x in intent})!=192:raise ValueError('Intent 192 rows required')
 if len(comp)!=24 or len({x['case_id'] for x in comp})!=24:raise ValueError('Composite24 rows required')
 if len(wf)!=40 or len({(x['workflow_id'],x['criterion_id']) for x in wf})!=40:raise ValueError('40 workflow acceptance criteria expected')
 return qa,intent,comp,wf

if a.figure1:draw_figure1()
if a.schema or a.strict:
 qa,intent,comp,wf=validate_cardinalities()
 report={'qa_rows':len(qa),'qa_scores_filled':sum(x['egts'] in ['0','1'] for x in qa),'intent_rows':len(intent),'intent_complete':sum(x['available_1_0'] in ['0','1'] and x['required_met_1_0'] in ['0','1'] and x['forbidden_violated_1_0'] in ['0','1'] for x in intent),'composite_rows':len(comp),'composite_complete':sum(x['available_1_0'] in ['0','1'] and x['observed_groups'].strip() and x['forbidden_violated_1_0'] in ['0','1'] for x in comp),'workflow_criteria':len(wf),'workflow_complete':sum(x['observed_1_0_NA'].strip().upper() in ['0','1','NA'] for x in wf)}
 print('Completeness:',json.dumps(report,ensure_ascii=False))
 if a.strict and (report['qa_scores_filled']!=180 or report['intent_complete']!=192 or report['composite_complete']!=24 or report['workflow_complete']!=40):
  raise SystemExit('Refusing to plot partial/placeholder results: finish real QA, intent, composite and workflow judgments')
 if a.strict:
  # Complete raw audit requirement
  missing=[];failed_adapter=[]
  for q in qa:
   fn=ROOT/f'06_runs/raw/{q["condition"]}/QA_{q["question_id"]}_{q["condition"]}_T1.json'
   if not fn.exists():missing.append(str(fn.relative_to(ROOT)));continue
   record=json.loads(fn.read_text(encoding='utf-8'))
   if record.get('actual_response',{}).get('adapter_exception') in {'NotImplementedError','ModuleNotFoundError'}:failed_adapter.append(str(fn.relative_to(ROOT)))
  for x in intent+comp:
   fn=ROOT/f'06_runs/raw/B2/{x["case_id"]}_B2_T1.json'
   if not fn.exists():missing.append(str(fn.relative_to(ROOT)))
  if missing or failed_adapter:raise SystemExit(f'Refusing to analyze: missing {len(missing)} raw runs, adapter failures {len(failed_adapter)} (sample {missing[:3]})')
 if a.schema and not a.strict:raise SystemExit(0)

if not a.strict:raise SystemExit(0)

qmap={q['题号']:q for q in csvrows('03_benchmarks/qa60/qa60_CANDIDATE.csv')}
by={(r['question_id'],r['condition']):int(r['egts']) for r in qa}
assert set(by)=={(i,c) for i in qmap for c in CONDS}
S={s:[q for q in qmap if qmap[q]['组别']==s] for s in ALL}
source_cluster={q:qmap[q]['必要材料'].split('+')[0].split('/')[0].replace('上传','').replace(' PDF','').strip() for q in qmap}
acc=[]
for s in ALL+['MACRO_5','MICRO_52']:
 for cond in CONDS:
  if s=='MACRO_5':
   values=[statistics.mean(by[(q,cond)] for q in S[g]) for g in CATS];rate=statistics.mean(values);succ='';n=52;lo=hi=''
  else:
   ids=sum([S[g] for g in CATS],[]) if s=='MICRO_52' else S[s]
   n=len(ids);succ=sum(by[(q,cond)] for q in ids);rate=succ/n;lo,hi=wilson(succ,n)
  acc.append({'stratum':s,'condition':cond,'n':n,'successes':succ,'egts_rate':round(rate,6),'wilson_95_lo':round(lo,6) if lo!='' else '','wilson_95_hi':round(hi,6) if hi!='' else ''})

rng=random.Random(20261104)
def strat_boot_difference(groupnames,left='B2',right='B1',cluster=False,replications=5000):
 base=[statistics.mean(by[(q,left)]-by[(q,right)] for q in S[s]) for s in groupnames]
 point=statistics.mean(base)
 out=[];clusters={s:collections.defaultdict(list) for s in groupnames}
 for s in groupnames:
  for q in S[s]:clusters[s][source_cluster[q]].append(q)
 minclusters=min(len(clusters[s]) for s in groupnames)
 for _ in range(replications):
  vals=[]
  for s in groupnames:
   ids=S[s]
   if cluster:
    ks=list(clusters[s]);draw=[rng.choice(ks) for _ in ks]
    sampled=list(itertools.chain.from_iterable(clusters[s][k] for k in draw))
   else:sampled=rng.choices(ids,k=len(ids))
   vals.append(statistics.mean(by[(q,left)]-by[(q,right)] for q in sampled))
  out.append(statistics.mean(vals))
 out.sort()
 return point,out[int(0.025*(len(out)-1))],out[int(0.975*(len(out)-1))],minclusters
import itertools
forest=[]
for s in CATS+['MACRO_5']:
 groups=CATS if s=='MACRO_5' else [s]
 for c in ['B1','B0']:
  effect,lo,hi,cln=strat_boot_difference(groups,'B2',c,False,a.bootstrap)
  _,clo,chi,cln=strat_boot_difference(groups,'B2',c,True,max(1200,a.bootstrap//2))
  ids=sum([S[g] for g in groups],[])
  pmc,b,cnt=exact_mcnemar([by[(i,'B2')] for i in ids],[by[(i,c)] for i in ids])
  forest.append({'stratum':s,'comparison':'B2 - '+c,'n_questions':len(ids),'diff_pp':round(100*effect,3),'paired_boot_ci_lo_pp':round(100*lo,3),'paired_boot_ci_hi_pp':round(100*hi,3),'source_cluster_lo_pp':round(100*clo,3),'source_cluster_hi_pp':round(100*chi,3),'min_clusters_per_stratum':cln,'mcnemar_exact_p':round(pmc,6),'B2_only_success':b,'comparator_only_success':cnt,'bootstrap_B':a.bootstrap})
# N separate correctness is EGTS interpretation of N group; do not merge in primary
intent_rates=[];matrix=collections.Counter()
for label in INTENTS:
 samples=[x for x in intent if x['gold_intent']==label];avail=[x for x in samples if x['available_1_0']=='1']
 passed=sum(x['required_met_1_0']=='1' and x['forbidden_violated_1_0']=='0' for x in avail)
 bad=sum(x['forbidden_violated_1_0']=='1' for x in avail)
 lo,hi=wilson(passed,len(avail))
 intent_rates.append({'gold_intent':label,'total':len(samples),'available_n':len(avail),'unavailable_n':len(samples)-len(avail),'route_compliant':passed,'route_compliance_rate':round(passed/len(avail),6) if avail else '','wilson_lo':round(lo,6) if avail else '','wilson_hi':round(hi,6) if avail else '','violations':bad})
 for x in avail:matrix[(label,x['observed_primary_group'].strip() or 'NOT_RECORDED')]+=1
composite_summary=[]
for x in comp:
 if x['available_1_0']!='1':continue
 target=set(t for t in x['required_tool_groups'].split('|') if t)
 observed=set(t for t in x['observed_groups'].split('|') if t)
 if not observed:raise SystemExit('Composite observed groups must not be blank; use NONE if no tools')
 if observed=={'NONE'}:observed=set()
 tp=len(target&observed);prec=tp/len(observed) if observed else 0.;rec=tp/len(target) if target else 1.
 f1=2*prec*rec/(prec+rec) if prec+rec else 0.
 composite_summary.append({'case_id':x['case_id'],'precision':round(prec,5),'recall':round(rec,5),'set_f1':round(f1,5),'forbidden_violated':x['forbidden_violated_1_0'],'completion':x['completion_1_0']})
workflow_ids=sorted(set(x['workflow_id'] for x in wf));wf_scores=[];mapwf={}
for caseid in workflow_ids:
 subs=[x for x in wf if x['workflow_id']==caseid];fails=[x for x in subs if x['observed_1_0_NA']=='0'];criteria=[x for x in subs if x['observed_1_0_NA']!='NA'];passn=sum(x['observed_1_0_NA']=='1' for x in subs)
 critical_fail=any(x['critical_1_0']=='1' for x in fails)
 wf_scores.append({'workflow_id':caseid,'total':len(subs),'applicable_n':len(criteria),'pass_n':passn,'fail_n':len(fails),'not_applicable_n':len(subs)-len(criteria),'critical_failure':int(critical_fail),'fully_completed':int(not fails and not critical_fail and bool(criteria))})
 mapwf[caseid]=subs
write_csv('09_analysis/tables/qa_accuracy.csv',acc)
write_csv('09_analysis/tables/qa_paired_effects.csv',forest)
write_csv('09_analysis/tables/intent_route_rates.csv',intent_rates)
write_csv('09_analysis/tables/composite_route_summary.csv',composite_summary,fields=['case_id','precision','recall','set_f1','forbidden_violated','completion'])
write_csv('09_analysis/tables/workflow_step_acceptance.csv',wf_scores)
write_csv('09_analysis/tables/observed_route_matrix.csv',[{'target':k[0],'observed':k[1],'count':n} for k,n in sorted(matrix.items())])

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
# Do not explicitly override colors or styles: matplotlib default palette only.
# Fig02
fig,ax=plt.subplots(figsize=(10,5.8))
for j,c in enumerate(CONDS):
 use=[next(z for z in acc if z['stratum']==s and z['condition']==c) for s in CATS]
 xi=np.arange(len(CATS))+(j-1)*0.17
 y=np.array([x['egts_rate'] for x in use]);e=np.array([[y[i]-x['wilson_95_lo'] for i,x in enumerate(use)],[x['wilson_95_hi']-y[i] for i,x in enumerate(use)]])
 ax.errorbar(xi,y,yerr=e,fmt='o',capsize=3,label=c,ms=6)
ax.set_xticks(np.arange(len(CATS)),['Local lit. (L)','Varieties (V)','Web (W)','Cross-source (X)','Uploaded PDFs (P)'])
ax.set_ylim(0,1.05);ax.set_ylabel('Evidence-grounded task success (EGTS)');ax.set_title('Fig. 2 | 52 answerable questions: per-stratum EGTS (Wilson 95% CI)');ax.legend();fig.tight_layout();fig.savefig(ROOT/'10_figures/Fig02_QA_by_stratum.png',dpi=240);plt.close(fig)
# Fig03 effect forest
fg=[x for x in forest if x['comparison']=='B2 - B1'];fig,ax=plt.subplots(figsize=(9,5.8))
for i,x in enumerate(fg):
 lo,hi=x['paired_boot_ci_lo_pp'],x['paired_boot_ci_hi_pp'];ax.errorbar(x['diff_pp'],i,xerr=[[x['diff_pp']-lo],[hi-x['diff_pp']]],fmt='o',capsize=3)
ax.set_yticks(range(len(fg)),[x['stratum'] for x in fg]);ax.invert_yaxis();ax.axvline(0,linestyle='--');ax.set_xlabel('B2 – B1 EGTS difference (percentage points)');ax.set_title('Fig. 3 | Paired stratified bootstrap, 95% CI');fig.tight_layout();fig.savefig(ROOT/'10_figures/Fig03_paired_effects.png',dpi=240);plt.close(fig)
# Fig04 observable routing proxy (not genuine classifier)
observed_order=INTENTS+['MIXED','NONE','NOT_RECORDED','UNAVAILABLE','OTHER'];mat=np.zeros((len(INTENTS),len(observed_order)),dtype=int)
for (tar,ob),n in matrix.items():
 label=ob if ob in observed_order else 'OTHER';mat[INTENTS.index(tar),observed_order.index(label)]+=n
fig,ax=plt.subplots(figsize=(12,6.3));img=ax.imshow(mat,aspect='auto',cmap='Greys')
ax.set_xticks(range(len(observed_order)),observed_order,rotation=45,ha='right');ax.set_yticks(range(len(INTENTS)),INTENTS)
for i in range(len(INTENTS)):
 for j in range(len(observed_order)):
  if mat[i,j]:ax.text(j,i,str(mat[i,j]),ha='center',va='center',fontsize=8)
ax.set_title('Fig. 4 | Observed action route proxy – NOT classifier predictions');ax.set_xlabel('Observed primary tool category');ax.set_ylabel('Requested intent / task');fig.colorbar(img,ax=ax,shrink=.7,label='Cases');fig.tight_layout();fig.savefig(ROOT/'10_figures/Fig04_observed_routing_matrix.png',dpi=240);plt.close(fig)
# Fig05
fig,ax=plt.subplots(figsize=(10,5.5));xs=np.arange(len(INTENTS));ys=[x['route_compliance_rate'] if x['route_compliance_rate']!='' else np.nan for x in intent_rates]
errlo=[y-x['wilson_lo'] if x['wilson_lo']!='' else 0 for y,x in zip(ys,intent_rates)];errhi=[x['wilson_hi']-y if x['wilson_hi']!='' else 0 for y,x in zip(ys,intent_rates)]
ax.errorbar(xs,ys,yerr=[errlo,errhi],fmt='o',capsize=4)
ax.set_xticks(xs,INTENTS,rotation=35,ha='right');ax.set_ylim(0,1.06);ax.set_ylabel('Required actions met AND no forbidden actions');ax.set_title('Fig. 5 | Observable route compliance, conditional on availability');fig.tight_layout();fig.savefig(ROOT/'10_figures/Fig05_intent_route_compliance.png',dpi=240);plt.close(fig)
# Fig06 WF matrix, different criteria numbers to max, mark absent criteria NA
maxk=max(len(x) for x in mapwf.values());wfmat=np.full((len(workflow_ids),maxk),np.nan)
for i,caseid in enumerate(workflow_ids):
 for j,x in enumerate(mapwf[caseid]):wfmat[i,j]=float(x['observed_1_0_NA']) if x['observed_1_0_NA']!='NA' else np.nan
fig,ax=plt.subplots(figsize=(10,5));cmap=plt.get_cmap('Greys').copy();cmap.set_bad('white')
im=ax.imshow(np.ma.masked_invalid(wfmat),vmin=0,vmax=1,aspect='auto',cmap=cmap)
for i in range(wfmat.shape[0]):
 for j in range(wfmat.shape[1]):
  if np.isnan(wfmat[i,j]):continue
  ax.text(j,i,'Pass' if wfmat[i,j]==1 else 'Fail',ha='center',va='center',fontsize=7,rotation=0)
ax.set_yticks(range(len(workflow_ids)),workflow_ids);ax.set_xticks(range(maxk),[f'Criterion {i+1}' for i in range(maxk)],rotation=45,ha='right');ax.set_title('Fig. 6 | Audited end-to-end workflow acceptance (NA blank)');fig.tight_layout();fig.savefig(ROOT/'10_figures/Fig06_workflow_acceptance.png',dpi=240);plt.close(fig)
# Supplementary timing if genuinely observed
has_latency=[x for x in qa if x['duration_s'].strip()]
if len(has_latency)==180:
 vals={c:[float(x['duration_s']) for x in has_latency if x['condition']==c] for c in CONDS}
 fig,ax=plt.subplots(figsize=(8,5));ax.boxplot([vals[c] for c in CONDS],tick_labels=CONDS);ax.set_ylabel('End-to-end seconds');ax.set_title('Supplementary Fig. 1 | Response time by condition');fig.tight_layout();fig.savefig(ROOT/'10_figures/SuppF1_latency.png',dpi=240);plt.close(fig)
# Analysis manifest reproducible source hashes; figures are user study results, not preexisting data
files=['07_scoring/reviews/qa_scores_adjudicated.csv','07_scoring/reviews/intent_observations.csv','07_scoring/reviews/composite_observations.csv','07_scoring/reviews/workflow_acceptance.csv','00_protocol/locked_manifest.json']
jsondump('09_analysis/tables/analysis_manifest.json',{'analyzed_utc':utc(),'primary':'equal-weight macro EGTS on L,V,W,X,P; B2-B1','qa_n':52,'unanswerable_n':8,'intent_n':192,'multi_n':24,'workflow_n':7,'bootstrap_B':a.bootstrap,'seed':20261104,'source_sha256':{f:sha256_file(ROOT/f) if (ROOT/f).exists() else None for f in files},'figure_caveat':'Route matrix is observed behavior; not model-intent prediction. WF07 is synthetic fixture.'})
print('DONE: 5 numerical result figures + tables from fully populated real data only, no fabricated scores.')

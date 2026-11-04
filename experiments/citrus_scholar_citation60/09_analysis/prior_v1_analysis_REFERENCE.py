#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CitrusScholar 60题成对统计与Fig2/3，绝不自动补全未完成的结果。
用法：python CitrusScholar_完成后统计作图.py --input CitrusScholar_逐题得分空表.csv --output result_figures
先在CSV中填满180行的egts(0或1)，记录各题唯一的B0/B1/B2条件。统计单位是题目，不是运行轮次。
"""
from __future__ import annotations
import argparse,csv,random,math,collections,statistics
from pathlib import Path

def bmean(a):return sum(a)/len(a) if a else float('nan')

def percentile(vals,p):
    v=sorted(vals);idx=(len(v)-1)*p;lo=int(idx);hi=min(len(v)-1,lo+1)
    return v[lo]*(1-(idx-lo))+v[hi]*(idx-lo)

def ci_pairs(items, n=4000,seed=20261104):
    rng=random.Random(seed);m=len(items)
    dist=[bmean([items[rng.randrange(m)] for _ in range(m)]) for _ in range(n)]
    return percentile(dist,0.025),percentile(dist,0.975)

def mcnemar_exact(pair):
    b=sum(a==0 and c==1 for a,c in pair)
    c=sum(a==1 and c==0 for a,c in pair)
    n=b+c
    if not n:return 1.0,b,c
    k=min(b,c)
    p=min(1.0,2*sum(math.comb(n,i) for i in range(k+1))/2**n)
    return p,b,c

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',default='CitrusScholar_逐题得分空表.csv');ap.add_argument('--output',default='result_figures')
    args=ap.parse_args(); inp=Path(args.input)
    with inp.open('r',encoding='utf-8-sig',newline='') as f:raw=list(csv.DictReader(f))
    groups=['L','V','W','X','P','N'];conds=['B0','B1','B2'];data={}
    for r in raw:
        key=(r['question_id'],r['condition'])
        if r['stratum'] not in groups or r['condition'] not in conds:raise ValueError('未知题组/组别: '+str(key))
        if key in data:raise ValueError('重复题组: '+str(key))
        if r['egts'] not in ('0','1'):
            raise ValueError('拒绝基于缺失评分出图，请先填写真实EGTS: '+str(key))
        data[key]={'stratum':r['stratum'],'score':int(r['egts']),'cluster':r.get('source_cluster','')}
    if len(data)!=180:raise ValueError(f'主设计需要180行独立条件评测，得到{len(data)}。')
    bygroup={g:[] for g in groups}
    for qid in {qid for qid,_ in data}:
        if not all((qid,c) in data for c in conds):raise ValueError('某题条件不完整: '+qid)
        g=data[(qid,'B2')]['stratum']
        if any(data[(qid,c)]['stratum']!=g for c in conds):raise ValueError('同一题组别不一致: '+qid)
        bygroup[g].append((qid,[data[(qid,c)]['score'] for c in conds]))
    if [len(bygroup[g]) for g in groups]!=[12,10,10,12,8,8]:raise ValueError('组别题数偏离预登记的12/10/10/12/8/8')
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    lines=['stratum,n,B0_rate,B1_rate,B2_rate,B2_minus_B1_pp,ci_low_pp,ci_high_pp,mcnemar_exact_p,B1_only_success,B2_only_success']
    for g in groups:
        arr=[scores for _,scores in bygroup[g]]
        diffs=[z[2]-z[1] for z in arr]
        ci=ci_pairs(diffs)
        pv,b,c=mcnemar_exact([(z[1],z[2]) for z in arr])
        lines.append(f'{g},{len(arr)},{bmean([z[0] for z in arr]):.4f},{bmean([z[1] for z in arr]):.4f},{bmean([z[2] for z in arr]):.4f},{100*bmean(diffs):.2f},{100*ci[0]:.2f},{100*ci[1]:.2f},{pv:.4f},{b},{c}')
    (out/'score_by_stratum.csv').write_text('\n'.join(lines)+'\n',encoding='utf-8-sig')
    macro={c:bmean([bmean([z[1][ci] for z in bygroup[g]]) for g in groups[:5]]) for ci,c in enumerate(conds)}
    (out/'macro_scores.txt').write_text('均为真实填入数据的5个可回答层(L/V/W/X/P)等权宏平均；N单独报告。\n'+ '\n'.join(f'{k}: {v:.4f}' for k,v in macro.items())+'\n',encoding='utf-8')
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(10,5.2));xx=list(range(6))
    for ci,c in enumerate(conds):
        yy=[bmean([scores[ci] for _,scores in bygroup[g]]) for g in groups]
        ax.plot([i+(-.14+ci*.14) for i in xx],yy,marker='o',linestyle='none',markersize=7,label=c)
    ax.set_xticks(xx,groups);ax.set_ylim(-.05,1.05);ax.set_ylabel('EGTS, proportion');ax.set_xlabel('Question stratum');ax.set_title('Fig. 2 | Evidence-grounded task success');ax.legend();ax.grid(axis='y',alpha=.25)
    fig.tight_layout();fig.savefig(out/'Fig2_EGTS_by_stratum.png',dpi=200);plt.close(fig)
    fig,ax=plt.subplots(figsize=(8.6,5.8));means=[];los=[];his=[]
    for g in groups:
        diffs=[z[1][2]-z[1][1] for z in bygroup[g]];ci=ci_pairs(diffs)
        means.append(100*bmean(diffs));los.append(100*ci[0]);his.append(100*ci[1])
    ax.errorbar(means,list(range(6)),xerr=[[m-l for m,l in zip(means,los)],[h-m for h,m in zip(his,means)]],fmt='o',capsize=4)
    ax.axvline(0,linestyle='--',linewidth=1);ax.set_yticks(list(range(6)),groups);ax.set_xlabel('B2 - B1 (percentage points)');ax.set_title('Fig. 3 | Paired improvement (within-stratum bootstrap CI)');ax.invert_yaxis();ax.grid(axis='x',alpha=.25)
    fig.tight_layout();fig.savefig(out/'Fig3_B2_minus_B1.png',dpi=200);plt.close(fig)
    print('完成: '+str(out));print('警告: 这不是以文献为簇的bootstrap，重复文献会造成相关性，正式论文需补做来源簇敏感性分析。')

if __name__=='__main__':main()

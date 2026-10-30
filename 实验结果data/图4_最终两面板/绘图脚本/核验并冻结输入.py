from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

SOURCE = Path(r'E:\codex_WORKSPACES\Citrus_QA_Agent')
HERE = Path(__file__).resolve().parent.parent
DATA = HERE / '数据'
RAW = SOURCE / '07_交付与文档_delivery_docs/04_数据_data/原始_raw/runs'
V11 = SOURCE / '07_交付与文档_delivery_docs/05_实验_rerun_v11'
ARM_SUMMARY = SOURCE / '实验结果data/09_联网五组对照_数据/02_结果数据_results/web_arms_summary.json'
CANONICAL = SOURCE / '07_交付与文档_delivery_docs/04_数据_data/审计_audit/fig4_v11_structural_audit.csv'
OLD_CHECK = SOURCE / '实验结果data/图4_联网混池风险与交付可靠性/数据/图4_核验_数据溯源.json'
JUDGES = ['kimi-k2.6', 'glm-5.3']
H1 = re.compile(r'(?m)^#\s*联网获取信息\s*$')
WEB = re.compile(r'\[W\d+\]')
LOCAL = re.compile(r'\[(\d+)\]')


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load(p):
    return [json.loads(s) for s in p.read_text(encoding='utf-8-sig').splitlines() if s.strip()]


def write_csv(name, rows):
    with (DATA / name).open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    summary = json.loads(ARM_SUMMARY.read_text(encoding='utf-8-sig'))
    old = json.loads(OLD_CHECK.read_text(encoding='utf-8-sig'))
    inputs = [ARM_SUMMARY, CANONICAL, OLD_CHECK]
    inputs += [SOURCE / '实验结果data/00_实验数据与图件_最终口径清单.md',
               SOURCE / 'paper/图件方案_CitrusQA_HA投稿版.md',
               SOURCE / 'paper/图件契约_nature-figure.md',
               SOURCE / 'experiment/09_联网五组对照_web_arms/01_实验脚本_scripts/analyze_web_arms.py',
               SOURCE / '实验结果data/图4_联网混池风险与交付可靠性/绘图脚本/审计_Fig4b结构合规_v11.py']
    for d in summary['arms'].values():
        inputs.extend([RAW / d['arm'] / 'judge_C_final.jsonl', RAW / d['arm'] / 'answers.jsonl'])
    for sub in ['e2e/runs/pools144', 'system_layer']:
        inputs.extend([V11 / sub / 'answers_final.jsonl', V11 / sub / 'retrieval.jsonl'])
    before = {str(p): digest(p) for p in inputs}
    label_map = {'A': 'Local-only', 'B': 'Unfiltered', 'C': 'Receipt-filtered',
                 "C'": 'Model-visible', 'D': 'Excerpt-only'}
    arms, verdicts, common_ids, missing = [], [], None, []
    expected = [3, 23, 32, 18, 22]
    for i, (tag, frozen) in enumerate(summary['arms'].items()):
        jp = RAW / frozen['arm'] / 'judge_C_final.jsonl'
        ap = RAW / frozen['arm'] / 'answers.jsonl'
        rows, answers = load(jp), load(ap)
        ids = {r['id'] for r in rows}
        assert len(rows) == len(ids) == len(answers) == 48
        assert ids == {r['id'] for r in answers}
        assert all(not a.get('error') and (a.get('answer_final') or a.get('answer') or '').strip() for a in answers)
        if common_ids is None:
            common_ids = ids
        assert common_ids == ids, 'Five arms must use the same question subset'
        n = h = valid = 0
        for row in rows:
            for judge in JUDGES:
                item = row[judge]
                assert isinstance(item, dict) and not item.get('error')
                flag = item.get('hallucinated')
                assert flag is None or isinstance(flag, bool)
                n += 1
                h += int(flag is True)
                valid += int(isinstance(flag, bool))
                if flag is None:
                    missing.append(dict(arm=tag, question_id=row['id'], judge=judge, raw_value=item, source=str(jp)))
                verdicts.append(dict(arm=tag, question_id=row['id'], judge=judge,
                                     hallucinated='' if flag is None else int(flag),
                                     status='missing' if flag is None else 'valid', source=str(jp)))
        assert h == frozen['hallucinated'] == old['arms'][tag]['h'] == expected[i]
        assert n == frozen['n'] == old['arms'][tag]['n'] == 96
        assert abs(h/n - frozen['rate_pooled']) < 0.000051
        arms.append(dict(arm=tag, label=label_map[tag], events=h, verdicts=n, valid_verdicts=valid,
                         percent=100*h/n, judge_source=str(jp), answer_source=str(ap)))

    assert [(m['arm'],m['question_id'],m['judge'],m['raw_value']) for m in missing] == [('D','C48-new-19','kimi-k2.6',{})]
    pool_file = V11 / 'e2e/runs/pools144/answers_final.jsonl'
    pool_rows = load(pool_file)
    pool_retrieval = load(V11 / 'e2e/runs/pools144/retrieval.jsonl')
    assert len(pool_rows) == len({r['id'] for r in pool_rows}) == 144
    assert {r['id'] for r in pool_rows} == {r['id'] for r in pool_retrieval}
    pool_checks = []
    for a in pool_rows:
        assert not a.get('error') and a['answer'].strip()
        text = a['answer']
        matches = list(H1.finditer(text))
        assert len(matches) <= 1
        m = matches[0] if matches else None
        local, web = (text[:m.start()], text[m.end():]) if m else (text, '')
        nl, nw = len(WEB.findall(local)), len(WEB.findall(web))
        pool_checks.append(dict(question_id=a['id'], pool=a['pool'], local_web_occurrences=nl,
                                web_section_occurrences=nw, local_violation=int(nl > 0),
                                has_web_citations=int(nw > 0)))
    assert all(sum(r['pool'] == p for r in pool_checks) == 48 for p in 'ABC')
    c = [r for r in pool_checks if r['pool'] == 'C']
    boundary = dict(local_occurrences=sum(r['local_web_occurrences'] for r in pool_checks),
                    web_occurrences=sum(r['web_section_occurrences'] for r in pool_checks),
                    local_violation_answers=sum(r['local_violation'] for r in pool_checks),
                    all_answers=len(pool_checks), pool_c_with_web=sum(r['has_web_citations'] for r in c),
                    pool_c_answers=len(c))
    assert list(boundary.values()) == [0, 820, 0, 144, 47, 48], boundary

    ap = V11 / 'system_layer/answers_final.jsonl'
    rp = V11 / 'system_layer/retrieval.jsonl'
    answers, retrieval = load(ap), load(rp)
    index = {r['id']: r for r in retrieval}
    assert len(answers) == len(index) == 84
    assert {r['id'] for r in answers} == set(index)
    citation_checks = []
    for a in answers:
        assert a['answer_final'].strip()
        rr = index[a['id']]
        pool = {str(c['ref_id']) for c in rr['cited']}
        assert all(c.get('doi') or c.get('title') or c.get('paper_id') for c in rr['cited'])
        marks = LOCAL.findall(a['answer_final'])
        valid = sum(m in pool for m in marks)
        citation_checks.append(dict(question_id=a['id'], markers=len(marks), resolvable=valid,
                                    out_of_pool=len(marks)-valid))
    integrity = dict(markers=sum(r['markers'] for r in citation_checks),
                     resolvable=sum(r['resolvable'] for r in citation_checks),
                     out_of_pool=sum(r['out_of_pool'] for r in citation_checks), answers=len(answers))
    assert list(integrity.values()) == [1649, 1649, 0, 84], integrity

    canonical = list(csv.DictReader(CANONICAL.open(encoding='utf-8-sig')))
    checks = [('本地段出现联网编号的回答数', 0, '144'), ('本地段联网编号出现次数', 0, '—'),
              ('联网段联网编号出现次数', 820, '—'), ('联网段携带联网编号的回答数（C 池）', 47, '48'),
              ('正文引用标记总数', 1649, '—'), ('可解析标记数', 1649, '1649'),
              ('交付文本内池外引用数', 0, '1649')]
    for metric, num, den in checks:
        row = next(r for r in canonical if r['指标名'] == metric)
        assert int(row['分子']) == num and row['分母'] == den
    after = {str(p): digest(p) for p in inputs}
    assert before == after, 'A read-only source changed during verification'
    frozen = dict(arms=arms, boundary=boundary, integrity=integrity,
                  canonical_audit=str(CANONICAL), canonical_match=True,
                  denominator_policy='User-specified frozen nominal denominator; no missing labels imputed.',
                  missing_verdicts=missing, unresolved_conflict='D has 95 non-empty verdicts but frozen nominal denominator is 96; latest inventory incorrectly states no missing verdicts.',
                  all_sources_unchanged=True, source_sha256=after)
    (DATA / 'Fig4_冻结绘图输入.json').write_text(json.dumps(frozen, ensure_ascii=False, indent=2), encoding='utf-8')
    write_csv('五臂_逐判定_核验.csv', verdicts)
    write_csv('结构审计_三池逐回答_核验.csv', pool_checks)
    write_csv('结构审计_系统层逐回答_核验.csv', citation_checks)
    write_csv('五臂_绘图输入.csv', arms)
    with (DATA / 'fig4_v11_structural_audit_冻结副本.csv').open('wb') as f:
        f.write(CANONICAL.read_bytes())
    print(json.dumps(dict(arms=[(a['arm'],a['events'],a['verdicts']) for a in arms],
                          boundary=boundary, integrity=integrity, readonly_inputs=len(inputs)), ensure_ascii=False))


if __name__ == '__main__':
    main()

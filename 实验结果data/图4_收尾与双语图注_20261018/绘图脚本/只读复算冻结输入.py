"""Recompute frozen observations from existing records; never run a model or edit raw inputs."""
from pathlib import Path
from collections import Counter
import ast, csv, hashlib, json, re

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / '数据'
snap = json.loads((DATA / 'Fig4_冻结绘图输入.json').read_text(encoding='utf-8'))
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
load = lambda p: [json.loads(s) for s in Path(p).read_text(encoding='utf-8-sig').splitlines() if s.strip()]
assert all(sha(p) == h for p, h in snap['source_sha256'].items())
verdicts, common = [], None
for arm in snap['arms']:
    rows, answers = load(arm['judge_source']), load(arm['answer_source'])
    ids = {r['id'] for r in rows}
    assert len(ids) == len(rows) == len(answers) == 48
    assert ids == {r['id'] for r in answers}
    common = ids if common is None else common
    assert ids == common
    flags = []
    for row in rows:
        for judge in ['kimi-k2.6', 'glm-5.3']:
            flag = row[judge].get('hallucinated')
            assert flag is None or isinstance(flag, bool)
            flags.append(flag)
            verdicts.append(dict(arm=arm['arm'], question_id=row['id'], judge=judge,
                                 hallucinated='' if flag is None else int(flag),
                                 status='missing' if flag is None else 'valid'))
    assert (sum(x is True for x in flags), len(flags), sum(isinstance(x, bool) for x in flags)) == (arm['events'], arm['verdicts'], arm['valid_verdicts'])
missing = [v for v in verdicts if v['status'] == 'missing']
assert len(missing) == 1 and (missing[0]['arm'], missing[0]['question_id'], missing[0]['judge']) == ('D', 'C48-new-19', 'kimi-k2.6')
poolfile = next(p for p in snap['source_sha256'] if 'pools144' in p and p.endswith('answers_final.jsonl'))
pool = load(poolfile)
H1, WEB, URL = re.compile(r'(?m)^#\s*联网获取信息\s*$'), re.compile(r'\[W\d+\]'), re.compile(snap['source_screening']['url_pattern'])
checks, candidates = [], []
for a in pool:
    m = H1.search(a['answer'])
    local, web = (a['answer'][:m.start()], a['answer'][m.end():]) if m else (a['answer'], '')
    checks.append(dict(question_id=a['id'], pool=a['pool'], local_web_occurrences=len(WEB.findall(local)),
                       web_section_occurrences=len(WEB.findall(web)), local_url=int(bool(URL.search(local)))))
    terms = snap['source_screening']['authority_terms']
    hit = [s.strip() for s in re.split(r'(?<=[。；\n])', local) if s.strip() and any(k in s for k in terms)]
    if hit: candidates.append(dict(id=a['id'], sentences=hit))
assert len(pool) == len({r['id'] for r in pool}) == 144
assert Counter(r['pool'] for r in pool) == Counter(A=48, B=48, C=48)
computed = dict(local_occurrences=sum(x['local_web_occurrences'] for x in checks),
                web_occurrences=sum(x['web_section_occurrences'] for x in checks),
                local_violation_answers=sum(x['local_web_occurrences'] > 0 for x in checks),
                all_answers=len(checks), pool_c_with_web=sum(x['pool'] == 'C' and x['web_section_occurrences'] > 0 for x in checks),
                pool_c_answers=48, local_url_answers=sum(x['local_url'] for x in checks))
assert computed == snap['boundary']
assert len(candidates) == 51 and sum(len(x['sentences']) for x in candidates) == 263
afile = next(p for p in snap['source_sha256'] if 'system_layer' in p and p.endswith('answers_final.jsonl'))
rfile = next(p for p in snap['source_sha256'] if 'system_layer' in p and p.endswith('retrieval.jsonl'))
answers, retrieval = load(afile), {r['id']: r for r in load(rfile)}
assert len(answers) == len(retrieval) == 84 and {a['id'] for a in answers} == set(retrieval)
markers = []
for a in answers:
    refs = {str(x['ref_id']) for x in retrieval[a['id']]['cited']}
    marks = re.findall(r'\[(\d+)\]', a['answer_final'])
    markers.append(dict(question_id=a['id'], markers=len(marks), resolvable=sum(m in refs for m in marks), out_of_pool=sum(m not in refs for m in marks)))
assert dict(markers=sum(r['markers'] for r in markers), resolvable=sum(r['resolvable'] for r in markers), out_of_pool=sum(r['out_of_pool'] for r in markers), answers=len(answers)) == snap['integrity']
canonical = list(csv.DictReader(Path(snap['canonical_audit']).open(encoding='utf-8-sig')))
for metric, num, den in [('本地段出现联网编号的回答数', 0, '144'), ('联网段联网编号出现次数', 820, '—'), ('可解析标记数', 1649, '1649'), ('交付文本内池外引用数', 0, '1649')]:
    r = next(x for x in canonical if x['指标名'] == metric)
    assert int(r['分子']) == num and r['分母'] == den
assert all(sha(p) == h for p, h in snap['source_sha256'].items())
for name, rows in [('五臂_逐判定_核验.csv', verdicts), ('结构审计_三池逐回答_核验.csv', checks), ('结构审计_系统层逐回答_核验.csv', markers)]:
    with (DATA / name).open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
report = dict(status='PASS_WITH_DISCLOSED_DENOMINATOR_CONFLICT', existing_raw_records_only=True,
              arms=[dict(arm=a['arm'], events=a['events'], nominal_slots=a['verdicts'], nonempty=a['valid_verdicts']) for a in snap['arms']],
              boundary=computed, integrity=snap['integrity'], screened_answers=51, sentence_fragments=263,
              missing=missing, source_hashes_unchanged=True,
              scope='Lexical and marker-level recalculation. Existing targeted review is not repeated or upgraded to exhaustive semantic validation.')
(DATA / '只读复算核验.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))

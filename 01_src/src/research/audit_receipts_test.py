"""P6 contracts using small synthetic algorithm fixtures, never research evidence."""
import io

import pandas as pd
import pytest
from openpyxl import Workbook, load_workbook

from src.research.audit_methods import audit_frame


def scan(frame, **kwargs):
    receipt = {}
    findings, _ = audit_frame(frame, kwargs.pop('mapping', {}), kwargs.pop('groups', []),
                             kwargs.pop('values', []), execution_receipt=receipt, **kwargs)
    return findings, receipt['rules']


def signals(findings, check):
    return [f for f in findings if f['check'] == check]


def test_receipts_describe_actual_gates_and_stronger_relation_skips():
    findings, rules = scan(pd.DataFrame({'a': range(6), 'b': range(6), 'g': ['x']*6}),
                           groups=['g'], values=['a'])
    assert rules['identical_columns']['tested'] == 1
    assert rules['identical_columns']['status'] == 'executed'
    assert rules['constant_offset_columns']['tested'] == 0
    assert rules['constant_offset_columns']['status'] == 'skipped'
    assert rules['iqr_outlier']['tested'] == 0
    assert rules['iqr_outlier']['status'] == 'skipped'
    assert rules['decimal_ending_concentration']['status'] == 'skipped'
    assert rules['expected_experiment_structure']['status'] == 'skipped'
    assert signals(findings, 'identical_columns')


def test_invalid_operator_never_counts_as_success_or_hides_behind_truncation():
    _, rules = scan(pd.DataFrame({'a': [1,2], 'b': [0,1]}),
                    relations=[{'left':'a','op':'!=','right':'b'}], source_coverage={'truncated': True})
    assert rules['logic_conflict']['status'] == 'invalid_input'
    assert rules['logic_conflict']['tested'] == 0
    assert 'invalid_operator_config' in rules['logic_conflict']['reasons']


def test_large_integer_ranges_relations_and_pair_equality_remain_distinguishable():
    base = 2**60
    a = [str(base+i) for i in range(6)]
    b = [str(base+i+(i%2)) for i in range(6)]
    findings, rules = scan(pd.DataFrame({'a':a, 'b':b}),
        ranges={'a':{'max':str(base)}}, relations=[{'left':'a','op':'==','right':'b'}])
    assert len(signals(findings,'out_of_range')) == 5
    assert [x['row'] for x in signals(findings,'logic_conflict')] == [3,5,7]
    assert not signals(findings,'identical_columns')
    assert not signals(findings,'constant_offset_columns')
    assert not signals(findings,'exact_linear_columns')
    assert rules['logic_conflict']['tested'] == 6


def test_group_iqr_does_not_collapse_large_integer_variation():
    base = 2**60
    findings, rules = scan(pd.DataFrame({'g':['A']*9, 'v':[str(base+x) for x in [0,1,2,3,4,5,6,7,100]]}),
                           groups=['g'], values=['v'])
    assert [x['row'] for x in signals(findings,'iqr_outlier')] == [10]
    assert rules['iqr_outlier']['tested'] == 9


def test_failed_transform_has_failed_receipt(monkeypatch):
    from src.research import audit_methods
    monkeypatch.setattr(audit_methods, '_transform', lambda *a: (_ for _ in ()).throw(ValueError('synthetic calculation failure')))
    _, rules = scan(pd.DataFrame({'a':range(6),'b':[1,3,8,4,6,12]}))
    assert rules['constant_offset_columns']['status'] == 'failed'
    assert rules['constant_offset_columns']['tested'] == 1
    assert rules['exact_linear_columns']['status'] == 'failed'


def test_composite_keys_are_tuples_and_missing_components_are_excluded():
    frame = pd.DataFrame({'id':['A','A','B','B',None], 'visit':[1,2,1,1,1]})
    findings, rules = scan(frame, composite_keys=[['id','visit']])
    assert [x['row'] for x in signals(findings,'duplicate_composite_key')] == [4,5]
    assert rules['duplicate_composite_key']['tested'] == 4
    assert rules['duplicate_composite_key']['skipped'] == 1


def test_raw_tail_requires_preserved_lexical_precision_and_does_not_pad():
    frame = pd.DataFrame({'v':[f'{i}.1200' for i in range(12)]})
    options = {'values':['v'], 'pattern_checks':{'raw_numeric_tail':True}}
    findings, rules = scan(frame, **options)
    assert rules['raw_numeric_tail']['status'] == 'skipped'
    assert not signals(findings,'raw_numeric_tail')
    frame.attrs['lexical_precision_preserved'] = True
    findings, rules = scan(frame, **options)
    signal = signals(findings,'raw_numeric_tail')[0]
    assert signal['evidence']['tail'] == '00'
    assert signal['evidence']['count'] == 12
    assert rules['raw_numeric_tail']['tested'] == 12
    short = pd.DataFrame({'v':[f'{i}.1' for i in range(12)]})
    short.attrs['lexical_precision_preserved'] = True
    findings, rules = scan(short, **options)
    assert rules['raw_numeric_tail']['tested'] == 0
    assert not signals(findings,'raw_numeric_tail')


def test_raw_tail_minimum_sample_is_per_precision_stratum():
    frame = pd.DataFrame({'v':['1.1200']*12 + [f'{i}.120' for i in range(12)]})
    frame.attrs['lexical_precision_preserved'] = True
    findings, rules = scan(frame, values=['v'], pattern_checks={'raw_numeric_tail':True})
    found = signals(findings, 'raw_numeric_tail')
    assert len(found) == 1 and found[0]['evidence']['precision_exponent'] == (3,0)
    assert rules['raw_numeric_tail']['tested'] == 12


def test_difference_windows_do_not_sort_or_bridge_missing_values():
    options = {'values':['v'], 'pattern_checks':{'order_confirmed':True,
               'adjacent_equal_differences':True,'sliding_equal_differences':True,'window':4}}
    findings, rules = scan(pd.DataFrame({'v':[1,2,3,4,None,5,6,7]}), **options)
    windows = signals(findings,'sliding_equal_differences')
    assert len(windows) == 1
    assert [c['row'] for c in windows[0]['evidence']['cells']] == [2,3,4,5]
    assert rules['sliding_equal_differences']['tested'] == 1
    shuffled, _ = scan(pd.DataFrame({'v':[1,3,2,4]}), **options)
    assert not signals(shuffled,'sliding_equal_differences')
    no_order, receipts = scan(pd.DataFrame({'v':range(6)}), values=['v'],
                             pattern_checks={'adjacent_equal_differences':True})
    assert receipts['adjacent_equal_differences']['status'] == 'skipped'


def test_rowwise_windows_require_units_and_keep_physical_column_order():
    frame = pd.DataFrame({'x':[1], 'y':[2], 'z':[3]})
    patterns = {'axis':'columns','order_confirmed':True,'adjacent_equal_differences':True}
    _, rules = scan(frame, values=['x','y','z'], pattern_checks=patterns)
    assert rules['adjacent_equal_differences']['status'] == 'skipped'
    findings, _ = scan(frame, values=['x','y','z'], pattern_checks={**patterns,'same_units_confirmed':True})
    assert signals(findings,'adjacent_equal_differences')[0]['evidence']['cells'] == [
        {'row':2,'column_index':1},{'row':2,'column_index':2},{'row':2,'column_index':3}]
    _, rules = scan(frame, values=['x','y','z'], pattern_checks={**patterns,'same_units_confirmed':True,'columns':['z','y','x']})
    assert rules['adjacent_equal_differences']['status'] == 'invalid_input'


def test_repeated_sequences_and_blocks_have_both_locations():
    frame = pd.DataFrame({'a':[1,4,2,1,4,2], 'b':[10,40,20,10,40,20]})
    findings, rules = scan(frame, values=['a','b'], pattern_checks={
        'order_confirmed':True,'window':3,'repeated_sequences':True,'repeated_blocks':True})
    assert signals(findings,'repeated_sequences')
    blocks = signals(findings,'repeated_blocks')
    assert blocks and len(blocks[0]['evidence']['cells']) == 12
    assert rules['repeated_blocks']['tested'] == 4


def test_explicit_expected_structure_accepts_unbalanced_design_and_reports_missing_cell():
    frame = pd.DataFrame({'treatment':['A','A','B'],'animal':['a','b','c'],'rep':[1,1,1]})
    structure = {'factors':['treatment'],'unit_column':'animal','replicate_column':'rep',
        'expected_cells':[{'treatment':'A','expected_units':2,'expected_replicates':1},
                          {'treatment':'B','expected_units':1,'expected_replicates':1}]}
    findings, rules = scan(frame, groups=['treatment'], expected_structure=structure)
    assert not signals(findings,'expected_experiment_structure')
    assert rules['expected_experiment_structure']['tested'] == 2
    structure['expected_cells'].append({'treatment':'C','expected_units':1})
    findings, _ = scan(frame, expected_structure=structure)
    assert signals(findings,'expected_experiment_structure')[0]['evidence']['expected']['treatment'] == 'C'


def test_numeric_prefix_and_finding_caps_are_receipted():
    frame = pd.DataFrame({'a':range(20001),'b':range(20001)})
    _, rules = scan(frame)
    assert rules['identical_columns']['status'] == 'truncated'
    assert rules['identical_columns']['scopes'][0]['used_rows'] == 20000
    # Sparse required fields produce more findings than can be returned.
    findings, rules = scan(pd.DataFrame({'required':[None]*20001}), required_fields=['required'])
    assert len(findings) == 20001
    assert findings[-1]['check'] == 'findings_truncated'
    assert rules['missing_cell']['status'] == 'truncated'
    assert rules['missing_cell']['tested'] == 20001


def workbook_bytes():
    wb = Workbook()
    wb.active.title = 'oversized'
    wb.active.cell(1,201,'outside_column_budget')
    ws = wb.create_sheet('usable')
    ws.append(['v','label'])
    ws.append([2**60+1,'a'])
    ws.append([None,'b'])
    wb.create_sheet('empty')
    out = io.BytesIO()
    wb.save(out)
    # openpyxl's writer rounds numeric cells to 16 significant digits. Put the
    # exact integer into this synthetic OOXML fixture so the reader is tested
    # against bytes that actually contain it, rather than an already rounded value.
    from zipfile import ZipFile
    from xml.etree import ElementTree as ET
    exact = io.BytesIO()
    with ZipFile(io.BytesIO(out.getvalue())) as source, ZipFile(exact, 'w') as target:
        for member in source.infolist():
            payload = source.read(member.filename)
            if member.filename == 'xl/worksheets/sheet2.xml':
                tree = ET.fromstring(payload)
                tree.find('.//{*}c[@r="A2"]/{*}v').text = str(2**60+1)
                payload = ET.tostring(tree)
            target.writestr(member, payload)
    return exact.getvalue()


def test_shared_loader_keeps_large_int_with_missing_and_supplement_sheet_failures():
    from src.research.tools import _load_frame_from_bytes, _audit_supplement_tables
    data = workbook_bytes()
    frame, meta = _load_frame_from_bytes(data,'synthetic.xlsx',sheet_name='usable')
    assert frame.iloc[0,0] == 2**60+1
    assert meta['lexical_precision_preserved'] is False
    findings, coverage = _audit_supplement_tables([('synthetic.xlsx',data)])
    states = {entry['sheet']:entry['status'] for entry in coverage}
    assert states == {'oversized':'failed','usable':'executed','empty':'skipped'}
    receipt = next(entry for entry in coverage if entry['sheet']=='usable')['rule_execution']
    assert receipt['rules']['iqr_outlier']['status'] == 'skipped'


def test_supplement_total_cell_budget_marks_partial_and_unvisited_tables():
    from src.research.tools import _audit_supplement_tables
    _, coverage = _audit_supplement_tables([('a.csv',b'v,label\n1,a\n2,b\n3,c\n'),
                                          ('b.csv',b'v,label\n1,a\n2,b\n')], max_cells=4)
    assert coverage[0]['status'] == 'truncated' and coverage[0]['tested'] == 2
    assert coverage[0]['source_coverage']['processed_rows'] == 2
    assert coverage[0]['source_coverage']['source_rows'] == 3
    rule = coverage[0]['rule_execution']['rules']['duplicate_row']
    assert rule['source_coverage']['processed_rows'] == rule['tested'] == 2
    assert coverage[1]['status'] == 'truncated' and coverage[1]['tested'] == 0


def test_annotations_do_not_disable_deterministic_scan_or_change_receipts(monkeypatch):
    from src.research import tools
    monkeypatch.setattr(tools,'get_request_tables',lambda:[{'name':'synthetic.csv','data':b'v,label\n1,a\n1,a\n3,c\n'}])
    monkeypatch.setattr(tools,'get_user_id',lambda:0)
    monkeypatch.setattr(tools,'get_session_id',lambda:'')
    _, plain = tools.audit_uploaded_data.func()
    _, annotated = tools.audit_uploaded_data.func(annotations_json=[{'row':2,'verdict':'正常','reason':'synthetic annotation'}])
    before, after = plain['results'][0], annotated['results'][0]
    assert after['rule_execution'] == before['rule_execution']
    assert after['deterministic_checks_executed'] is True
    assert after['annotations'][0]['layer'] == 'manual_or_model_annotation'
    assert signals(after['findings'],'duplicate_row')
    assert signals(after['findings'],'row_annotation')
    assert not annotated['_tool_outcome']['artifacts']


def test_tool_rejects_invalid_operator_before_loading_source(monkeypatch):
    from src.research import tools
    monkeypatch.setattr(tools,'get_request_tables',lambda:[{'name':'a.csv','data':b'a,b\n1,2'}])
    monkeypatch.setattr(tools,'_load_frame',lambda *a,**kw:pytest.fail('invalid configuration reached parser'))
    with pytest.raises(ValueError,match='invalid_operator_config'):
        tools.audit_uploaded_data.func(checks_json={'relations':[{'left':'a','op':'unknown','right':'b'}]})


def test_workbook_receipts_and_annotations_remain_distinct():
    from src.core.data_audit_xlsx import annotate_workbook_multi
    wb = Workbook()
    wb.active.title='data'
    for row in [['v'],[1],[2],[3]]:
        wb.active.append(row)
    out=io.BytesIO(); wb.save(out)
    findings,rules=scan(pd.DataFrame({'v':[1,2,3]}), values=['v'],
        pattern_checks={'adjacent_equal_differences':True,'order_confirmed':True})
    findings.append({'check':'row_annotation','row':2,'field':'v','level':'ok','detail':'synthetic opinion'})
    data, _ = annotate_workbook_multi(out.getvalue(), targets=[{'sheet_name':'data','header_row':1,
        'findings':findings,'row_count':3,'column_count':1}], rule_catalog=[{'sheet':'data',**r} for r in rules.values()])
    exported=load_workbook(io.BytesIO(data))
    assert exported['data']['A2'].comment is not None
    assert exported['data']['A2'].fill.fgColor.rgb.endswith('FDE2E1')
    headers=[c.value for c in exported['审查规则'][1]]
    assert '实际检查数' in headers and '方法版本' in headers
    layers={row[12].value for row in list(exported['问题清单'].rows)[1:]}
    assert layers == {'deterministic','manual_or_model_annotation'}
    exported.close()


@pytest.mark.parametrize('operator', ['!=', [], None, {}])
def test_invalid_operator_is_receipted_even_for_an_empty_source(operator):
    _, rules = scan(pd.DataFrame(columns=['a', 'b']),
                    relations=[{'left': 'a', 'op': operator, 'right': 'b'}])
    assert rules['logic_conflict']['status'] == 'invalid_input'
    assert rules['logic_conflict']['tested'] == 0


@pytest.mark.parametrize('operator', [[], None, {}])
def test_tool_rejects_nonstring_operators_before_loading(operator, monkeypatch):
    from src.research import tools
    monkeypatch.setattr(tools, 'get_request_tables', lambda: [{'name': 'synthetic.csv', 'data': b'a,b\n1,2\n'}])
    monkeypatch.setattr(tools, '_load_frame', lambda *a, **kw: pytest.fail('invalid operator reached parser'))
    with pytest.raises(ValueError, match='invalid_operator_config'):
        tools.audit_uploaded_data.func(checks_json={'relations': [{'left': 'a', 'op': operator, 'right': 'b'}]})


def test_integer_summary_invalid_values_and_missing_precision_have_distinct_receipts():
    _, invalid = scan(pd.DataFrame({'mean': ['1.25'], 'n': ['not-a-count']}),
                      mapping={'mean': 'mean', 'n': 'n'}, integer_data_confirmed=True)
    assert invalid['integer_mean_inconsistent']['status'] == 'invalid_input'
    _, skipped = scan(pd.DataFrame({'mean': [1.25], 'n': [4]}),
                      mapping={'mean': 'mean', 'n': 'n'}, integer_data_confirmed=True)
    assert skipped['integer_mean_inconsistent']['status'] == 'skipped'
    assert skipped['integer_mean_inconsistent']['reasons'] == {'reported precision unavailable': 1}


def test_null_range_bound_is_not_reported_as_an_executed_comparison():
    _, rules = scan(pd.DataFrame({'v': [1, 2, 3]}), ranges={'v': {'min': None}})
    assert rules['out_of_range']['status'] == 'invalid_input'
    assert rules['out_of_range']['tested'] == 0


def test_docx_supplement_discovery_is_independent_of_a_wide_first_table():
    import zipfile
    from src.research.tools import _audit_supplement_tables
    def row(values):
        return '<w:tr>' + ''.join(f'<w:tc><w:p><w:r><w:t>{v}</w:t></w:r></w:p></w:tc>' for v in values) + '</w:tr>'
    wide = '<w:tbl>' + row(['x']*201) + '</w:tbl>'
    usable = '<w:tbl>' + row(['value', 'label']) + row(['1', 'a']) + row(['2', 'b']) + '</w:tbl>'
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + wide + usable + '</w:body></w:document>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr('word/document.xml', xml)
    _, coverage = _audit_supplement_tables([('synthetic.docx', out.getvalue())])
    assert [(c['sheet'], c['status']) for c in coverage] == [
        ('synthetic.docx!t1', 'failed'), ('synthetic.docx!t2', 'executed')]
    assert coverage[1]['tested'] == 2


def test_pdf_supplement_discovery_is_independent_of_a_wide_first_table(monkeypatch):
    from src.research.tools import _audit_supplement_tables
    class Page:
        def extract_tables(self):
            return [[['x']*201, ['1']*201], [['value', 'label'], ['1', 'a'], ['2', 'b']]]
    class Pdf:
        pages = [Page()]
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
    monkeypatch.setattr('pdfplumber.open', lambda *_a, **_kw: Pdf())
    _, coverage = _audit_supplement_tables([('synthetic.pdf', b'%PDF-synthetic')])
    assert [(c['sheet'], c['status']) for c in coverage] == [
        ('synthetic.pdf!p1_t1', 'failed'), ('synthetic.pdf!p1_t2', 'executed')]
    assert coverage[1]['tested'] == 2


def test_supplement_discovery_limit_and_failed_sources_are_visible(monkeypatch):
    from src.research import tools
    class Page:
        def extract_tables(self):
            return [[['v'], ['1']], [['v'], ['2']]]
    class Pdf:
        pages = [Page()]
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
    monkeypatch.setattr('pdfplumber.open', lambda *_a, **_kw: Pdf())
    monkeypatch.setattr(tools, '_MAX_EXTRACTED_TABLES', 1)
    _, coverage = tools._audit_supplement_tables([
        ('bad.xlsx', b'not-a-workbook'), ('synthetic.pdf', b'%PDF-synthetic')])
    assert coverage[0]['status'] == 'failed' and len(coverage[0]['source_sha256']) == 64
    assert any(c.get('reason') == 'source_discovery_limit' and c['tested'] == 0 for c in coverage)
    assert any(c['sheet'] == 'synthetic.pdf!p1_t1' and c['tested'] == 1 for c in coverage)


def test_supplement_report_cap_does_not_prevent_scanning_later_sources():
    from src.research.tools import _audit_supplement_tables
    findings, coverage = _audit_supplement_tables([
        ('first/data.csv', b'v,label\n1,a\n1,a\n1,a\n'),
        ('second/data.csv', b'v,label\n2,b\n2,b\n2,b\n')], max_findings=1)
    assert len(findings) == 1
    assert [c['file'] for c in coverage] == ['first/data.csv', 'second/data.csv']
    assert all(c['tested'] == 3 and c['status'] == 'truncated' for c in coverage)
    assert all(c['report_findings_omitted'] > 0 for c in coverage)
    assert all(c['rule_execution']['rules']['duplicate_row']['tested'] == 3 for c in coverage)


def test_supplement_workbook_contains_actual_rule_receipts_and_failed_loading():
    from src.research.tools import _audit_supplement_tables, _write_audit_xlsx
    findings, coverage = _audit_supplement_tables([
        ('bad.xlsx', b'not-a-workbook'), ('usable.csv', b'v,label\n1,a\n2,b\n')])
    content = _write_audit_xlsx('synthetic.xlsx', {'scan_coverage': coverage}, findings)
    book = load_workbook(io.BytesIO(content))
    rows = list(book['实际执行凭证'].values)
    assert any(r[0] == 'bad.xlsx' and r[2:4] == ('source_loading', 'failed') for r in rows[1:])
    assert any(r[0] == 'usable.csv' and r[2:4] == ('duplicate_row', 'executed') and r[5] == 2 for r in rows[1:])
    book.close()


def test_explicit_errors_and_exploration_keep_severity_in_workbook():
    from src.core.data_audit_xlsx import annotate_workbook
    frame = pd.DataFrame({'ID':['a','a','c','d','e','f','g','h','i'], 'Group':['A']*9,
                          'value':[1,2,None,4,5,6,7,8,100], 'Note':[None]*8+['ok']})
    findings, _ = scan(frame, groups=['Group'], values=['value'], required_fields=['value'],
                       key_columns=['ID'], ranges={'value':{'min':0,'max':50}})
    for check in ('missing_cell','duplicate_key','out_of_range'):
        assert signals(findings,check) and all(f['severity']=='error' for f in signals(findings,check))
    assert all(f['severity']=='review' for f in signals(findings,'iqr_outlier'))
    assert not [f for f in signals(findings,'missing_cell') if f['field']=='Note']
    assert signals(findings,'missing_values')[0]['severity']=='info'
    wb=Workbook(); wb.active.title='data'; wb.active.append(list(frame.columns))
    for row in frame.astype(object).where(frame.notna(),None).itertuples(index=False,name=None):
        wb.active.append(row)
    out=io.BytesIO(); wb.save(out)
    content,_=annotate_workbook(out.getvalue(),sheet_name='data',header_row=1,findings=findings)
    book=load_workbook(io.BytesIO(content))
    rows=list(book['问题清单'].values)
    items=[dict(zip(rows[0],r)) for r in rows[1:]]
    assert all(r['严重程度']=='错误' for r in items if r['检测规则'] in {'missing_cell','duplicate_key','out_of_range'})
    assert all(r['严重程度']=='提示' for r in items if r['检测规则']=='missing_values')
    assert '[错误]' in book['data']['C4'].comment.text
    assert all(book['data'].cell(r,4).comment is None for r in range(2,11))
    book.close()


@pytest.mark.parametrize('checks', [
    {'group_columns':['Group']}, {'column_mapping':{'Group':'Group','values':['value']}},
    {'pattern_checks':{'made_up_check':True}}, {'ranges':{'value':{'minimum':0}}},
    {'groups':'Group'}, {'values':[42]},
])
def test_unrecognized_audit_configuration_never_silently_scans(checks,monkeypatch):
    from src.research import tools
    monkeypatch.setattr(tools,'get_request_tables',lambda:[{'name':'a.csv','data':b'value\n1\n'}])
    monkeypatch.setattr(tools,'_load_frame',lambda *a,**kw:pytest.fail('invalid configuration reached source'))
    with pytest.raises(ValueError,match='invalid_input'):
        tools.audit_uploaded_data.func(checks_json=checks)


def test_missing_group_does_not_apply_an_incomplete_grouping():
    findings,rules=scan(pd.DataFrame({'Group':['A']*9,'value':[1,2,3,4,5,6,7,8,100]}),
                        groups=['Group','absent'],values=['value'])
    assert not signals(findings,'iqr_outlier')
    assert rules['invalid_group_mapping']['status']=='invalid_input'


def test_explicit_numeric_range_reports_bad_tokens_even_below_numeric_gate():
    findings,_=scan(pd.DataFrame({'value':['bad','also bad',1]}),ranges={'value':{'min':0,'max':10}})
    assert len(signals(findings,'non_numeric_cell'))==2
    assert all(f['severity']=='error' for f in signals(findings,'non_numeric_cell'))


from src.research.integrity_integration_test import workspace  # noqa: E402,F401


def audit_fixture(workspace):
    wb=Workbook(); wb.active.title='data'; wb.active.append(['ID','Group','value','Note'])
    for i,value in enumerate([1,2,3,4,5,6,7,8,100],1):
        wb.active.append(['duplicate' if i in (1,2) else str(i),'A',value,None if i<9 else 'text'])
    wb.active['C4']=None
    out=io.BytesIO(); wb.save(out)
    upload=workspace.upload([('synthetic.xlsx',out.getvalue())])[0]
    checks={'groups':['Group'],'values':['value'],'required_fields':['value'],'key_columns':['ID'],
            'ranges':{'value':{'min':0,'max':50}}}
    return upload,checks


def test_one_scan_can_deliver_all_requested_formats_and_reuse_the_review(workspace,monkeypatch):
    from src.research import tools,delivery_tools,integrity_review
    upload,checks=audit_fixture(workspace)
    calls=[]; original=tools._audit_frame
    def counted(*a,**kw):
        calls.append(1)
        return original(*a,**kw)
    monkeypatch.setattr(tools,'_audit_frame',counted)
    text,result=tools.audit_uploaded_data.func(checks_json=checks,publish_formats=['xlsx','html','json'])
    assert result['_tool_outcome']['status']=='complete',text
    assert len(calls)==1
    assert len(result['_tool_outcome']['artifacts'])==3
    rid=result['integrity_review']['review_id']
    saved=integrity_review.get_review(101,rid)['report']['table_results'][0]
    assert saved['findings']==result['results'][0]['findings']
    json_receipt=next(r for r in result['publication_receipts'] if r['filename'].endswith('.json'))
    import json
    payload=json.loads(workspace.resolve(json_receipt['url'])['path'].read_bytes())
    assert payload['review_id']==rid
    missing=next(f for f in payload['issues'] if f['category']=='missing_cell')
    assert (missing['sheet'],missing['cell'],missing['severity'])==('data','C4','error')
    assert not [f for f in payload['issues'] if f['category']=='design_checks_skipped']
    assert [f for f in payload['issues'] if f['category']=='iqr_outlier']
    assert payload['table_results'][0]['coverage']['processed_rows']==9
    assert workspace.files.get_account_file(upload['file_id'],101)['path'].read_bytes()==upload['data']
    monkeypatch.setattr(tools,'_audit_frame',lambda *a,**kw:pytest.fail('replayed audit'))
    monkeypatch.setattr(tools,'get_request_tables',lambda:pytest.fail('looked for current uploads'))
    for fmt in ('html','json','xlsx'):
        text,delivery=delivery_tools.deliver_research_artifact.func(kind='data_audit',review_id=rid,output_format=fmt)
        assert delivery['_tool_outcome']['status']=='complete',text
        assert delivery['result_refs']==[{'kind':'integrity_review','review_id':rid}]
        for receipt in delivery['receipts']:
            assert receipt['file_id'] and receipt['sha256'] and receipt['size_bytes']>0


@pytest.mark.parametrize('fmt',['html','json'])
def test_explicit_audit_format_does_not_render_unrequested_xlsx(workspace,monkeypatch,fmt):
    from src.research import tools
    from src.core import data_audit_xlsx
    audit_fixture(workspace)
    monkeypatch.setattr(data_audit_xlsx,'annotate_workbook_multi',lambda *a,**kw:pytest.fail('unrequested XLSX'))
    text,result=tools.audit_uploaded_data.func(publish=True,publish_formats=[fmt])
    assert result['_tool_outcome']['status']=='complete',text
    assert len(result['_tool_outcome']['artifacts'])==1
    assert result['publication_receipts'][0]['filename'].endswith('.'+fmt)


def test_failed_workbook_publication_preserves_authoritative_scan_for_retry(workspace,monkeypatch):
    from src.research import tools,delivery_tools,integrity_review
    _,checks=audit_fixture(workspace)
    save=tools._save_artifact
    monkeypatch.setattr(tools,'_save_artifact',lambda *a,**kw:(_ for _ in ()).throw(OSError('synthetic publication failure')))
    text,result=tools.audit_uploaded_data.func(checks_json=checks,publish=True)
    assert result['_tool_outcome']['status']=='partial',text
    assert result['results'][0]['findings'] and result['results'][0]['publish_status']=='failed'
    rid=result['integrity_review']['review_id']
    assert integrity_review.get_review(101,rid)['report']['table_results'][0]['findings']
    monkeypatch.setattr(tools,'_save_artifact',save)
    monkeypatch.setattr(tools,'_audit_frame',lambda *a,**kw:pytest.fail('replayed scan'))
    text,delivery=delivery_tools.deliver_research_artifact.func(kind='data_audit',review_id=rid,output_format='xlsx')
    assert delivery['_tool_outcome']['status']=='complete',text


def test_review_delivery_rejects_foreign_sessions_and_changed_workbook(workspace,monkeypatch):
    from src.research import tools,delivery_tools
    from src.core import tracing
    from src.session.manager import session_manager
    upload,checks=audit_fixture(workspace)
    _,result=tools.audit_uploaded_data.func(checks_json=checks)
    rid=result['integrity_review']['review_id']
    session_manager._create_session_sync('other-synthetic-session',101)
    tracing.set_session_id('other-synthetic-session')
    with pytest.raises(LookupError):
        delivery_tools.deliver_research_artifact.func(kind='data_audit',review_id=rid,output_format='json')
    tracing.set_session_id(workspace.session_id); tracing.set_user_id(202)
    with pytest.raises(LookupError):
        delivery_tools.deliver_research_artifact.func(kind='data_audit',review_id=rid,output_format='json')
    tracing.set_user_id(101)
    source=workspace.files.get_account_file(upload['file_id'],101)
    source['path'].write_bytes(b'changed source')
    with pytest.raises(ValueError,match='哈希|不可用|变化'):
        delivery_tools.deliver_research_artifact.func(kind='data_audit',review_id=rid,output_format='xlsx')
    # Persisted evidence remains exportable, without pretending to read a new source version.
    text,exported=delivery_tools.deliver_research_artifact.func(kind='data_audit',review_id=rid,output_format='json')
    assert exported['_tool_outcome']['status']=='complete',text


@pytest.fixture
def audit_event_loop():
    # Windows creates the loop's local socketpair before the workspace's network guard.
    import asyncio
    with asyncio.Runner() as runner:
        runner.get_loop()
        yield runner


@pytest.mark.parametrize('args', [
    {'checks_json':{'group_by':['Group']}},
    {'checks_json':{'column_mapping':{'Group':'Group'}}},
    {'publish_formats':['pdf']},
    {'file_ids_json':['latest']},
    {'file_ids_json':['a'*32,'a'*32]},
])
def test_registry_rejects_audit_configuration_before_dispatch(audit_event_loop,workspace,monkeypatch,args):
    from src.research import tools
    from src.tools import registry
    monkeypatch.setattr(registry,'_TOOL_SPECS',{})
    registry.register_tool(tools.audit_uploaded_data,is_readonly=False,write_scope='session_artifact',
                           preflight=tools.validate_audit_request)
    monkeypatch.setattr(tools,'get_request_tables',lambda:pytest.fail('preflight read uploads'))
    monkeypatch.setattr(tools,'_save_artifact',lambda *a,**kw:pytest.fail('preflight published a file'))
    dispatched=[]
    message=audit_event_loop.run(registry._run_single_tool(tools.audit_uploaded_data,
        {'id':'invalid-audit-config','name':'audit_uploaded_data','args':args},
        on_dispatch=lambda:dispatched.append(True)))
    assert message.artifact['_tool_outcome']['status']=='not_run'
    assert message.artifact['_tool_outcome']['reason_code']=='invalid_tool_arguments'
    assert message.additional_kwargs.get('execution_dispatched') is False
    assert not dispatched
    assert workspace.files.list_files(workspace.session_id,101)==[]


@pytest.mark.parametrize('args', [
    {'file_ids_json':['latest']}, {'file_ids_json':['a'*32,'a'*32]},
    {'file_index':-1}, {'header_rows_json':[0]}, {'header_rows_json':['1']},
])
def test_registry_rejects_inspection_options_before_source_access(audit_event_loop,workspace,monkeypatch,args):
    from src.research import tools
    from src.tools import registry
    monkeypatch.setattr(registry,'_TOOL_SPECS',{})
    registry.register_tool(tools.inspect_uploaded_table,is_readonly=True,
                           preflight=tools.validate_inspection_request)
    monkeypatch.setattr(tools,'_audit_source_tables',lambda *a:pytest.fail('invalid inspection read a source'))
    dispatched=[]
    message=audit_event_loop.run(registry._run_single_tool(tools.inspect_uploaded_table,
        {'id':'invalid-inspection','name':'inspect_uploaded_table','args':args},
        on_dispatch=lambda:dispatched.append(True)))
    assert message.artifact['_tool_outcome']['status']=='not_run'
    assert message.artifact['_tool_outcome']['reason_code']=='invalid_tool_arguments'
    assert message.additional_kwargs.get('execution_dispatched') is False
    assert not dispatched


def test_account_file_ids_allow_cross_turn_new_rules_and_keep_old_review(workspace,monkeypatch):
    import json
    from src.core import tracing
    from src.research import tools,integrity_review,delivery_tools
    from src.research.context import set_request_tables,reset_request_tables
    upload,checks=audit_fixture(workspace)
    initial={**checks,'ranges':{'value':{'max':500}}}
    _,first=tools.audit_uploaded_data.func(checks_json=initial)
    first_id=first['integrity_review']['review_id']
    before=integrity_review.get_review(101,first_id)['report']['table_results'][0]
    token=set_request_tables([])
    try:
        tracing.set_client_request_id('synthetic-next-turn')
        profile=json.loads(tools.inspect_uploaded_table.func(file_ids_json=[upload['file_id']]))
        assert profile['source_ref']['file_id']==upload['file_id']
        assert profile['source_ref']['sha256']==before['source_sha256']
        assert profile['source_selection']=='account_file_ids'
        text,second=tools.audit_uploaded_data.func(file_ids_json=[upload['file_id']],checks_json=checks)
        assert second['_tool_outcome']['status']=='complete',text
        after=second['results'][0]
        assert signals(after['findings'],'out_of_range')
        assert not signals(before['findings'],'out_of_range')
        assert after['source_file_id']==upload['file_id']
        assert after['source_sha256']==before['source_sha256']
        assert after['source_selection']=='account_file_ids'
        assert second['integrity_review']['review_id']!=first_id
        assert integrity_review.get_review(101,first_id)['report']['table_results'][0]==before
        monkeypatch.setattr(tools,'_audit_frame',lambda *a,**kw:pytest.fail('export replayed audit'))
        text,delivery=delivery_tools.deliver_research_artifact.func(kind='data_audit',review_id=first_id,output_format='json')
        assert delivery['_tool_outcome']['status']=='complete',text
        payload=json.loads(workspace.resolve(delivery['receipts'][0]['url'])['path'].read_bytes())
        assert payload['table_results'][0]['findings']==before['findings']
    finally:
        reset_request_tables(token)


def test_explicit_account_sources_do_not_merge_current_or_other_history(workspace):
    import json
    from src.research import tools
    from src.research.context import set_request_tables,reset_request_tables
    uploads=workspace.upload([('same.csv',b'v,label\n1,first\n2,second\n'),
                              ('same.csv',b'v,label\n90,third\n91,fourth\n'),
                              ('extra.csv',b'v\n999\n')])
    ids=[uploads[0]['file_id'],uploads[1]['file_id']]
    token=set_request_tables([uploads[2]])
    try:
        profile=json.loads(tools.inspect_uploaded_table.func(file_ids_json=ids,file_index=1))
        assert profile['source_ref']['file_id']==ids[1]
        _,selected=tools.audit_uploaded_data.func(file_ids_json=ids,file_indexes_json=[1],
                                                checks_json={'ranges':{'v':{'max':5}}})
        assert len(selected['results'])==1
        item=selected['results'][0]
        assert item['file_index']==1 and item['source_file_id']==ids[1]
        assert len(signals(item['findings'],'out_of_range'))==2
        # Empty IDs preserve attachment-index behavior and select only the actual current attachment.
        _,current=tools.audit_uploaded_data.func(file_ids_json=[],file_indexes_json=[0])
        assert current['results'][0]['source_file_id']==uploads[2]['file_id']
        assert current['results'][0]['source_selection']=='current_turn_attachments'
    finally:
        reset_request_tables(token)


def test_no_reference_returns_not_run_without_choosing_history(workspace,monkeypatch):
    import json
    from src.research import tools
    from src.research.context import set_request_tables,reset_request_tables
    audit_fixture(workspace)
    token=set_request_tables([])
    monkeypatch.setattr(tools,'_load_frame_from_bytes',lambda *a,**kw:pytest.fail('history guessed'))
    try:
        profile=json.loads(tools.inspect_uploaded_table.func())
        assert profile['_tool_outcome']['status']=='not_run'
        assert profile['_tool_outcome']['reason_code']=='source_reference_required'
        _,audit=tools.audit_uploaded_data.func()
        assert audit['_tool_outcome']['status']=='not_run'
        assert 'file_ids_json' in audit['next_action']
        assert audit['audit_results']==[]
    finally:
        reset_request_tables(token)


@pytest.mark.parametrize('mutation',['owner','hash','declared_size','actual_size','missing'])
def test_account_audit_source_integrity_is_checked_before_parser(workspace,monkeypatch,mutation):
    import json,sqlite3
    from src.core import tracing
    from src.research import tools
    upload,_=audit_fixture(workspace)
    entry=workspace.files.get_account_file(upload['file_id'],101)
    if mutation=='owner':
        tracing.set_user_id(202)
    elif mutation=='hash':
        raw=entry['path'].read_bytes()
        entry['path'].write_bytes(bytes([raw[0]^1])+raw[1:])
    elif mutation=='declared_size':
        with sqlite3.connect(workspace.files.DB_PATH) as conn:
            conn.execute('UPDATE session_files SET size_bytes=? WHERE file_id=?',
                         (tools._AUDIT_SOURCE_MAX_BYTES+1,upload['file_id']))
    elif mutation=='actual_size':
        monkeypatch.setattr(tools,'_AUDIT_SOURCE_MAX_BYTES',len(upload['data']))
        with entry['path'].open('ab') as handle:
            handle.write(b'x')
    else:
        entry['path'].unlink()
    monkeypatch.setattr(tools,'_load_frame_from_bytes',lambda *a,**kw:pytest.fail('unverified source reached parser'))
    expected={'owner':'source_unavailable','hash':'source_changed','declared_size':'source_size_limit',
              'actual_size':'source_size_limit','missing':'source_unavailable'}[mutation]
    _,audit=tools.audit_uploaded_data.func(file_ids_json=[upload['file_id']])
    profile=json.loads(tools.inspect_uploaded_table.func(file_ids_json=[upload['file_id']]))
    for result in (audit,profile):
        assert result['_tool_outcome']['status']=='not_run'
        assert result['_tool_outcome']['reason_code']==expected
        assert not result['_tool_outcome']['artifacts']


@pytest.mark.parametrize('ids',['latest',['same.csv'],['a'*32,'a'*32],['a'*32]*21,[[1]]])
def test_account_file_ids_reject_aliases_duplicates_and_invalid_shapes(monkeypatch,ids):
    from src.research import tools
    monkeypatch.setattr(tools,'get_request_tables',lambda:pytest.fail('invalid IDs reached sources'))
    with pytest.raises(ValueError):
        tools.validate_audit_request(file_ids_json=ids)
    with pytest.raises(ValueError):
        tools.audit_uploaded_data.func(file_ids_json=ids)


def test_account_source_uses_shared_bounded_parser_and_validates_indexes(workspace,monkeypatch):
    import json
    from src.research import tools
    upload,_=audit_fixture(workspace)
    original=tools._load_frame_from_bytes
    called=[]
    def parser(data,name,**kwargs):
        called.append((data,name))
        return original(data,name,**kwargs)
    monkeypatch.setattr(tools,'_load_frame_from_bytes',parser)
    profile=json.loads(tools.inspect_uploaded_table.func(file_ids_json=[upload['file_id']]))
    assert profile['rows']==9 and called==[(upload['data'],'synthetic.xlsx')]
    with pytest.raises(ValueError,match='序号无效'):
        tools.inspect_uploaded_table.func(file_ids_json=[upload['file_id']],file_index=1)
    with pytest.raises(ValueError,match='序号无效'):
        tools.audit_uploaded_data.func(file_ids_json=[upload['file_id']],file_indexes_json=[1])

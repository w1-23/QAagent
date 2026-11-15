"""Offline parity and selective-execution contracts for pure audit methods."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest


def audit_cases():
    frame = pd.DataFrame({
        'group': ['A'] * 16 + ['B'] * 16,
        'value': list(range(15)) + [100] + list(range(15)) + [200],
        'offset': [x + 5 for x in range(32)],
        'ratio': [x * 3 for x in range(32)],
        'base': list(range(32)),
        'same': list(range(32)),
        'constant': [1] * 32,
        'key': list(range(31)) + [30],
        'Note': [None] * 31 + ['remark'],
        'required_empty': [None] * 32,
        'mean': [0.27] * 32,
        'n': [4] * 32,
        'sd': [0.1] * 32,
        'mixed': ['unit'] + list(range(31)),
    })
    frame = pd.concat([frame, frame.iloc[[1]]], ignore_index=True)
    kwargs = {'mapping': {'mean': 'mean', 'n': 'n', 'sd': 'sd'},
              'groups': ['group'], 'values': ['value'], 'row_offset': 5,
              'integer_data_confirmed': True, 'sd_kind': 'sample',
              'mean_decimals': 2, 'sd_decimals': 1,
              'required_fields': ['required_empty', 'absent'],
              'key_columns': ['key'], 'ranges': {'value': {'min': 2, 'max': 50}},
              'relations': [{'left': 'value', 'op': '<=', 'right': 'base'}]}
    return [
        (frame, kwargs),
        (pd.DataFrame({'g': ['x'] * 4 + ['y'] * 4, 'v': list(range(8)),
                       'another': ['unit'] + list(range(7))}),
         {'mapping': {}, 'groups': ['g'], 'values': ['v']}),
        (pd.DataFrame({'a': [1, None, None, 1], '备注': [None, 'ok', None, None]}),
         {'mapping': {}, 'groups': [], 'values': [], 'optional_fields': ['a']}),
        (pd.DataFrame(columns=['a']), {'mapping': {}, 'groups': [], 'values': []}),
    ]


def digest(findings, issue_rows, receipt):
    value = [findings, {str(k): sorted(v) for k, v in issue_rows.items()}, receipt]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), default=str).encode()).hexdigest()


# Captured from the original tools._audit_frame before replacing its body.
LEGACY_DIGESTS = [
    'b792a3e940f78c42d4cfafe523906afe5efabed01456f76706f858c9c3487e70',
    '13aa91e33e9538ee59a838a48e99e0ec4f0107484d3c46e9ffa59af92938cbf5',
    '3a198f89cbe02979bb4225c885000d7e52ff23de22f5999e74baed94cb53df0d',
    '23ec775d9332f687c89537c48fca6c2ef93ba3e094431b1e5fb4902cdd602fb8',
]


@pytest.mark.parametrize('case_index', range(4))
def test_full_scan_matches_original_algorithm(case_index):
    from src.research import audit_methods
    from src.research import tools
    frame, kwargs = audit_cases()[case_index]
    original = frame.copy(deep=True)
    receipt = {}
    findings, rows = audit_methods.audit_frame(frame, **kwargs, execution_receipt=receipt)
    # Existing findings and summary-rule evidence remain unchanged; numeric
    # coverage is newly explicit and is asserted by the boundary tests below.
    legacy_receipt = {key: value for key, value in receipt.items() if key != 'numeric_forensics'}
    assert digest(findings, rows, legacy_receipt) == LEGACY_DIGESTS[case_index]
    other_receipt = {}
    assert tools._audit_frame(frame, **kwargs, execution_receipt=other_receipt) == (findings, rows)
    assert other_receipt == receipt
    pd.testing.assert_frame_equal(frame, original)


def test_numeric_column_budget_excludes_text_labels():
    from src.research.audit_methods import audit_frame
    frame = pd.DataFrame({'label': [f'row{i}' for i in range(6)],
                          **{f'n{i}': list(range(6)) for i in range(30)},
                          'notes': ['note'] * 6})
    receipt = {}
    findings, _ = audit_frame(frame, {}, [], [], execution_receipt=receipt)
    coverage = receipt['numeric_forensics']
    assert coverage['checked_columns'] == [f'n{i}' for i in range(30)]
    assert coverage['unscanned_columns'] == []
    assert any(item['check'] == 'identical_columns' and 'n29' in item['field'] for item in findings)
    frame['extra_numeric'] = list(range(6))
    audit_frame(frame, {}, [], [], execution_receipt=receipt)
    assert receipt['numeric_forensics']['unscanned_columns'] == ['extra_numeric']


def test_numeric_pair_row_limit_is_reported():
    from src.research.audit_methods import audit_frame
    frame = pd.DataFrame({'a': range(20001), 'b': range(20001)})
    receipt = {}
    audit_frame(frame, {}, [], [], execution_receipt=receipt)
    assert receipt['numeric_forensics']['rows_truncated'] is True


def test_filter_selection_explicitly_reports_full_scan(monkeypatch):
    from src.research import audit_methods as methods
    frame, kwargs = audit_cases()[0]
    full, _ = methods.audit_frame(frame, **kwargs)
    calls = []
    implementation = methods.audit_frame
    def record(*args, **values):
        calls.append(True)
        return implementation(*args, **values)
    monkeypatch.setattr(methods, 'audit_frame', record)
    selected = methods.audit_selected_checks(frame, checks=['duplicate_row'], **kwargs)
    assert calls == [True]
    assert selected['findings'] == [x for x in full if x['check'] == 'duplicate_row']
    assert selected['execution']['mode'] == 'full_scan_result_filter'
    assert selected['execution']['selection_affects_execution'] is False
    assert selected['execution']['returned_checks'] == ['duplicate_row']
    assert selected['execution']['full_findings_count'] == len(full)
    assert selected['execution']['rule_applicability'] == 'original_per_rule_conditions'
    assert all(checks == {'duplicate_row'} for checks in selected['issue_rows'].values())


@pytest.mark.parametrize('checks', [['unknown_rule'], 'duplicate_row', [1]])
def test_filter_selection_rejects_unknown_or_malformed_checks(checks):
    from src.research import audit_methods
    with pytest.raises(ValueError):
        audit_methods.audit_selected_checks(pd.DataFrame({'a': [1]}), checks=checks,
            mapping={}, groups=[], values=[])


def test_duplicate_minimal_method_reuses_shared_impl_and_skips_total_scan(monkeypatch):
    from src.research import audit_methods as methods
    frame, kwargs = audit_cases()[0]
    expected, _ = methods.audit_frame(frame, **kwargs)
    def forbidden(*args, **kwargs):
        raise AssertionError('Full audit must not execute')
    monkeypatch.setattr(methods, 'audit_frame', forbidden)
    findings, rows = methods.audit_duplicates(frame, key_columns=['key'], row_offset=5)
    assert findings == [x for x in expected if x['check'] in {'duplicate_key', 'duplicate_row'}]
    assert all(checks <= {'duplicate_key', 'duplicate_row'} for checks in rows.values())
    key_only, _ = methods.audit_duplicates(frame, key_columns=['key'], row_offset=5, checks=['duplicate_key'])
    assert key_only == [x for x in findings if x['check'] == 'duplicate_key']
    monkeypatch.setattr(pd.DataFrame, 'duplicated', forbidden)
    # A key-only execution must not scan entire rows for duplicates.
    methods.audit_duplicates(frame, key_columns=['key'], checks=['duplicate_key'])


def test_group_iqr_minimal_method_has_exact_legacy_findings_and_no_total_scan(monkeypatch):
    from src.research import audit_methods as methods
    frame, kwargs = audit_cases()[0]
    expected, _ = methods.audit_frame(frame, **kwargs)
    def forbidden(*args, **kwargs):
        raise AssertionError('Full audit must not execute')
    monkeypatch.setattr(methods, 'audit_frame', forbidden)
    findings, rows = methods.audit_group_outliers(frame, groups=['group'], values=['value'], row_offset=5)
    assert findings == [x for x in expected if x['check'] in {'iqr_outlier', 'iqr_insufficient'}]
    assert len(findings) == 2
    assert all(checks == {'iqr_outlier'} for checks in rows.values())


def test_group_iqr_original_gates_remain_unchanged():
    from src.research import audit_methods as methods
    frame, kwargs = audit_cases()[1]
    expected, _ = methods.audit_frame(frame, **kwargs)
    findings, _ = methods.audit_group_outliers(frame, groups=['g'], values=['v'])
    assert findings == [x for x in expected if x['check'] in {'iqr_outlier', 'iqr_insufficient'}]
    assert findings[0]['check'] == 'iqr_insufficient'
    assert methods.audit_group_outliers(frame, groups=[], values=['v']) == ([], {})
    assert methods.audit_group_outliers(frame, groups=['absent'], values=['v']) == ([], {})
    assert methods.audit_group_outliers(frame, groups=['g'], values=[]) == ([], {})
    small = frame.iloc[:7]
    assert methods.audit_group_outliers(small, groups=['g'], values=['v']) == ([], {})


def test_minimal_methods_handle_empty_tables_without_running_unrelated_checks():
    from src.research import audit_methods as methods
    for frame in (pd.DataFrame(columns=['a']), pd.DataFrame(index=[0, 1])):
        assert methods.audit_duplicates(frame) == ([], {})
        assert methods.audit_group_outliers(frame, groups=['g'], values=['v']) == ([], {})


def test_minimal_iqr_parses_only_selected_measurement_columns(monkeypatch):
    from src.research import audit_methods as methods
    frame, _ = audit_cases()[0]
    implementation = pd.to_numeric
    columns = []
    def record(series, **kwargs):
        columns.append(series.name)
        return implementation(series, **kwargs)
    monkeypatch.setattr(pd, 'to_numeric', record)
    methods.audit_group_outliers(frame, groups=['group'], values=['value'])
    assert columns == ['value']


def test_coverage_notices_survive_result_filtering_on_an_empty_table():
    from src.research import audit_methods as methods
    result = methods.audit_selected_checks(pd.DataFrame(columns=['a']), checks=['duplicate_row'])
    assert result['findings'] == []
    assert result['execution']['pipeline_scope'] == 'empty_table_only'
    assert result['execution']['coverage_notices'][0]['check'] == 'empty_table'
    assert result['execution']['individual_rule_execution'] == 'not_fully_instrumented'


def test_empty_selection_still_runs_full_scan_and_honestly_returns_no_selected_findings():
    from src.research import audit_methods as methods
    frame, kwargs = audit_cases()[0]
    result = methods.audit_selected_checks(frame, checks=[], **kwargs)
    assert result['findings'] == []
    assert result['issue_rows'] == {}
    assert result['execution']['full_findings_count'] > 0
    assert result['execution']['selection_affects_execution'] is False


def test_pure_module_import_has_no_service_storage_or_network_dependencies():
    source_root = Path(__file__).resolve().parents[2]
    code = '''import sys
sys.path.insert(0, sys.argv[1])
from src.research import audit_methods
import pandas as pd
frame=pd.DataFrame({'g':['A']*8,'v':range(8)})
audit_methods.audit_frame(frame,{},['g'],['v'])
for module in ('src.config','src.auth','src.session','src.research.tools','sqlite3','requests','langchain_core'):
    assert module not in sys.modules, module
'''
    result = subprocess.run([sys.executable, '-I', '-c', code, str(source_root)],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr

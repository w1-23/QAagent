"""Synthetic engineering examples, not observations from a research study."""
import math

import pandas as pd
import pytest

from src.research.summary_consistency import check_integer_summary


@pytest.mark.parametrize('values', [[0, 1, 1], [1, 2, 4, 8], [-3, -1, 2], [0, 0, 0], [1]])
@pytest.mark.parametrize('places', [0, 1, 2, 3])
def test_valid_integer_samples_survive_report_rounding(values, places):
    mean = sum(values) / len(values)
    sd = math.sqrt(sum((value - mean)**2 for value in values) / len(values))
    checks = check_integer_summary(f'{mean:.{places}f}', len(values), f'{sd:.{places}f}', sd_kind='population')
    assert all(item['status'] == 'checked' and not item['inconsistent'] for item in checks)


def test_fixed_tolerance_false_positive_is_removed():
    # Sum=1 with n=3 can legitimately be reported as mean 0.3.
    result = check_integer_summary('0.3', 3)[0]
    assert result['sum_interval'] == ['0.75', '1.05']
    assert not result['inconsistent']
    assert check_integer_summary('0.27', 4)[0]['inconsistent']


def test_excel_numeric_value_needs_reported_precision():
    assert check_integer_summary(0.27, 4)[0]['status'] == 'not_checked'
    assert check_integer_summary(0.27, 4, mean_decimals=2)[0]['inconsistent']


@pytest.mark.parametrize('mean,n', [('NaN', 3), ('inf', 3), ('1', 0), ('1', 1.2), ('1e999999999', 3)])
def test_invalid_or_unbounded_input_is_not_checked(mean, n):
    assert check_integer_summary(mean, n)[0]['status'] == 'not_checked'


def test_required_empty_column_is_not_skipped():
    from src.research.tools import _audit_frame
    findings, _ = _audit_frame(pd.DataFrame({'required': [None, None], 'notes': [None, None]}),
                              {}, [], [], required_fields=['required'], optional_fields=['required'])
    missing = [item for item in findings if item['check'] == 'missing_cell']
    assert [(item['field'], item['row'], item['severity']) for item in missing] == [
        ('required', 2, 'error'), ('required', 3, 'error')]


def test_execution_receipt_distinguishes_unknown_precision():
    from src.research.tools import _audit_frame
    receipt = {}
    findings, _ = _audit_frame(pd.DataFrame({'mean': [0.27], 'n': [4]}),
                              {'mean': 'mean', 'n': 'n'}, [], [],
                              integer_data_confirmed=True, execution_receipt=receipt)
    assert receipt['integer_mean_inconsistent'] == {'checked_rows': 0, 'skipped_rows': 1}
    assert any(item['check'] == 'integer_summary_not_checked' for item in findings)

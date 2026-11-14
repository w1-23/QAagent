"""Immutable scientific results reach Word/Excel without model retyped numbers."""
import json
from unittest.mock import Mock

import pandas as pd
import pytest

from src.research import analysis_delivery as delivery, analysis_results as results


@pytest.fixture
def result(tmp_path, monkeypatch):
    from src import auth, config
    monkeypatch.setattr(config, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(auth, 'user_by_id', lambda owner: {'active': True})
    snap = results.persist_dataset_snapshot(812345, pd.DataFrame({'value': [1 / 3, 2 / 7]}),
        source={'file_id': 'upload:synthetic-delivery', 'sha256': 'a' * 64}, row_ids=[2, 3])
    return results.persist_analysis_result(812345, snap.ref,
        outputs={'descriptive': {'value': {'mean': 1 / 3, 'n': 2}}, 'test': {'p': 0.050000000001}},
        parameters={'alpha': .05}, design={'experimental_unit': 'plant'},
        exclusions=[], coverage={'rows_used': 2})


def test_table_and_word_project_same_verified_full_precision_snapshot(result):
    table = delivery.result_table(812345, result.ref, 'descriptive')
    blocks, refs = delivery.materialize_word_blocks(812345, [{'type': 'analysis_table',
        'block_id': 'results_table', 'section_id': 'results', 'analysis_result_ref': result.ref,
        'result_section': 'descriptive'}])
    assert ['descriptive.value.mean', 1 / 3] in table['rows']
    assert blocks[0]['rows'] == table['rows']
    assert blocks[0]['type'] == 'table'
    assert result.sha256 in blocks[0]['caption']
    assert refs == [result.ref]


def test_cross_owner_and_tampered_result_refs_fail_before_materialization(result):
    with pytest.raises(LookupError):
        delivery.result_table(812346, result.ref)
    with pytest.raises(ValueError):
        delivery.result_table(812345, {**result.ref, 'sha256': '0' * 64})


def test_analysis_table_cannot_override_computed_rows(result):
    with pytest.raises(ValueError, match='覆盖'):
        delivery.materialize_word_blocks(812345, [{'type': 'analysis_table', 'analysis_result_ref': result.ref,
                                                   'rows': [[999]], 'block_id': 'x', 'section_id': 's'}])
    with pytest.raises(ValueError):
        delivery.result_table(812345, result.ref, 'unknown')


def test_export_table_references_snapshot_and_preserves_actual_numeric_cells(result, monkeypatch):
    from io import BytesIO
    from openpyxl import load_workbook
    from src.core import tracing
    from src.research import tools
    outputs = []
    monkeypatch.setattr(tools, '_save_artifact', lambda name, data, mime, **kw:
                        outputs.append((name, data, mime)) or '/api/artifacts/' + 'b' * 32)
    monkeypatch.setattr(tracing, 'get_user_id', lambda: 812345)
    tools.export_table.func(title='Synthetic result', output_format='xlsx',
                            analysis_result_ref=result.ref, result_section='descriptive')
    book = load_workbook(BytesIO(outputs[0][1]), data_only=True)
    rows = list(book.active.values)
    assert ('analysis_result.id', result.id) in rows
    assert ('analysis_result.sha256', result.sha256) in rows
    numeric = next(row[1] for row in rows if row[0] == 'descriptive.value.mean')
    assert numeric == pytest.approx(1 / 3, rel=1e-15)
    with pytest.raises(Exception, match='覆盖'):
        tools.export_table.func(title='Synthetic result', columns_json=['fake'], rows_json=[[999]],
                                analysis_result_ref=result.ref)

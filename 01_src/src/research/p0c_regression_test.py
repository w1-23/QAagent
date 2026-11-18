"""Offline Linux checks for real parsers, stored attempts and task receipts."""
from contextlib import nullcontext
from hashlib import sha256
from io import BytesIO
import json

from openpyxl import Workbook
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from citrus_audit.inspection import inspect_workbook
from citrus_refcheck.inventory import extract_inventory, digest
from src.core.op_receipt import make_receipt
from src.core.task_result import build_task_result, effective_tool_records
from src.core.turn_outcome import completion_errors, completion_from_answer
from src.research import reference_audit as service


def _facts(*rows, errors=None):
    return {'records': [{**row, 'record_sha256': digest(row)} for row in rows],
            'errors': errors or {}, 'http_evidence': [], 'providers': {}}


def _record(payload, cid):
    receipt = make_receipt('verify_uploaded_references', cid, json.dumps(payload), None)
    return {'name': receipt['tool_name'], 'tool_call_id': cid, 'status': receipt['status'],
            'reason': receipt['reason_code'], 'op_receipt': receipt}


class P0CRegressionTests(unittest.TestCase):
    def test_workbook_layout_and_wide_coordinate_pages(self):
        wb = Workbook()
        sheet = wb.active
        sheet.merge_cells('A1:B1')
        sheet['A1'], sheet['A2'], sheet['B2'], sheet['D2'] = 'group', 'id', 'value', 'other'
        sheet['B3'], sheet['C4'] = '=1+1', '汉' * 9000
        wb.create_sheet('hidden').sheet_state = 'hidden'
        stream = BytesIO()
        wb.save(stream)
        data = stream.getvalue()
        opts = dict(sheet_name=sheet.title, header_rows=[1, 2], cell_range='A1:IV5', page_rows=10, cursor='')
        layout = inspect_workbook(data, 'fixture.xlsx', mode='layout', **opts)
        assert layout['merged_ranges'] == [{'range': 'A1:B1', 'anchor': 'A1'}]
        assert layout['sheet_layout'][1] == {'name': 'hidden', 'visibility': 'hidden'}
        assert 'A2:B2' in layout['observed_blocks'] and 'D2:D2' in layout['observed_blocks']
        assert layout['source_sha256'] == sha256(data).hexdigest()
        cells, cursors = [], set()
        while True:
            page = inspect_workbook(data, 'fixture.xlsx', mode='cells', **opts)
            assert 0 < len(page['cells']) <= 200
            assert len(json.dumps(page, ensure_ascii=False).encode()) <= 12_000
            cells.extend(page['cells'])
            opts['cursor'] = page['next_cursor']
            if not opts['cursor']:
                break
            assert opts['cursor'] not in cursors
            cursors.add(opts['cursor'])
        by_cell = {c['cell']: c for c in cells}
        assert len(cells) == len(by_cell) == 1280
        assert by_cell['B3'] == {'cell': 'B3', 'type': 'f', 'value': '=1+1'}
        assert by_cell['C4']['truncated'] and by_cell['C4']['value_characters'] == 9000
        opts.update(cursor=next(iter(cursors)), cell_range='B1:IV5')
        with self.assertRaisesRegex(ValueError, '游标'):
            inspect_workbook(data, 'fixture.xlsx', mode='cells', **opts)

    def test_delimited_inventory_with_exact_encoding_duplicate_and_bad_rows(self):
        source = '编号\t标题\t年份\r\n10.1234/a\t"上\r\n下"\t2024\r\n10.1234/a\t别名\t2024\r\nshort\r\n'.encode('gb18030')
        column_map = {'doi': '编号', 'title': '标题', 'year': '年份'}
        with self.assertRaisesRegex(ValueError, 'decoding'):
            extract_inventory(name='input.tsv', content=source, column_map=column_map)
        result = extract_inventory(name='input.tsv', content=source, column_map=column_map, encoding='gb18030')
        first, second, bad = result['entries']
        assert result['source']['sha256'] == sha256(source).hexdigest()
        assert first['positions'] == [{'line': 2, 'line_end': 3}]
        assert first['claims']['title'] == '上\r\n下'
        assert first['record_id'] != second['record_id'] and first['claims']['doi'] == second['claims']['doi']
        assert bad['parse_status'] == 'invalid_row' and bad['positions'] == [{'line': 5, 'line_end': 5}]
        assert result['extraction']['status'] == 'partial'

    def test_submitted_field_verdict_and_documented_years(self):
        claim = {'doi': '10.1234/a', 'title': 'Known article', 'year': '2024'}
        first = {**claim, 'source': 'one', 'journal': 'Name One', 'pages': '1-4'}
        second = {**claim, 'source': 'two', 'journal': 'Different Review', 'pages': '62-64'}
        clean = service._project_verification(claim, _facts(first, second))
        assert clean['status'] == 'metadata_matched' and clean['provider_conflicts']
        assert clean['fields']['pages']['status'] == 'not_checked'
        second.update(year='2025', years=[{'kind': 'published-online', 'year': 2024}])
        assert service._project_verification(claim, _facts(first, second))['fields']['year']['status'] == 'matched'
        second.update(year='2021', years=[])
        disputed = service._project_verification(claim, _facts(first, second))
        assert disputed['fields']['year']['status'] == 'uncertain'
        assert disputed['status'] == 'metadata_uncertain'
        uncertain = service._project_verification({**claim, 'year': '2023'}, _facts(first))
        assert uncertain['fields']['year']['reason'] == 'possible_online_print_difference_not_documented'

    def test_100_structured_rows_resume_export_and_receipts(self):
        from src import config
        from src.core import tracing
        from src.core import cancel
        from src.core import task_events

        root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.object(config, 'PROJECT_ROOT', root))
        self.enterContext(patch.object(service, '_scope', lambda owner, session: None))
        self.enterContext(patch.object(service, '_lease', lambda owner: nullcontext()))
        self.enterContext(patch.object(tracing, 'web_search_enabled', lambda: True))
        self.enterContext(patch.object(cancel, 'is_cancelled', lambda: False))
        self.enterContext(patch.object(task_events, 'remaining_seconds', lambda _: 600))
        attempted = []

        def verify(claim, **kwargs):
            attempted.append(claim['title'])
            retry = claim['title'] == 'Citation 17' and attempted.count('Citation 17') == 1
            kwargs['debit']()
            facts = _facts(errors={'fixture': 'timeout'}) if retry else _facts(claim)
            return service._project_verification(claim, facts)

        self.enterContext(patch.object(service, 'verify_record', verify))
        source = ('doi,title,year\n' + ''.join(f'10.1234/duplicate,Citation {i},2024\n' for i in range(100))).encode()
        audit = service.create_audit(5, 'test', name='input.csv', content=source, request_budget=101)
        audit_id = audit['audit_id']
        journal = []
        for i in range(5):
            step = service.run_audit(5, 'test', audit_id, max_rows=20)
            assert step['processed_indices'] == list(range(i * 20, i * 20 + 20))
            journal.append(_record(step, f'call-{i}'))
        assert step['coverage']['pending'] == 0 and step['coverage']['retryable'] == 1 and not step['terminal']
        assert service.run_audit(5, 'test', audit_id)['processed_indices'] == []
        settled = service.run_audit(5, 'test', audit_id, resume=True)
        assert settled['processed_indices'] == [17] and settled['coverage']['complete']
        assert len(attempted) == 101 and settled['budget']['requests_remaining'] == 0
        assert len({row['record_id'] for row in settled['rows']}) == 100
        assert [a['result']['status'] for a in settled['rows'][17]['attempt_history']] == ['network_error', 'metadata_matched']
        journal.append(_record(settled, 'settled'))
        assert all(row['status'] == 'complete' for row in effective_tool_records(journal))
        assert journal[0]['status'] == 'partial'
        answer = completion_from_answer('100 source rows reviewed.', journal)
        assert not completion_errors(answer, journal)
        assert build_task_result(journal).status == 'completed'
        assert completion_errors(answer, journal, missing=['required_xlsx'])
        data, _, _ = service.export_audit(5, 'test', audit_id, format='json')
        assert len(json.loads(data)['rows']) == 100
        from src.research.reference_tools import _compact_audit
        offsets, projected = set(), []
        offset = 0
        while offset is not None:
            assert offset not in offsets
            offsets.add(offset)
            page = _compact_audit(service.get_audit(5, 'test', audit_id, offset=offset, limit=10))
            assert 0 < len(page['rows']) <= 10
            assert len(json.dumps(page, ensure_ascii=False).encode()) <= 12_500
            projected.extend(row['record_index'] for row in page['rows'])
            offset = page['next_offset']
        assert projected == list(range(100))
        xlsx, _, _ = service.export_audit(5, 'test', audit_id, format='xlsx')
        from openpyxl import load_workbook
        exported = load_workbook(BytesIO(xlsx), read_only=True)
        try:
            assert any(sheet.max_row == 101 for sheet in exported.worksheets)
        finally:
            exported.close()
        other = {**settled, 'audit_id': 'f' * 32}
        assert effective_tool_records([journal[0], _record(other, 'other')])[0]['status'] == 'partial'
        bad_publish = {**step, 'export_status': 'failed'}
        assert effective_tool_records([_record(bad_publish, 'bad'), journal[-1]])[0]['status'] == 'partial'

    def test_catalog_installs_new_tool_contracts(self):
        from src.agent.preset_catalog import _PRESETS, _load, _read_manifest

        manifest = _read_manifest()
        for preset in _PRESETS:
            if preset.name in {'inspect_uploaded_table', 'verify_uploaded_references'}:
                assert _load(preset, manifest[preset.name]).name == preset.name

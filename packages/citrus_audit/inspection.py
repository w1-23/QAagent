"""Read bounded workbook layout and source-coordinate pages without building a DataFrame."""
from contextlib import closing
from hashlib import sha256
import io
import json
import re
from zipfile import ZipFile

from .readers import _missing_sheet_message, open_xlsx_readonly

_MAX_BYTES = 12_000
_MAX_CELLS = 200
_MAX_SCAN_ROW = 100_000
_XML_NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, default=str).encode('utf-8'))


def _cell_value(cell):
    value = cell.value
    item = {'cell': cell.coordinate, 'type': cell.data_type, 'value': value}
    if _size(item) > 2048:
        text = str(value)
        item.update(value=text[:400], truncated=True, value_characters=len(text))
    return item


def _merges(data, path):
    from openpyxl.xml.functions import iterparse

    result = []
    with ZipFile(io.BytesIO(data)) as archive, archive.open(path) as source:
        sheet = None
        for event, element in iterparse(source, events=('start', 'end')):
            if sheet is None:
                sheet = element
            if event != 'end':
                continue
            if element.tag == _XML_NS + 'mergeCell':
                ref = element.get('ref', '')
                if re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]{0,6}:[A-Z]{1,3}[1-9][0-9]{0,6}', ref):
                    if len(result) == 80:
                        return result, True
                    result.append({'range': ref, 'anchor': ref.split(':', 1)[0]})
            elif element.tag in {_XML_NS + 'row', _XML_NS + 'sheetData'}:
                element.clear()
                sheet.clear()
    return result, False


def _layout(workbook, sheet, data, header_rows):
    from openpyxl.utils import get_column_letter

    names = [item.title for item in workbook.worksheets]
    reported = sheet.calculate_dimension() if sheet.max_row and sheet.max_column else None
    sheet.reset_dimensions()
    scan_rows, scan_columns = 24, 48
    headers, occupied = [], {}
    for row_index, cells in enumerate(sheet.iter_rows(min_row=1, max_row=scan_rows,
                                                       max_col=scan_columns), 1):
        present = [cell for cell in cells if cell.value is not None]
        occupied[row_index] = {cell.column for cell in present}
        if row_index in header_rows:
            headers.extend(_cell_value(cell) for cell in present)
    # Each block is a contiguous rectangle of observed occupied cells. Gaps are
    # meaningful, especially for side-by-side tables and multi-row headings.
    blocks = []
    active = {}
    for row_index, columns in occupied.items():
        runs = []
        for column in sorted(columns):
            if runs and runs[-1][1] == column - 1:
                runs[-1] = (runs[-1][0], column)
            else:
                runs.append((column, column))
        active = {run: active.get(run) or [row_index, row_index, *run] for run in runs}
        for block in active.values():
            if block[0] == row_index:
                blocks.append(block)
            block[1] = row_index
    merges, merges_truncated = _merges(data, sheet._worksheet_path)
    result = {'sheet': sheet.title, 'sheets': names[:40], 'sheet_count': len(names),
              'sheet_layout': [{'name': item.title, 'visibility': item.sheet_state}
                               for item in workbook.worksheets[:40]],
              'reported_dimension': reported, 'observed_range': f'A1:{get_column_letter(scan_columns)}{scan_rows}',
              'header_rows': header_rows, 'header_cells': [], 'header_cells_omitted': 0,
              'merged_ranges': merges, 'merged_ranges_truncated': merges_truncated,
              'observed_blocks': [f'{get_column_letter(left)}{top}:{get_column_letter(right)}{bottom}'
                                  for top, bottom, left, right in blocks[:40]],
              'observed_blocks_truncated': len(blocks) > 40}
    for cell in headers:
        result['header_cells'].append(cell)
        if _size(result) > _MAX_BYTES - 1200:
            result['header_cells'].pop()
            break
    result['header_cells_omitted'] = len(headers) - len(result['header_cells'])
    return result


def _cells(sheet, cell_range, page_rows, cursor, digest):
    from openpyxl.utils.cell import range_boundaries, get_column_letter

    left, top, right, bottom = range_boundaries(cell_range)
    if (None in (left, top, right, bottom) or not 1 <= left <= right <= 16_384
            or not 1 <= top <= bottom <= _MAX_SCAN_ROW):
        raise ValueError('请给出有效的单元格范围，例如 A1:F40，最多读取到第100000行。')
    width = right - left + 1
    stamp = sha256(json.dumps([digest, sheet.title, left, top, right, bottom]).encode()).hexdigest()
    offset = 0
    if cursor:
        scope, separator, value = cursor.partition(':')
        if scope != stamp or not separator or not re.fullmatch(r'[0-9]{1,10}', value):
            raise ValueError('分页游标与来源、工作表或范围不匹配。')
        offset = int(value)
    total = (bottom - top + 1) * width
    if offset >= total:
        raise ValueError('分页游标超出指定范围。')
    end = min(total, offset + _MAX_CELLS, ((offset // width) + page_rows) * width)
    page = {'sheet': sheet.title, 'cell_range': cell_range, 'cells': [], 'next_cursor': None,
            'source_sha256': digest, 'start_offset': offset, 'total_cells': total}
    sheet.reset_dimensions()
    position = offset
    while position < end:
        row = top + position // width
        first_column = left + position % width
        # Read at most one rectangle with a fixed width per page. Pinning a
        # column interval prevents a wide source from expanding a single row.
        columns = min(right - first_column + 1, end - position)
        multirow = first_column == left and columns == width
        last_row = min(bottom, row + (end - position) // width - 1) if multirow else row
        for row_index, values in zip(range(row, last_row + 1), sheet.iter_rows(
                min_row=row, max_row=last_row, min_col=first_column, max_col=first_column + columns - 1)):
            for column, cell in enumerate(values, first_column):
                item = (_cell_value(cell) if cell.value is not None else
                        {'cell': f'{get_column_letter(column)}{row_index}', 'type': 'blank', 'value': None})
                page['cells'].append(item)
                if _size(page) > _MAX_BYTES - 1200:
                    page['cells'].pop()
                    page['next_cursor'] = f'{stamp}:{position}'
                    return page
                position += 1
        # openpyxl can omit trailing empty rows with unknown dimensions.
        while position < end and top + position // width <= last_row:
            row_index, column = top + position // width, left + position % width
            page['cells'].append({'cell': f'{get_column_letter(column)}{row_index}', 'type': 'blank', 'value': None})
            if _size(page) > _MAX_BYTES - 1200:
                page['cells'].pop()
                page['next_cursor'] = f'{stamp}:{position}'
                return page
            position += 1
    if position < total:
        page['next_cursor'] = f'{stamp}:{position}'
    return page


def inspect_workbook(data, name, *, sheet_name, header_rows, mode, cell_range, page_rows, cursor):
    digest = sha256(data).hexdigest()
    with closing(open_xlsx_readonly(data, name, data_only=False)) as workbook:
        names = [sheet.title for sheet in workbook.worksheets]
        chosen = sheet_name.strip() or next((sheet.title for sheet in workbook.worksheets
                                             if sheet.sheet_state == 'visible'), '')
        if chosen not in names:
            raise ValueError(_missing_sheet_message(chosen, names))
        sheet = workbook[chosen]
        result = (_layout(workbook, sheet, data, header_rows) if mode == 'layout'
                  else _cells(sheet, cell_range, page_rows, cursor, digest))
    return {'name': name, 'inspection': mode, 'source_sha256': digest, **result}

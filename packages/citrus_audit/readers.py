"""Bounded byte parsers; no account files, network or publication."""
from __future__ import annotations
import csv
import io
import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
logger=logging.getLogger(__name__)
_MAX_TABLE_ROWS=100000
_MAX_TABLE_COLUMNS=200
_MAX_TABLE_CELLS=2000000
_MAX_EXTRACTED_TABLES=500

def _header_cell_text(value: Any) -> str:
    return "" if value is None else str(value).strip()

def _row_is_header_like(cells: list[str]) -> bool:
    """至少两个非空、且没有明显的数据特征（长文本、数值为主）。"""
    values = [cell for cell in cells if cell]
    if len(values) < 2:
        return False
    if len(set(values)) < 2:
        return False
    if any(len(value) > 40 for value in values):
        return False
    numeric = sum(1 for value in values if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value))
    return numeric < max(1, len(values) // 2)

def _auto_header_rows(raw_rows: list[list[Any]], width: int, scan: int = 8) -> list[int]:
    """推断表头行区间：跳过单标题行，合并连续的「分组 + 字段名」多层表头。

    判定顺序：首个像表头的行作为起点；只要下一行的去重标签数更多（分组行标签会重复），
    且不含数值单元格（数据行特征），就继续并入同一表头区间。
    """
    limit = min(len(raw_rows), max(1, scan))
    start = -1
    for index in range(limit):
        cells = [_header_cell_text(v) for v in (raw_rows[index] or [])][:width]
        if _row_is_header_like(cells):
            start = index
            break
    if start < 0:
        return []
    rows: list[int] = []
    previous_distinct = 0
    for index in range(start, min(len(raw_rows), start + 4)):
        cells = [_header_cell_text(v) for v in (raw_rows[index] or [])][:width]
        values = [cell for cell in cells if cell]
        if not _row_is_header_like(cells):
            break
        distinct = len(set(values))
        if rows and distinct <= previous_distinct:
            break
        rows.append(index + 1)
        previous_distinct = distinct
    return rows or [start + 1]

def _resolve_headers(raw_rows: list[list[Any]], header_rows: list[int],
                     width: int) -> tuple[list[str], list[int]]:
    """合并一或多行（1 基）表头，返回 (规范列名, 实际使用的表头行)。

    规范列名取**叶子名**（最下一层表头的字段名），分组名只作为可读路径保留在
    ``meta["header_paths"]``。此前的实现把每一层拼成「分组 / 字段」，导致按列名
    配置的 ``optional_fields`` / ``ranges`` / ``relations`` / ``key_columns``
    全部匹配不上，整列被误判为缺失并错标到相邻列。
    """
    requested = sorted({int(row) for row in (header_rows or [1])})
    selected: list[list[str]] = []
    used: list[int] = []
    for row_no in requested:
        row = raw_rows[row_no - 1] if 0 < row_no <= len(raw_rows) else []
        values = [_header_cell_text(v) for v in row]
        values = (values + [""] * width)[:width]
        if sum(bool(v) for v in values) <= 1:  # 单标题/日期说明行，不是字段头
            continue
        # Excel 的合并表头只在最左格保存可见值：向右补齐，保证每列都带分组名。
        filled: list[str] = []
        horizontal = ""
        for value in values:
            if value:
                horizontal = value
            filled.append(value or horizontal)
        selected.append(filled)
        used.append(row_no)
    if not selected:
        auto = _auto_header_rows(raw_rows, width)
        if auto:
            logger.warning("Header rows %s look like a title row; auto-detected %s",
                           requested, auto)
            return _resolve_headers(raw_rows, auto, width)
        fallback_row = requested[-1] if requested else 1
        selected = [[_header_cell_text(v)
                     for v in (raw_rows[fallback_row - 1] if 0 < fallback_row <= len(raw_rows) else [])]]
        used = [fallback_row]
    labels: list[str] = []
    paths: list[str] = []
    for column in range(width):
        parts: list[str] = []
        carry = ""
        for row in selected:
            value = row[column] if column < len(row) else ""
            if value:
                carry = value
            if carry and (not parts or carry != parts[-1]):
                parts.append(carry)
        leaf = parts[-1] if parts else ""
        paths.append(" / ".join(parts))
        labels.append(leaf or f"未命名列{column + 1}")
    seen: dict[str, int] = {}
    for index, label in enumerate(labels):
        seen[label] = seen.get(label, 0) + 1
        if seen[label] > 1:
            # 叶子名重复时退回完整路径（例如两组的同名字段），并保证唯一。
            label = paths[index] or label
            suffix = 2
            unique = label
            while unique in labels[:index]:
                unique = f"{label} ({suffix})"
                suffix += 1
            labels[index] = unique
    return labels, used

def _missing_sheet_message(requested: str, available, *, kind: str = "工作表") -> str:
    """错误必须带上可用名称：PDF/Word 的表标签是合成的，模型无从猜起。"""
    names = [str(item) for item in (available or ())]
    listing = "、".join(names[:40]) + ("…" if len(names) > 40 else "")
    return (f"{kind}不存在：{requested}；本文件可用{kind}（共 {len(names)} 个）："
            f"{listing or '（无）'}。请从上述名称中选择，或先调用 "
            "inspect_uploaded_table 查看结构。")

def _decode_csv(data: bytes, header_row: int = 1, header_rows: list[int] | None = None):
    text = None
    encoding = ""
    for candidate in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            text = data.decode(candidate)
            encoding = candidate
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("CSV 编码无法识别（尝试 UTF-8 与常见中文编码均失败）")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",\t;|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    header_rows = sorted(set(header_rows or [header_row]))
    if not header_rows or any(row < 1 or row > 20 for row in header_rows):
        raise ValueError("header_row 必须在 1 到 20 之间")
    row_limit = _MAX_TABLE_ROWS + max(header_rows) + 1
    raw_rows = []
    raw_cells = 0
    truncated = False
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        if len(raw_rows) >= row_limit:
            truncated = True
            break
        if len(row) > _MAX_TABLE_COLUMNS:
            raise ValueError(f"列数超过限制 {_MAX_TABLE_COLUMNS}")
        if raw_cells + len(row) > _MAX_TABLE_CELLS:
            truncated = True
            break
        raw_rows.append(row)
        raw_cells += len(row)
    raw_preview = raw_rows[:8]
    if not raw_rows:
        return _pd().DataFrame(), encoding, raw_preview, header_rows, False
    header, header_rows = _resolve_headers(raw_rows, header_rows, max(len(row) for row in raw_rows))
    body_start = max(header_rows)
    width = len(header)
    if width > _MAX_TABLE_COLUMNS:
        raise ValueError(f"列数 {width} 超过限制 {_MAX_TABLE_COLUMNS}")
    data_row_limit = min(_MAX_TABLE_ROWS, _MAX_TABLE_CELLS // max(1, width))
    body = [(row + [""] * width)[:width]
            for row in raw_rows[body_start:body_start + data_row_limit]]
    frame = _pd().DataFrame(body, columns=header)
    frame = frame.replace(r"^\s*$", _pd().NA, regex=True)
    # Keep literal CSV values (including zero-padded IDs) intact. Numeric
    # calculations use temporary coercions and the raw source is never rewritten.
    truncated = truncated or len(raw_rows) > body_start + data_row_limit
    return frame, encoding, raw_preview, header_rows, truncated

def _pd():
    import pandas as pd
    return pd

def _bound_raw_table(rows: list[list[Any]], header_rows: list[int],
                     cell_budget: int, label: str) -> tuple[list[list[Any]], bool, int]:
    width = max((len(row) for row in rows), default=0)
    if width > _MAX_TABLE_COLUMNS:
        raise ValueError(f"{label}列数 {width} 超过限制 {_MAX_TABLE_COLUMNS}，未执行审查")
    if width <= 0:
        return [], len(rows) > 0, 0
    header_limit = max(header_rows)
    row_limit = min(_MAX_TABLE_ROWS + header_limit, header_limit + max(1, cell_budget // width))
    bounded = rows[:row_limit]
    return bounded, len(rows) > row_limit, len(bounded) * width

def open_xlsx_readonly(data, name, *, data_only):
    import openpyxl
    from ._zip_safety import validate_zip_bytes

    validate_zip_bytes(data, label=name)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if "xl/workbook.xml" not in archive.namelist():
            raise ValueError("工作簿缺少必要的 OOXML 结构")
    return openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=data_only, keep_vba=False)


def _load_frame_from_bytes(data: bytes, name: str = "table", sheet_name: str = "",
                           header_row: int = 1, header_rows: list[int] | None = None,
                           discover_only: bool = False) -> tuple[Any, dict[str, Any]]:
    """Parse table bytes into a DataFrame under the service's read budgets."""
    suffix = Path(name).suffix.lower()
    used_header_rows = sorted(set(header_rows or [header_row]))
    if not used_header_rows or any(type(row) is not int or not 1 <= row <= 20 for row in used_header_rows):
        raise ValueError("header_row 必须在 1 到 20 之间")
    if suffix in {".csv", ".tsv"}:
        frame, encoding, raw_preview, used_header_rows, truncated = _decode_csv(data, header_row, used_header_rows)
        meta = {"name": name, "sheet": "", "encoding": encoding, "delimiter": "auto",
                "raw_preview": raw_preview, "header_row": max(used_header_rows),
                "header_rows": used_header_rows, "truncated": truncated,
                "source_rows": int(frame.shape[0]), "source_columns": int(frame.shape[1]),
                "processed_rows": int(frame.shape[0]),
                "processed_columns": int(frame.shape[1]),
                "truncation_reasons": (["CSV 行数或单元格数超过安全解析预算"]
                                       if truncated else [])}
    elif suffix in {".xlsx", ".xlsm", ".xls", ".xlsb"}:
        try:
            used_header_rows = sorted(set(header_rows or [header_row]))
            if not used_header_rows or any(row < 1 or row > 20 for row in used_header_rows):
                raise ValueError("header_row 必须在 1 到 20 之间")
            if suffix in {".xls", ".xlsb"}:
                try:
                    import python_calamine
                    reader = python_calamine.CalamineWorkbook.from_filelike(io.BytesIO(data))
                    names = reader.sheet_names
                    if discover_only and not sheet_name:
                        return _pd().DataFrame(), {'name': name, 'sheets': names, 'sheet': '', 'discovery_only': True}
                    requested_sheet = sheet_name.strip()
                    if requested_sheet and requested_sheet not in names:
                        raise ValueError(_missing_sheet_message(requested_sheet, names))
                    chosen = requested_sheet or (names[0] if names else "")
                    if not chosen:
                        raise ValueError("工作簿中没有可读取的工作表")
                    sheet = reader.get_sheet_by_name(chosen)
                    width = getattr(sheet, "width", None)
                    height = getattr(sheet, "height", None)
                    width = width() if callable(width) else width
                    height = height() if callable(height) else height
                    if not isinstance(width, int) or not isinstance(height, int):
                        raise ValueError("无法在读取前确认工作表尺寸；为避免无界内存占用，已停止解析")
                    if width > _MAX_TABLE_COLUMNS:
                        raise ValueError(f"列数 {width} 超过限制 {_MAX_TABLE_COLUMNS}")
                    row_limit = max(1, min(_MAX_TABLE_ROWS + max(used_header_rows) + 1,
                                           _MAX_TABLE_CELLS // max(1, width) + max(used_header_rows)))
                    if height > row_limit:
                        raise ValueError(
                            f"工作表有 {height:,} 行，当前 XLS/XLSB 安全解析上限为 {row_limit:,} 行；"
                            "请先拆分工作表后重试，未对该表执行审查")
                    data_rows = sheet.to_python()
                    if len(data_rows) > row_limit:
                        raise ValueError("工作表实际行数超出安全解析范围，未执行审查")
                except ImportError as exc:
                    raise ValueError(f"读取 {suffix.upper()} 需要安装 python-calamine；文件未被判定为无异常") from exc
            else:
                wb = open_xlsx_readonly(data, name, data_only=True)
                names = wb.sheetnames
                if discover_only and not sheet_name:
                    wb.close()
                    return _pd().DataFrame(), {'name': name, 'sheets': names, 'sheet': '', 'discovery_only': True}
                requested_sheet = sheet_name.strip()
                if requested_sheet and requested_sheet not in names:
                    wb.close()
                    raise ValueError(_missing_sheet_message(requested_sheet, names))
                chosen = requested_sheet or next(
                    (s for s in names if wb[s].max_row and wb[s].max_column),
                    names[0] if names else "")
                if not chosen:
                    wb.close()
                    raise ValueError("工作簿中没有可读取的工作表")
                worksheet = wb[chosen]
                width = int(worksheet.max_column or 0)
                height = int(worksheet.max_row or 0)
                if width > _MAX_TABLE_COLUMNS:
                    wb.close()
                    raise ValueError(f"列数 {width} 超过限制 {_MAX_TABLE_COLUMNS}")
                header_span = max(used_header_rows)
                if width and height:
                    row_limit = max(1, min(_MAX_TABLE_ROWS + header_span + 1,
                                           _MAX_TABLE_CELLS // max(1, width) + header_span))
                    data_rows = list(worksheet.iter_rows(
                        min_row=1, max_row=min(height, row_limit), max_col=width,
                        values_only=True))
                else:
                    # 有些工作簿没有写 sheet dimension，read_only 模式下 max_row/max_column 为 None；
                    # 此时按有界惰性遍历读取真实内容，再按实际行列重算尺寸，避免把整张表判定为空。
                    row_limit = max(1, _MAX_TABLE_ROWS + header_span + 1)
                    data_rows = []
                    observed_cells = 0
                    unknown_dimensions_truncated = False
                    for row in worksheet.iter_rows(values_only=True):
                        row = row if isinstance(row, tuple) else tuple(row or ())
                        if len(row) > _MAX_TABLE_COLUMNS:
                            wb.close()
                            raise ValueError(f"列数超过限制 {_MAX_TABLE_COLUMNS}")
                        if len(data_rows) >= row_limit or observed_cells + len(row) > _MAX_TABLE_CELLS:
                            unknown_dimensions_truncated = True
                            break
                        data_rows.append(row)
                        observed_cells += len(row)
                    while data_rows and not any(
                            cell is not None and str(cell).strip() for cell in data_rows[-1]):
                        data_rows.pop()
                    width = max((len(row) for row in data_rows), default=0)
                    height = len(data_rows)
                    if width > _MAX_TABLE_COLUMNS:
                        wb.close()
                        raise ValueError(f"列数 {width} 超过限制 {_MAX_TABLE_COLUMNS}")
                wb.close()
            data_row_limit = min(_MAX_TABLE_ROWS, _MAX_TABLE_CELLS // max(1, width))
            if len(data_rows) < max(used_header_rows):
                frame = _pd().DataFrame()
            else:
                width = max((len(row) for row in data_rows), default=0)
                if width > _MAX_TABLE_COLUMNS:
                    raise ValueError(f"列数 {width} 超过限制 {_MAX_TABLE_COLUMNS}")
                header, used_header_rows = _resolve_headers(data_rows, used_header_rows, width)
                data_row_limit = min(_MAX_TABLE_ROWS, _MAX_TABLE_CELLS // max(1, len(header)))
                frame = _pd().DataFrame(
                    data_rows[max(used_header_rows):max(used_header_rows) + data_row_limit],
                    columns=header, dtype=object)
            meta = {"name": name, "sheet": chosen, "sheets": names, "encoding": suffix[1:],
                    "header_row": max(used_header_rows), "header_rows": used_header_rows,
                    "raw_preview": [[str(v)[:120] if v is not None else "" for v in row]
                                    for row in data_rows[:8]],
                    "source_rows": max(0, int(height) - max(used_header_rows)),
                    "source_columns": int(width),
                    "processed_rows": int(frame.shape[0]),
                    "processed_columns": int(frame.shape[1]),
                    "truncation_reasons": ([
                        f"工作表源数据行数 {max(0, height - max(used_header_rows)):,} "
                        "超过单次审查行数或单元格预算"
                    ] if max(0, height - max(used_header_rows)) > frame.shape[0] else [])}
            meta["truncated"] = (height > row_limit) or (
                len(data_rows) > max(used_header_rows) + data_row_limit) or bool(
                    locals().get('unknown_dimensions_truncated', False))
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"无法读取工作簿：{exc}") from exc
    elif suffix == ".pdf":
        try:
            import pdfplumber
            all_tables = []
            cell_budget = _MAX_TABLE_CELLS
            extraction_truncated = False
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                pages = pdf.pages[:200]
                extraction_truncated = len(pdf.pages) > len(pages)
                for page_no, page in enumerate(pages, start=1):
                    for table_no, rows in enumerate(page.extract_tables() or [], start=1):
                        if rows and any(any(str(cell or "").strip() for cell in row) for row in rows):
                            if len(all_tables) >= _MAX_EXTRACTED_TABLES or cell_budget <= 0:
                                extraction_truncated = True
                                break
                            label = f"{name}!p{page_no}_t{table_no}"
                            # Discovery lists targets without parsing the first table.
                            # A malformed/wide table must not hide a later usable one.
                            if (discover_only and not sheet_name.strip()) or (sheet_name.strip() and label != sheet_name.strip()):
                                all_tables.append((label, []))
                                continue
                            bounded_rows, clipped, used_cells = _bound_raw_table(
                                rows, used_header_rows, cell_budget, label)
                            all_tables.append((label, bounded_rows))
                            cell_budget -= used_cells
                            extraction_truncated = extraction_truncated or clipped
                    if extraction_truncated and (len(all_tables) >= _MAX_EXTRACTED_TABLES or cell_budget <= 0):
                        break
            if not all_tables:
                raise ValueError("PDF 中没有可提取的结构化表格；扫描件/OCR 未覆盖，审查未完成")
            if discover_only and not sheet_name.strip():
                return _pd().DataFrame(), {
                    'name': name, 'sheet': '', 'sheets': [label for label, _ in all_tables],
                    'discovery_only': True, 'truncated': extraction_truncated,
                    'truncation_reasons': ['PDF 页数或表格数超过发现预算'] if extraction_truncated else []}
            if sheet_name.strip():
                matching = next(((label, rows) for label, rows in all_tables
                                 if label == sheet_name.strip()), None)
                if matching is None:
                    raise ValueError(_missing_sheet_message(
                        sheet_name.strip(), [label for label, _rows in all_tables],
                        kind="PDF 表格"))
                chosen, rows = matching
            elif len(all_tables) > 1 and not discover_only:
                raise ValueError(
                    "PDF 提取到多个表格，请用 sheet_name 指定目标（例如 "
                    f"{all_tables[0][0]}）；未默认选择第一张表")
            else:
                chosen, rows = all_tables[0]
            used_header_rows = sorted(set(header_rows or [header_row]))
            if any(row < 1 or row > 20 for row in used_header_rows):
                raise ValueError("header_row 必须在 1 到 20 之间")
            width = max((len(row) for row in rows), default=0)
            header, used_header_rows = _resolve_headers(rows, used_header_rows, width)
            data_row_limit = min(_MAX_TABLE_ROWS, _MAX_TABLE_CELLS // max(1, width))
            frame = _pd().DataFrame(
                [(row + [None] * width)[:width]
                 for row in rows[max(used_header_rows):max(used_header_rows) + data_row_limit]],
                columns=header)
            meta = {"name": name, "sheet": chosen, "sheets": [label for label, _ in all_tables],
                    "encoding": "pdf-table", "header_row": max(used_header_rows),
                    "header_rows": used_header_rows, "raw_preview": rows[:8],
                    "source_rows": max(0, len(rows) - max(used_header_rows)),
                    "source_columns": int(width),
                    "processed_rows": int(frame.shape[0]),
                    "processed_columns": int(frame.shape[1]),
                    "truncated": extraction_truncated or
                                 len(rows) > max(used_header_rows) + data_row_limit,
                    "truncation_reasons": (
                        ["PDF 页数、表格数、行数或单元格数超过提取预算"]
                        if extraction_truncated or
                        len(rows) > max(used_header_rows) + data_row_limit else [])}
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"无法提取 PDF 表格：{exc}") from exc
    elif suffix == ".docx":
        try:
            from ._zip_safety import validate_zip_bytes
            validate_zip_bytes(data, label=name)
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                document_info = archive.getinfo("word/document.xml")
                if document_info.file_size > 16 * 1024 * 1024:
                    raise ValueError("Word 正文 XML 超过 16 MB，已停止解析")
                root = ET.fromstring(archive.read("word/document.xml"))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            word_tables = []
            cell_budget = _MAX_TABLE_CELLS
            table_nodes = root.findall(".//w:tbl", ns)
            extraction_truncated = len(table_nodes) > _MAX_EXTRACTED_TABLES
            if discover_only and not sheet_name.strip():
                return _pd().DataFrame(), {
                    'name': name, 'sheet': '',
                    'sheets': [f'{name}!t{number}' for number in range(1, min(len(table_nodes), _MAX_EXTRACTED_TABLES)+1)],
                    'discovery_only': True, 'truncated': extraction_truncated,
                    'truncation_reasons': ['Word 表格数超过发现预算'] if extraction_truncated else []}
            for number, table in enumerate(table_nodes[:_MAX_EXTRACTED_TABLES], start=1):
                label = f"{name}!t{number}"
                if sheet_name.strip() and label != sheet_name.strip():
                    continue
                rows = []
                observed_cells = 0
                row_limit = _MAX_TABLE_ROWS + max(used_header_rows) + 1
                for row in table.findall("./w:tr", ns):
                    cells = row.findall("./w:tc", ns)
                    if len(cells) > _MAX_TABLE_COLUMNS:
                        raise ValueError(f"Word 表格 {number} 列数超过限制 {_MAX_TABLE_COLUMNS}，未执行审查")
                    if len(rows) >= row_limit or observed_cells + len(cells) > cell_budget:
                        extraction_truncated = True
                        break
                    rows.append(["".join(t.text or "" for t in cell.findall(".//w:t", ns))
                                 for cell in cells])
                    observed_cells += len(cells)
                if rows:
                    if len(word_tables) >= _MAX_EXTRACTED_TABLES or cell_budget <= 0:
                        extraction_truncated = True
                        break
                    label = f"{name}!t{number}"
                    bounded_rows, clipped, used_cells = _bound_raw_table(
                        rows, sorted(set(header_rows or [header_row])), cell_budget, label)
                    word_tables.append((label, bounded_rows))
                    cell_budget -= used_cells
                    extraction_truncated = extraction_truncated or clipped
                    if cell_budget <= 0:
                        extraction_truncated = True
                        break
            if not word_tables:
                raise ValueError("Word 中没有可提取的结构化表格，审查未完成")
            if sheet_name.strip():
                matching = next(((label, rows) for label, rows in word_tables
                                 if label == sheet_name.strip()), None)
                if matching is None:
                    raise ValueError(_missing_sheet_message(
                        sheet_name.strip(), [label for label, _rows in word_tables],
                        kind="Word 表格"))
                chosen, rows = matching
            elif len(word_tables) > 1 and not discover_only:
                raise ValueError(
                    "Word 提取到多个表格，请用 sheet_name 指定目标（例如 "
                    f"{word_tables[0][0]}）；未默认选择第一张表")
            else:
                chosen, rows = word_tables[0]
            used_header_rows = sorted(set(header_rows or [header_row]))
            width = max((len(row) for row in rows), default=0)
            header, used_header_rows = _resolve_headers(rows, used_header_rows, width)
            data_row_limit = min(_MAX_TABLE_ROWS, _MAX_TABLE_CELLS // max(1, width))
            frame = _pd().DataFrame(
                [(row + [None] * width)[:width]
                 for row in rows[max(used_header_rows):max(used_header_rows) + data_row_limit]],
                columns=header)
            meta = {"name": name, "sheet": chosen, "sheets": [label for label, _ in word_tables],
                    "encoding": "docx-table", "header_row": max(used_header_rows),
                    "header_rows": used_header_rows, "raw_preview": rows[:8],
                    "source_rows": max(0, len(rows) - max(used_header_rows)),
                    "source_columns": int(width),
                    "processed_rows": int(frame.shape[0]),
                    "processed_columns": int(frame.shape[1]),
                    "truncated": extraction_truncated or
                                 len(rows) > max(used_header_rows) + data_row_limit,
                    "truncation_reasons": (
                        ["Word 表格数、行数或单元格数超过提取预算"]
                        if extraction_truncated or
                        len(rows) > max(used_header_rows) + data_row_limit else [])}
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"无法提取 Word 表格：{exc}") from exc
    else:
        raise ValueError("当前审查支持 CSV/TSV/XLSX/XLS/XLSM/PDF/DOCX")
    if frame.shape[1] > _MAX_TABLE_COLUMNS:
        raise ValueError(f"列数 {frame.shape[1]} 超过限制 {_MAX_TABLE_COLUMNS}")
    if frame.shape[0] > _MAX_TABLE_ROWS:
        frame = frame.iloc[:_MAX_TABLE_ROWS].copy()
        meta["truncated"] = True
    else:
        meta["truncated"] = bool(meta.get("truncated", False))
    if meta["truncated"] and not meta.get("truncation_reasons"):
        meta["truncation_reasons"] = ["输入行数或单元格数超过单次审查预算"]
    frame = _pd().DataFrame(
        [[_pd().NA if isinstance(value, str) and not value.strip() else value for value in row]
         for row in frame.itertuples(index=False, name=None)], columns=frame.columns, dtype=object)
    # Text parsers retain numeric tokens. Excel numeric cells have already lost
    # display/lexical precision; never manufacture trailing digits from floats.
    lexical = suffix in {'.csv', '.tsv', '.docx'}
    frame.attrs['lexical_precision_preserved'] = lexical
    meta['lexical_precision_preserved'] = lexical
    meta['source_rows_exact'] = not meta.get('truncated', False)
    meta['formula_evaluation'] = 'cached_values_only_no_recalculation' if suffix in {'.xlsx', '.xlsm', '.xls', '.xlsb'} else 'not_applicable'
    return frame, meta


def read_table(data: bytes, name: str, *, sheet_name='', header_row=1, header_rows=None):
    """Read bytes once, retaining actual tokens and original coordinates."""
    from decimal import Decimal
    import hashlib
    from .envelope import TableEnvelope
    if not isinstance(data,bytes) or len(data)>30*1024*1024:
        raise ValueError('Source exceeds 30 MiB byte budget')
    frame,meta=_load_frame_from_bytes(data,name,sheet_name,header_row,header_rows)
    rows=tuple(range(int(meta['header_row'])+1,int(meta['header_row'])+1+len(frame)))
    cols=tuple(range(1,len(frame.columns)+1))
    from .integrity import _number
    evidence=[];column_evidence={col:[] for col in cols}
    for row,values in zip(rows,frame.itertuples(index=False,name=None)):
        for col,value in zip(cols,values):
            # A real string token can survive Excel too; numeric cells never
            # acquire synthetic digits from their number_format.
            if not isinstance(value,str) or len(value)>256:
                continue
            match=re.fullmatch(r'[+-]?(?:[0-9]+(?:\.([0-9]*))?|\.([0-9]+))(?:[eE]([+-]?[0-9]+))?',value.strip())
            if match and _number(value)[0] is not None:
                item={'row':row,'column':col,'raw':value,
                      'places':len(match[1] or match[2] or ''),'exp':int(match[3] or 0)}
                evidence.append(item);column_evidence[col].append(item)
    by_column={}
    for col,name_column in zip(cols,frame.columns):
        actual=sum(_number(value)[0] is not None for value in frame[name_column])
        tokens=column_evidence[col]
        status='not_applicable' if not actual else 'preserved' if len(tokens)==actual else 'partial' if tokens else 'unavailable'
        by_column[str(name_column)]={'status':status,'numeric_values':actual,'tokens':len(tokens),
            'rows':[item['row'] for item in tokens],
            'reason':'actual parser tokens only; numeric display format does not establish lexical precision'}
    return TableEnvelope(columns=tuple(frame.columns),values=tuple(frame.itertuples(index=False,name=None)),
        row_numbers=rows,column_numbers=cols,source_sha256=hashlib.sha256(data).hexdigest(),
        source_name=name,sheet=meta.get('sheet',''),header_row=int(meta['header_row']),
        precision_evidence=tuple(evidence),lexical_coverage={'columns':by_column,'tokens':len(evidence)},
        parser_limits={'max_rows':_MAX_TABLE_ROWS,'max_columns':_MAX_TABLE_COLUMNS,'max_cells':_MAX_TABLE_CELLS,
                       'max_bytes':30*1024*1024,'max_extracted_tables':_MAX_EXTRACTED_TABLES},coverage=meta)


def read_tables(data: bytes, name: str, *, sheet_name='', header_row=1, header_rows=None):
    """Discover every bounded sheet/table; source-loading failures stay explicit."""
    if not isinstance(data,bytes) or len(data)>30*1024*1024:
        raise ValueError('Source exceeds 30 MiB byte budget')
    _,inventory=_load_frame_from_bytes(data,name,sheet_name=sheet_name,header_row=header_row,
                                     header_rows=header_rows,discover_only=True)
    names=[sheet_name] if sheet_name else inventory.get('sheets') or [inventory.get('sheet','')]
    tables=[]; errors=[];remaining=_MAX_TABLE_CELLS
    if len(names)>_MAX_EXTRACTED_TABLES:
        errors.append({'code':'source_discovery_truncated','message':'Table count exceeds file budget','retryable':False})
        names=names[:_MAX_EXTRACTED_TABLES]
    for index,sheet in enumerate(names):
        if remaining<=0:
            errors.append({'code':'file_cell_budget','unread_tables':len(names)-index,'retryable':False})
            break
        try:
            table=read_table(data,name,sheet_name=sheet,header_row=header_row,header_rows=header_rows)
            cells=len(table.values)*len(table.columns)
            if cells>remaining:
                # Keep whole original rows and their lexical evidence, never
                # silently shorten the table and claim complete coverage.
                from dataclasses import replace
                count=remaining//len(table.columns);kept=set(table.row_numbers[:count])
                lexical={'columns':{key:{**value,'rows':[r for r in value['rows'] if r in kept],
                    'tokens':sum(r in kept for r in value['rows']),'status':'partial'}
                    for key,value in table.lexical_coverage.get('columns',{}).items()}}
                table=replace(table,values=table.values[:count],row_numbers=table.row_numbers[:count],
                    precision_evidence=tuple(e for e in table.precision_evidence if e['row'] in kept),
                    lexical_coverage=lexical,coverage={**table.coverage,'truncated':True,
                        'truncation_reasons':['file_cell_budget']})
                errors.append({'sheet':sheet,'code':'file_cell_budget','unread_tables':len(names)-index-1,'retryable':False})
                tables.append(table);break
            tables.append(table);remaining-=cells
        except (ValueError,ImportError) as exc:
            errors.append({'sheet':sheet,'code':'source_parse_failed','message':str(exc),'retryable':False})
    if inventory.get('truncated'):
        errors.append({'code':'source_discovery_truncated','message':'Table discovery budget reached','retryable':False})
    return tables,errors

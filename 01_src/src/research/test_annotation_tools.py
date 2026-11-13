"""标注 Excel 审查：逐行标注而非规则扫描。

覆盖三件事：
1. 标注输入校验（错误行号、空结论、单对象写法）；
2. 生成文件的结构（原始列保留 + 三列标注 + 说明页 + 未标注行留空）；
3. 工具层行为（文件命名、原件不被修改、越界行给警告、并发/异常不污染原件）。
"""
from __future__ import annotations

import io
import json

import pandas as pd
import pytest
from openpyxl import load_workbook

from src.research import tools as research_tools
from src.research.tools import (
    _normalize_annotations,
    _write_annotated_xlsx,
    audit_uploaded_data,
)


def _frame():
    return pd.DataFrame({
        "处理": ["对照", "低氮", "高氮"],
        "重复": [3, 3, 3],
        "产量": [41.2, 45.8, 39.4],
    })


def _meta(sheet: str = "Sheet1"):
    return {"name": "产量.xlsx", "sheet": sheet, "header_row": 1, "header_rows": [1]}


def test_normalize_annotations_accepts_single_object_and_trims():
    items = _normalize_annotations({"row": 2, "verdict": "正常", "reason": "  与原文一致  "})
    assert items == [{"row": 2, "level": "ok", "verdict": "正常",
                      "reason": "与原文一致", "field": "", "sheet": ""}]


@pytest.mark.parametrize("payload,expected", [
    ({"row": 2, "verdict": "可疑", "reason": "x"}, "review"),
    ({"row": 2, "verdict": "错误", "reason": "x"}, "error"),
    ({"row": 2, "verdict": "随便写的", "reason": "x"}, "review"),
    ({"row": 2, "level": "ok", "verdict": "可疑", "reason": "x"}, "ok"),
])
def test_level_is_normalized_from_verdict_or_explicit_level(payload, expected):
    assert _normalize_annotations([payload])[0]["level"] == expected


@pytest.mark.parametrize("payload", [
    [{"verdict": "正常", "reason": "无行号"}],
    [{"row": "abc", "verdict": "正常"}],
    [{"row": 0, "verdict": "正常"}],
    [{"row": 2, "verdict": "", "reason": ""}],
    "not-a-list-of-objects",
])
def test_normalize_annotations_rejects_bad_input(payload):
    with pytest.raises(ValueError):
        _normalize_annotations(payload)


def test_annotated_xlsx_keeps_original_columns_and_marks_missing_rows():
    content, stats = _write_annotated_xlsx(
        "产量.xlsx", _frame(), _meta(),
        [{"row": 3, "level": "review", "verdict": "需核对",
          "reason": "高氮产量低于对照，需核对记录", "field": "产量"},
         {"row": 99, "level": "error", "verdict": "错误", "reason": "行号越界"}],
        note="测试标注",
    )
    assert stats["rows"] == 3
    assert stats["annotated"] == 1
    assert any("99" in warning for warning in stats["warnings"])

    book = load_workbook(io.BytesIO(content))
    sheet = book["Sheet1"]
    header = [cell.value for cell in sheet[1]]
    assert header[:3] == ["处理", "重复", "产量"]
    assert header[3:] == ["审查结论", "审查理由", "涉及字段"]
    # 第 3 行数据（表头占第 1 行）拿到标注
    assert sheet.cell(row=3, column=4).value == "需核对"
    assert sheet.cell(row=3, column=6).value == "产量"
    # 未标注的行留空，而不是被写成“正常”
    assert sheet.cell(row=2, column=4).value in (None, "")
    assert sheet.cell(row=4, column=4).value in (None, "")
    guide = book["标注说明"]
    values = {str(row[0].value): row[1].value for row in guide.iter_rows(min_row=2)}
    assert values["数据行数"] == 3
    assert values["标注行数"] == 1
    assert values["未标注行数"] == 2
    assert values["说明"] == "测试标注"


def _single_sheet_source(title: str = "Sheet1") -> bytes:
    """真实工作簿字节：标注走 annotate_workbook_multi，需要能真正打开的源文件。"""
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = title
    sheet.append(list(_frame().columns))
    for row in _frame().itertuples(index=False, name=None):
        sheet.append(list(row))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_audit_tool_writes_标注_artifact_and_leaves_source_untouched(monkeypatch):
    source_bytes = _single_sheet_source()
    tables = [{"name": "产量.xlsx", "data": source_bytes}]
    saved = []

    def fake_save(filename, content, mime, **kwargs):
        saved.append({"filename": filename, "content": content, "mime": mime})
        return f"/download/{len(saved)}"

    monkeypatch.setattr(research_tools, "get_request_tables", lambda: tables)
    monkeypatch.setattr(research_tools, "_save_artifact", fake_save)
    monkeypatch.setattr(
        research_tools, "_load_frame",
        lambda index, sheet_name="", header_row=1, header_rows=None, **kwargs: (
            _frame(), _meta(sheet_name or "Sheet1")),
    )

    payload = audit_uploaded_data.invoke({
        "file_indexes_json": "[0]",
        "annotations_json": json.dumps(
            [{"row": 2, "verdict": "正常", "reason": "对照处理符合设计"}],
            ensure_ascii=False),
    })
    # 工具对模型返回简短中文说明（不返回大段 JSON），细节进事件与文件
    assert "产量.xlsx" in payload and "1 条标注写入" in payload
    assert "2 行未覆盖" in payload and "原始文件未修改" in payload
    assert saved[0]["filename"] == "产量_标注.xlsx"  # v15: 按工作簿短名命名，不再拼工作表名
    assert saved[0]["mime"].endswith("spreadsheetml.sheet")
    assert saved[0]["content"].startswith(b"PK")  # xlsx 是 zip 容器
    # 原表字节未被改动
    assert tables[0]["data"] is source_bytes


def test_audit_tool_reports_failure_without_selection(monkeypatch):
    monkeypatch.setattr(research_tools, "get_request_tables", lambda: [])
    assert "未生成审查文件" in audit_uploaded_data.invoke({"file_indexes_json": "[]"})

def test_annotation_filename_avoids_duplicated_sheet_name(monkeypatch):
    """xlsx 默认工作表名常等于文件名，不能生成“产量_产量_标注.xlsx”。"""
    saved = []
    monkeypatch.setattr(research_tools, "get_request_tables",
                        lambda: [{"name": "产量.xlsx", "data": _single_sheet_source("产量")}])
    monkeypatch.setattr(research_tools, "_save_artifact",
                        lambda filename, content, mime, **kwargs: saved.append(filename) or "/download/1")
    monkeypatch.setattr(
        research_tools, "_load_frame",
        lambda index, sheet_name="", header_row=1, header_rows=None, **kwargs: (_frame(), _meta("产量")),
    )
    research_tools.audit_uploaded_data.invoke({
        "file_indexes_json": "[0]",
        "annotations_json": json.dumps([{"row": 2, "verdict": "正常", "reason": "对照"}],
                                       ensure_ascii=False),
    })
    assert saved == ["产量_标注.xlsx"]


def test_audit_tool_covers_all_sheets_in_one_artifact(monkeypatch):
    """同一份多 Sheet 文件只出一个综合标注文件，不能每张表各出一个。"""
    import pandas as pd
    from openpyxl import Workbook
    import io as _io

    wb = Workbook()
    ws = wb.active
    ws.title = "产量"
    ws.append(["品种", "单株产量"])
    ws.append(["纽荷尔", 42])
    ws2 = wb.create_sheet("品质")
    ws2.append(["品种", "可滴定酸"])
    ws2.append(["纽荷尔", 0.8])
    buf = _io.BytesIO()
    wb.save(buf)
    source_bytes = buf.getvalue()

    tables = [{"name": "试验.xlsx", "data": source_bytes}]
    saved = []

    def fake_save(filename, content, mime, **kwargs):
        saved.append({"filename": filename, "content": content})
        return f"/download/{len(saved)}"

    def fake_load(index, sheet_name="", header_row=1, header_rows=None, **kwargs):
        name = sheet_name or "产量"
        if name == "产量":
            frame = pd.DataFrame({"品种": ["纽荷尔"], "单株产量": [42]})
        else:
            frame = pd.DataFrame({"品种": ["纽荷尔"], "可滴定酸": [0.8]})
        return frame, {"sheet": name, "sheets": ["产量", "品质"], "header_row": 1,
                       "header_rows": [1], "header_row_used": 1}

    monkeypatch.setattr(research_tools, "get_request_tables", lambda: tables)
    monkeypatch.setattr(research_tools, "_save_artifact", fake_save)
    monkeypatch.setattr(research_tools, "_load_frame", fake_load)

    payload = audit_uploaded_data.invoke({"file_indexes_json": "[0]"})
    assert len(saved) == 1, saved
    assert saved[0]["filename"] == "试验_数据审查.xlsx"
    assert "完成 2 个工作表的审查" in payload
    from openpyxl import load_workbook
    book = load_workbook(_io.BytesIO(saved[0]["content"]))
    assert book.sheetnames == ["产量", "品质", "问题清单", "审查概览", "审查规则"]  # v15 增加“审查规则”页
    assert tables[0]["data"] is source_bytes


def _two_sheet_workbook() -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    first = book.active
    first.title = "产量"
    first.append(["品种", "单株产量"])
    first.append(["纽荷尔", 42])
    first.append(["塔罗科", 39])
    second = book.create_sheet("品质")
    second.append(["品种", "可滴定酸"])
    second.append(["纽荷尔", 0.8])
    second.append(["塔罗科", 0.9])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _patch_two_sheets(monkeypatch, saved):
    source = _two_sheet_workbook()
    tables = [{"name": "试验.xlsx", "data": source}]

    def fake_save(filename, content, mime, **kwargs):
        saved.append({"filename": filename, "content": content})
        return f"/download/{len(saved)}"

    def fake_load(index, sheet_name="", header_row=1, header_rows=None, **kwargs):
        name = sheet_name or "产量"
        if name == "产量":
            frame = pd.DataFrame({"品种": ["纽荷尔", "塔罗科"], "单株产量": [42, 39]})
        else:
            frame = pd.DataFrame({"品种": ["纽荷尔", "塔罗科"], "可滴定酸": [0.8, 0.9]})
        return frame, {"sheet": name, "sheets": ["产量", "品质"], "header_row": 1,
                       "header_rows": [1]}

    monkeypatch.setattr(research_tools, "get_request_tables", lambda: tables)
    monkeypatch.setattr(research_tools, "_save_artifact", fake_save)
    monkeypatch.setattr(research_tools, "_load_frame", fake_load)
    return tables, source


def test_row_annotations_on_a_multi_sheet_file_produce_one_workbook(monkeypatch):
    """按行标注也必须一册一份，与默认确定性路径一致。"""
    saved = []
    tables, source = _patch_two_sheets(monkeypatch, saved)
    payload = audit_uploaded_data.invoke({
        "file_indexes_json": "[0]",
        "annotations_json": json.dumps([
            {"row": 2, "sheet": "产量", "verdict": "可疑", "reason": "合成理由甲"},
            {"row": 3, "sheet": "品质", "verdict": "正常", "reason": "合成理由乙"},
        ], ensure_ascii=False),
    })
    assert len(saved) == 1, saved
    assert saved[0]["filename"] == "试验_标注.xlsx"
    book = load_workbook(io.BytesIO(saved[0]["content"]))
    assert book.sheetnames[:2] == ["产量", "品质"]
    assert "问题清单" in book.sheetnames and "审查概览" in book.sheetnames
    issues = list(book["问题清单"].iter_rows(min_row=2, values_only=True))
    assert {(row[1], row[3]) for row in issues} == {("产量", 2), ("品质", 3)}
    assert "合成理由甲" in payload or "2 条标注写入" in payload
    assert tables[0]["data"] is source


def test_row_annotation_without_a_sheet_is_not_guessed_across_sheets(monkeypatch):
    saved = []
    _patch_two_sheets(monkeypatch, saved)
    payload = audit_uploaded_data.invoke({
        "file_indexes_json": "[0]",
        "annotations_json": json.dumps(
            [{"row": 2, "verdict": "可疑", "reason": "未指定工作表"}], ensure_ascii=False),
    })
    assert len(saved) == 1
    assert "可用工作表" in payload and "已忽略" in payload
    book = load_workbook(io.BytesIO(saved[0]["content"]))
    assert list(book["问题清单"].iter_rows(min_row=2, values_only=True)) == []


def test_row_annotation_out_of_range_row_is_reported_not_silently_dropped(monkeypatch):
    saved = []
    _patch_two_sheets(monkeypatch, saved)
    payload = audit_uploaded_data.invoke({
        "file_indexes_json": "[0]",
        "annotations_json": json.dumps(
            [{"row": 99, "sheet": "产量", "verdict": "错误", "reason": "越界"}],
            ensure_ascii=False),
    })
    assert "第 99 行不在该表的数据范围内" in payload
    assert len(saved) == 1


def test_annotation_error_names_the_expected_shape_and_received_keys():
    with pytest.raises(ValueError) as missing_row:
        _normalize_annotations([{"verdict": "可疑", "reason": "无行号"}])
    assert "row" in str(missing_row.value) and "verdict" in str(missing_row.value)
    with pytest.raises(ValueError) as wrong_type:
        _normalize_annotations("not-a-list")
    assert "str" in str(wrong_type.value) and "row" in str(wrong_type.value)


def test_annotation_batch_collects_errors_and_keeps_valid_entries():
    errors = []
    cleaned = _normalize_annotations([
        {"row": 2, "verdict": "正常", "reason": "合成有效条目"},
        {"row": 2.5, "verdict": "错误"},
        {"row": True, "reason": "布尔值不是行号"},
        {"verdict": "缺行号"},
        "not-an-object",
    ], errors=errors)
    assert len(cleaned) == 1 and cleaned[0]["row"] == 2
    assert len(errors) == 4
    assert "第 2 个" in errors[0] and "整数 row" in errors[0]
    assert "实际收到的键" in errors[2]
    assert "第 5 个" in errors[3]


def test_valid_annotations_survive_bad_entries_with_a_partial_receipt(monkeypatch):
    saved = []
    _patch_two_sheets(monkeypatch, saved)
    annotations = [{"row": row, "sheet": sheet, "verdict": "正常", "reason": "合成复核"}
                   for sheet in ("产量", "品质") for row in (2, 3)]
    annotations.extend([{"reason": "缺行号"}, "not-an-object"])
    text, artifact = audit_uploaded_data.func(annotations_json=annotations)
    assert len(saved) == 1
    assert artifact["_tool_outcome"]["status"] == "partial"
    assert artifact["reason_code"] == "coverage_incomplete"
    result = artifact["results"][0]
    assert result["annotated"] == 4 and result["unannotated"] == 0
    assert len(result["annotation_errors"]) == 2
    assert "未采用标注" in text and "实际收到的键" in text
    book = load_workbook(io.BytesIO(saved[0]["content"]))
    assert len(list(book["问题清单"].iter_rows(min_row=2, values_only=True))) == 4
    assert any("未采用标注" in str(row[1].value) for row in book["审查概览"].iter_rows(min_row=2))


def test_unknown_named_sheet_is_reported_instead_of_silently_dropping_annotations(monkeypatch):
    saved = []
    _patch_two_sheets(monkeypatch, saved)
    text, artifact = audit_uploaded_data.func(annotations_json=[
        {"row": 2, "sheet": "不存在", "verdict": "错误", "reason": "合成未匹配条目"}])
    assert "不存在" in text and "可用工作表：产量、品质" in text
    assert artifact["_tool_outcome"]["status"] == "partial"
    assert artifact["results"][0]["skipped"][0]["sheet"] == "不存在"
    assert len(saved) == 1


def test_all_invalid_annotations_do_not_fall_back_to_a_rule_scan(monkeypatch):
    saved = []
    _patch_two_sheets(monkeypatch, saved)
    with pytest.raises(ValueError, match="没有可采用的标注") as failure:
        audit_uploaded_data.func(annotations_json=[{"reason": "缺行号"}, "not-an-object"])
    assert "第 1 个" in str(failure.value) and "第 2 个" in str(failure.value)
    assert saved == []


def test_manual_annotation_receipt_keeps_true_parse_truncation_partial(monkeypatch):
    source = _two_sheet_workbook()
    saved = []
    monkeypatch.setattr(research_tools, "get_request_tables",
                        lambda: [{"name": "试验.xlsx", "data": source}])
    monkeypatch.setattr(research_tools, "_MAX_TABLE_ROWS", 1)
    monkeypatch.setattr(research_tools, "_save_artifact",
                        lambda name, data, mime, **kw: saved.append(data) or "/download/1")
    _text, artifact = audit_uploaded_data.func(annotations_json=[
        {"row": 2, "sheet": sheet, "verdict": "正常", "reason": "合成已读行"}
        for sheet in ("产量", "品质")])
    assert len(saved) == 1
    assert artifact["_tool_outcome"]["status"] == "partial"
    coverage = artifact["results"][0]["coverage"]
    assert coverage["input_rows"] == 4 and coverage["processed_rows"] == 2
    assert coverage["unannotated_rows"] == 2
    assert coverage["truncation_reasons"]


def test_manual_verdicts_keep_their_severity_in_the_shared_workbook_writer(monkeypatch):
    saved = []
    _patch_two_sheets(monkeypatch, saved)
    audit_uploaded_data.func(annotations_json=[
        {"row": 2, "sheet": "产量", "verdict": "正常", "reason": "合成结论甲"},
        {"row": 3, "sheet": "产量", "verdict": "可疑", "reason": "合成结论乙"},
        {"row": 2, "sheet": "品质", "verdict": "错误", "reason": "合成结论丙"},
    ])
    book = load_workbook(io.BytesIO(saved[0]["content"]))
    rows = list(book["问题清单"].iter_rows(min_row=2, values_only=True))
    severities = {(row[1], row[3]): row[7] for row in rows}
    assert severities == {("产量", 2): "正常", ("产量", 3): "需核查", ("品质", 2): "错误"}
    normal = book["产量"]["A2"]
    review = book["产量"]["A3"]
    error = book["品质"]["A2"]
    assert len({normal.fill.fgColor.rgb, review.fill.fgColor.rgb, error.fill.fgColor.rgb}) == 3
    assert "正常：合成结论甲" in normal.comment.text
    assert normal.value == "纽荷尔" and book["产量"]["B2"].value == 42


def test_annotated_guide_page_carries_no_wall_clock():
    content, _stats = _write_annotated_xlsx(
        "产量.xlsx", _frame(), _meta(),
        [{"row": 2, "level": "ok", "verdict": "正常", "reason": "合成", "field": ""}],
        note="测试标注")
    book = load_workbook(io.BytesIO(content))
    keys = [str(row[0].value) for row in book["标注说明"].iter_rows(min_row=2)]
    assert "生成时间" not in keys

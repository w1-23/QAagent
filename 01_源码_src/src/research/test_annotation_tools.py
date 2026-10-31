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
                      "reason": "与原文一致", "field": ""}]


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


def test_audit_tool_writes_标注_artifact_and_leaves_source_untouched(monkeypatch):
    source_bytes = b"raw-xlsx-bytes-should-not-change"
    tables = [{"name": "产量.xlsx", "data": source_bytes}]
    saved = []

    def fake_save(filename, content, mime):
        saved.append({"filename": filename, "content": content, "mime": mime})
        return f"/download/{len(saved)}"

    monkeypatch.setattr(research_tools, "get_request_tables", lambda: tables)
    monkeypatch.setattr(research_tools, "_save_artifact", fake_save)
    monkeypatch.setattr(
        research_tools, "_load_frame",
        lambda index, sheet_name="", header_row=1, header_rows=None: (
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
    assert saved[0]["filename"] == "产量_Sheet1_标注.xlsx"
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
                        lambda: [{"name": "产量.xlsx", "data": b"x"}])
    monkeypatch.setattr(research_tools, "_save_artifact",
                        lambda filename, content, mime: saved.append(filename) or "/download/1")
    monkeypatch.setattr(
        research_tools, "_load_frame",
        lambda index, sheet_name="", header_row=1, header_rows=None: (_frame(), _meta("产量")),
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

    def fake_save(filename, content, mime):
        saved.append({"filename": filename, "content": content})
        return f"/download/{len(saved)}"

    def fake_load(index, sheet_name="", header_row=1, header_rows=None):
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
    assert book.sheetnames == ["产量", "品质", "问题清单", "审查概览"]
    assert tables[0]["data"] is source_bytes

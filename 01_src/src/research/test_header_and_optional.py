"""表头归一化（叶子列名/自动识别）与备注列默认可选口径的回归测试。

对应两处由日志举证的缺陷：
1. 多级表头被拼成「分组 / 字段名」，导致按列名配置的 optional_fields / ranges /
   relations / key_columns 全部匹配不上，整列被误判缺失并错标到相邻列（A07）。
2. 模型在自然语言规则下漏传 optional_fields，备注列被逐格标成缺失（A01/A02/A09）。
"""
from __future__ import annotations

import io

import pandas as pd
import pytest
from openpyxl import Workbook

from src.research.context import reset_request_tables, set_request_tables
from src.research.tools import (
    _audit_frame,
    _is_annotation_column,
    _load_frame,
    _name_set,
    _resolve_headers,
    inspect_uploaded_table,
)

MULTI_HEADER_ROWS = [
    ["模拟数据 · 多级表头的果实品质", None, None, None, None, None, None],
    ["业务字段", "业务字段", "业务字段", "测定字段", "测定字段", "测定字段", "测定字段"],
    ["RecordID", "SampleID", "Variety", "Brix_deg", "TA_pct", "FruitMass_g", "Note"],
    ["多级-808-0001", "S001", "宫川", 12.5, 1.1, 210.4, None],
    ["多级-808-0002", "S002", "宫川", 13.1, 1.2, 205.8, None],
    ["多级-808-0003", "S003", "宫川", None, 1.05, 199.2, "复核"],
    ["多级-808-0004", "S004", "宫川", 14.0, "12.6mg/L", 220.0, None],
    ["多级-808-0005", "S005", "宫川", 11.8, 1.3, 208.7, None],
    ["多级-808-0006", "S006", "宫川", 12.9, 1.15, 214.2, None],
]


def _frame_with_columns(columns, rows):
    return pd.DataFrame(rows, columns=columns)


# ---------------------------------------------------------------- 表头归一化

def test_multilevel_header_uses_leaf_names():
    labels, used = _resolve_headers(MULTI_HEADER_ROWS, [2, 3], 7)
    assert used == [2, 3]
    assert labels == ["RecordID", "SampleID", "Variety", "Brix_deg", "TA_pct",
                      "FruitMass_g", "Note"]


def test_single_title_row_is_auto_detected_instead_of_degrading():
    # 模型只传 header_row=1（标题行）时，不能把标题当成唯一列名。
    labels, used = _resolve_headers(MULTI_HEADER_ROWS, [1], 7)
    assert max(used) == 3
    assert labels == ["RecordID", "SampleID", "Variety", "Brix_deg", "TA_pct",
                      "FruitMass_g", "Note"]


def test_single_header_row_is_left_untouched():
    rows = [["RecordID", "Brix_deg", "Note"], ["r1", 12.5, None], ["r2", 13.0, "ok"]]
    labels, used = _resolve_headers(rows, [1], 3)
    assert used == [1]
    assert labels == ["RecordID", "Brix_deg", "Note"]


def test_repeated_leaf_names_fall_back_to_group_path():
    rows = [
        ["组A", "组A", "组B", "组B"],
        ["Value", "Unit", "Value", "Unit"],
        ["1", "g", "2", "ml"],
    ]
    labels, used = _resolve_headers(rows, [1, 2], 4)
    assert used == [1, 2]
    assert labels[0] == "Value" and labels[2] == "组B / Value"
    assert len(set(labels)) == len(labels)


def test_merged_group_label_is_carried_to_missing_leaf():
    # 分组格里只有最左列有值（Excel 合并表头），叶子格为空时沿用分组名。
    rows = [["业务字段", None, None], ["RecordID", "SampleID", "Variety"]]
    labels, _ = _resolve_headers(rows, [1, 2], 3)
    assert labels == ["RecordID", "SampleID", "Variety"]


def test_unnameable_column_is_marked():
    rows = [["RecordID", None], ["r1", 1.0], ["r2", 2.0]]
    labels, _ = _resolve_headers(rows, [1], 2)
    assert labels[0] == "RecordID"
    assert labels[1].startswith("未命名列")


def test_load_frame_exposes_leaf_columns_for_xlsx():
    wb = Workbook()
    ws = wb.active
    ws.title = "多级表头品质"
    for row in MULTI_HEADER_ROWS:
        ws.append(row)
    payload = io.BytesIO()
    wb.save(payload)
    token = set_request_tables([{"name": "multi.xlsx", "data": payload.getvalue()}])
    try:
        # 模型只给 header_row=1（标题行）——应自动识别到第 3 行，且列名是叶子名
        frame, meta = _load_frame(0, header_row=1)
        assert list(frame.columns) == ["RecordID", "SampleID", "Variety", "Brix_deg",
                                       "TA_pct", "FruitMass_g", "Note"]
        assert max(meta["header_rows"]) == 3
        profile = getattr(inspect_uploaded_table, "func", inspect_uploaded_table)(file_index=0)
        assert "Brix_deg" in profile
    finally:
        reset_request_tables(token)


# ---------------------------------------------------------------- 备注列口径

def test_is_annotation_column_matches_note_like_names():
    for name in ("Note", "notes", "备注", "备注（可选）", "Remark", "comment", "说明", "9.备注"):
        assert _is_annotation_column(name), name
    for name in ("NoteID", "Notes_count", "记录数", "SampleID", "", "备注列编码"):
        assert not _is_annotation_column(name), name


def test_name_set_accepts_string_json_and_sequence():
    assert _name_set("Note") == {"Note"}
    assert _name_set("Note, Remark") == {"Note", "Remark"}
    assert _name_set('["Note", "备注"]') == {"Note", "备注"}
    assert _name_set(["Note", None, " 备注 "]) == {"Note", "备注"}
    assert _name_set(None) == set()


def test_annotation_column_is_optional_by_default():
    frame = _frame_with_columns(["RecordID", "Brix_deg", "Note"], [
        ["r1", 12.5, None], ["r2", 13.1, None], ["r3", None, "复核"], ["r4", 14.0, None],
    ])
    findings, _ = _audit_frame(frame, {}, [], [], optional_fields=None)
    checks = [f["check"] for f in findings]
    assert "missing_cell" not in [f["check"] for f in findings if f["field"] == "Note"]
    assert "optional_column" in checks
    # 真问题仍然要报
    assert [f for f in findings if f["check"] == "missing_cell" and f["field"] == "Brix_deg"]


def test_annotation_column_with_rule_is_still_flagged():
    frame = _frame_with_columns(["RecordID", "Brix_deg", "Note"], [
        ["r1", 12.5, None], ["r2", 13.1, None], ["r3", 12.9, "复核"], ["r4", 14.0, None],
    ])
    findings, _ = _audit_frame(frame, {}, [], [], key_columns=["RecordID", "Note"])
    assert [f for f in findings if f["check"] == "missing_cell" and f["field"] == "Note"]
    assert not [f for f in findings if f["check"] == "optional_column"]


def test_required_fields_restores_cell_level_missing():
    frame = _frame_with_columns(["RecordID", "Brix_deg", "Note"], [
        ["r1", 12.5, None], ["r2", 13.1, None], ["r3", 12.9, "复核"], ["r4", 14.0, None],
    ])
    findings, _ = _audit_frame(frame, {}, [], [], required_fields=["备注"])
    # 列名是 Note，required_fields 写「备注」不匹配 → 仍是可选；写 Note 才生效
    assert "optional_column" in [f["check"] for f in findings]
    findings, _ = _audit_frame(frame, {}, [], [], required_fields=["Note"])
    assert [f for f in findings if f["check"] == "missing_cell" and f["field"] == "Note"]
    assert "optional_column" not in [f["check"] for f in findings]


def test_explicit_optional_fields_accepts_string_forms():
    frame = _frame_with_columns(["RecordID", "Brix_deg", "批次"], [
        ["r1", 12.5, None], ["r2", 13.1, "B1"], ["r3", 12.9, None], ["r4", 14.0, "B2"],
    ])
    for value in ("批次", "批次,备注", '["批次"]'):
        findings, _ = _audit_frame(frame, {}, [], [], optional_fields=value)
        assert not [f for f in findings if f["check"] == "missing_cell" and f["field"] == "批次"], value


def test_multi_header_audit_matches_rule_names():
    """A07 复现：三行表头 + 规则列名，必须按名命中 ranges/relations/key_columns。"""
    labels, used = _resolve_headers(MULTI_HEADER_ROWS, [1], 7)
    body = [(list(row) + [None] * 7)[:7] for row in MULTI_HEADER_ROWS[max(used):]]
    df = pd.DataFrame(body, columns=labels)
    findings, _ = _audit_frame(
        df, {}, [], [],
        row_offset=max(used) + 1,
        optional_fields=["Note"],
        ranges={"Brix_deg": {"min": 5, "max": 13.5}, "TA_pct": {"min": 0.1, "max": 3}},
        relations=[{"left": "Brix_deg", "op": ">", "right": "TA_pct"}],
        key_columns=["RecordID", "SampleID"],
    )
    out_of_range = [f for f in findings if f["check"] == "out_of_range"]
    assert [f["field"] for f in out_of_range] == ["Brix_deg"]
    assert out_of_range[0]["row"] == 7           # 第 7 行=14.0 > 13.5
    assert [f for f in findings if f["check"] == "missing_cell" and f["field"] == "Brix_deg"]
    assert [f for f in findings if f["check"] == "non_numeric_cell"
            and f["field"] == "TA_pct" and f["row"] == 7]
    # 备注列按可选处理，不再逐格染红
    assert not [f for f in findings if f["check"] == "missing_cell" and f["field"] == "Note"]


# ------------------------------------------------ 错误必须可行动（列出可用值）


def test_missing_worksheet_error_lists_the_available_names():
    import io

    import pytest
    from openpyxl import Workbook

    from src.research.context import reset_request_tables, set_request_tables
    from src.research.tools import _load_frame

    book = Workbook()
    book.active.title = "合成表A"
    book.active.append(["列甲", "列乙"])
    book.active.append([1, 2])
    book.create_sheet("合成表B").append(["列甲", "列乙"])
    buffer = io.BytesIO()
    book.save(buffer)
    token = set_request_tables([{"name": "合成.xlsx", "data": buffer.getvalue()}])
    try:
        with pytest.raises(ValueError) as excinfo:
            _load_frame(0, sheet_name="表 S1")
    finally:
        reset_request_tables(token)
    message = str(excinfo.value)
    assert "表 S1" in message
    assert "合成表A" in message and "合成表B" in message
    assert "inspect_uploaded_table" in message


def test_invalid_attachment_index_lists_legal_indexes_and_names():
    import pytest

    from src.research.context import reset_request_tables, set_request_tables
    from src.research.tools import _attachment

    token = set_request_tables([{"name": "甲.xlsx", "data": b"x"},
                                {"name": "乙.csv", "data": b"y"}])
    try:
        with pytest.raises(ValueError) as excinfo:
            _attachment(5)
    finally:
        reset_request_tables(token)
    message = str(excinfo.value)
    assert "0=甲.xlsx" in message and "1=乙.csv" in message


def test_json_argument_error_echoes_the_received_type_and_expected_shape():
    import pytest

    from src.core.tool_errors import ToolArgumentError
    from src.research.tools import _json_arg, _parse_header_rows

    with pytest.raises(ToolArgumentError) as grouped:
        _parse_header_rows('{"表A": [1,2]}', 1)
    assert "dict" in str(grouped.value) and "扁平" in str(grouped.value)

    with pytest.raises(ToolArgumentError) as wrong_element:
        _json_arg('[1,2]', dict, {}, shape='{"ranges":{…}}')
    assert "list" in str(wrong_element.value) and "ranges" in str(wrong_element.value)


def test_header_row_error_shows_what_arrived():
    import pytest

    from src.research.tools import _parse_header_rows

    with pytest.raises(ValueError) as excinfo:
        _parse_header_rows('["1","2"]', 1)
    assert "[1,2]" in str(excinfo.value) or '\\"1\\"' in str(excinfo.value)

# -*- coding: utf-8 -*-
"""标注 Excel 的数据审查测试（不联网、不调模型、不碰真实数据目录）。

覆盖需求 B5 的十种情形：重复整行、合法重复值、必填/允许缺失、离群、
多 Sheet、公式与合并单元格、同一单元格双问题、无异常表、仅列级信号、
重新打开后原值/公式/坐标不变。
"""

import io as _io
import pathlib

import pytest
from openpyxl import Workbook, load_workbook

from src.core.data_audit_xlsx import annotate_workbook
from src.research import tools


def _make(path, sheets):
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    wb.save(path)
    return path


def _audit(frame, groups=None, **kwargs):
    findings, _ = tools._audit_frame(frame, {}, list(groups or []), [], row_offset=2, **kwargs)
    return findings


def _frame(path, sheet=""):
    import pandas as pd
    return pd.read_excel(path, sheet_name=sheet or 0),


def test_duplicate_full_row_is_flagged_cell_level(tmp_path):
    path = _make(tmp_path / "dup.xlsx", {"S": [
        ["样本", "处理", "值"],
        ["S01", "CK", 12.4],
        ["S01", "CK", 12.4],
        ["S02", "T1", 13.1],
    ]})
    frame = tools._pd().read_excel(path)
    findings = _audit(frame)
    dup = [f for f in findings if f["check"] == "duplicate_row"]
    assert len(dup) == 2 and {f["row"] for f in dup} == {2, 3}


def test_legal_repeated_values_are_not_duplicate_rows(tmp_path):
    path = _make(tmp_path / "rep.xlsx", {"S": [
        ["样本", "处理", "值"],
        ["S01", "CK", 12.4],
        ["S02", "CK", 12.4],
        ["S03", "T1", 12.4],
    ]})
    frame = tools._pd().read_excel(path)
    assert [f for f in _audit(frame) if f["check"] == "duplicate_row"] == []


def test_required_missing_flagged_but_optional_not(tmp_path):
    path = _make(tmp_path / "miss.xlsx", {"S": [
        ["样本", "值", "备注"],
        ["S01", 12.4, None],
        ["S02", None, "重复测量"],
    ]})
    frame = tools._pd().read_excel(path)
    findings = _audit(frame, optional_fields=["备注"])
    missing = [f for f in findings if f["check"] == "missing_cell"]
    assert len(missing) == 1 and missing[0]["field"] == "值" and missing[0]["row"] == 3
    # 备注列允许缺失 → 不再报
    assert all(f["field"] != "备注" for f in missing)


def test_grouped_iqr_outlier(tmp_path):
    values = [10.0, 10.2, 9.8, 10.1, 10.3, 9.9, 10.0, 10.2, 98.7, 10.1]
    rows = [["样本", "处理", "值"]] + [[f"S{i:02d}", "CK", v] for i, v in enumerate(values, 1)]
    path = _make(tmp_path / "out.xlsx", {"S": rows})
    frame = tools._pd().read_excel(path)
    findings = _audit(frame, groups=["处理"])
    outliers = [f for f in findings if f["check"] == "iqr_outlier"]
    assert outliers and all("IQR" in f["detail"] for f in outliers)
    assert any("98.7" in f["detail"] for f in outliers)
    # 需求 B3：离群必须说明规则、阈值和分组口径
    assert all("口径：处理=CK" in f["detail"] for f in outliers)
    assert all("1.5×IQR" in f["detail"] for f in outliers)
    assert all("n=" in f["detail"] for f in outliers)


def test_small_group_is_marked_unjudgeable_not_silently_skipped(tmp_path):
    """分组样本不足 8 时不做推断，但要留一条可追溯的说明（需求 B3）。"""
    rows = [["样本", "处理", "值"]]
    rows += [[f"S{i:02d}", "CK", 10.0 + i * 0.1] for i in range(9)]
    rows += [[f"T{i:02d}", "T1", 20.0 + i * 0.1] for i in range(3)]
    path = _make(tmp_path / "small.xlsx", {"S": rows})
    frame = tools._pd().read_excel(path)
    findings = _audit(frame, groups=["处理"])
    assert [f for f in findings if f["check"] == "iqr_outlier"] == []
    notes = [f for f in findings if f["check"] == "iqr_insufficient"]
    assert len(notes) == 1
    assert "处理=T1 n=3" in notes[0]["detail"]
    assert notes[0]["severity"] == "info" and notes[0]["row"] == ""


def test_multi_sheet_annotated_separately(tmp_path):
    src = _make(tmp_path / "multi.xlsx", {
        "产量": [["样本", "值"], ["S01", 12.4], ["S02", None]],
        "品质": [["样本", "糖度"], ["S01", 11.2], ["S01", 11.2]],
    })
    data = src.read_bytes()
    frame = tools._pd().read_excel(src, sheet_name="产量")
    findings = _audit(frame)
    content, stats = annotate_workbook(data, sheet_name="产量", header_row=1, findings=findings,
                                       row_count=2, column_count=2)
    wb = load_workbook(_io.BytesIO(content))
    assert "问题清单" in wb.sheetnames and "审查概览" in wb.sheetnames
    assert wb["产量"]["B3"].value is None            # 空值不写入字符串
    assert wb["产量"]["B3"].comment is not None      # 但有着色与批注
    assert wb["品质"]["B2"].comment is None          # 另一张表未被误标


def test_formulas_and_merged_cells_survive(tmp_path):
    src = _make(tmp_path / "f.xlsx", {"S": [["样本", "值", "均值"], ["S01", 12.4, None]]})
    wb = load_workbook(src)
    ws = wb["S"]
    ws["C2"] = "=B2*2"
    ws.merge_cells("A4:B4")
    ws["A4"] = "备注"
    wb.save(src)
    data = src.read_bytes()
    content, _ = annotate_workbook(data, sheet_name="S", header_row=1,
                                  findings=[{"check": "missing_cell", "field": "值", "row": 9,
                                             "detail": "x"}], row_count=1, column_count=3)
    out = load_workbook(_io.BytesIO(content))["S"]
    assert out["C2"].value == "=B2*2"
    assert "A4:B4" in [str(r) for r in out.merged_cells.ranges]


def test_two_rules_on_same_cell_are_both_recorded(tmp_path):
    src = _make(tmp_path / "two.xlsx", {"S": [["样本", "值"], ["S01", 999]]})
    data = src.read_bytes()
    findings = [
        {"check": "out_of_range", "field": "值", "row": 2, "detail": "值 999 超出预设合理范围 [0, 100]"},
        {"check": "logic_conflict", "field": "值", "row": 2, "detail": "与对照列冲突"},
    ]
    content, stats = annotate_workbook(data, sheet_name="S", header_row=1, findings=findings,
                                       row_count=1, column_count=2)
    assert stats["issues"] == 2 and stats["cells"] == 1
    ws = load_workbook(_io.BytesIO(content))
    comment = ws["S"]["B2"].comment.text
    assert "越界" in comment and "逻辑冲突" in comment
    issue_rows = list(ws["问题清单"].iter_rows(min_row=2, values_only=True))
    assert len(issue_rows) == 2


def test_clean_table_has_no_issues(tmp_path):
    rows = [["样本", "值"]] + [[f"S{i:02d}", 10 + i * 0.1] for i in range(1, 13)]
    path = _make(tmp_path / "clean.xlsx", {"S": rows})
    frame = tools._pd().read_excel(path)
    cell_level = [f for f in _audit(frame) if f["check"] in
                  {"missing_cell", "duplicate_row", "non_numeric_cell", "iqr_outlier",
                   "out_of_range", "logic_conflict", "duplicate_key"}]
    assert cell_level == []


def test_column_level_signal_not_painted(tmp_path):
    src = _make(tmp_path / "cols.xlsx", {"S": [
        ["样本", "A", "B"], ["S01", 1.0, 2.0], ["S02", 2.0, 4.0], ["S03", 3.0, 6.0],
        ["S04", 4.0, 8.0], ["S05", 5.0, 10.0]]})
    data = src.read_bytes()
    frame = tools._pd().read_excel(src)
    findings = _audit(frame)
    ratio = [f for f in findings if f["check"] == "constant_ratio_columns"]
    assert ratio and ratio[0]["row"] == ""
    content, _ = annotate_workbook(data, sheet_name="S", header_row=1, findings=findings,
                                   row_count=5, column_count=3)
    wb = load_workbook(_io.BytesIO(content))
    painted = [c.coordinate for row in wb["S"].iter_rows() for c in row if c.comment is not None]
    assert painted == []                       # 不整列染红
    rules = [r[8] for r in wb["问题清单"].iter_rows(min_row=2, values_only=True)]
    assert "constant_ratio_columns" in rules   # 但进了问题清单


def test_reopened_workbook_keeps_values_and_coordinates(tmp_path):
    src = _make(tmp_path / "reopen.xlsx", {"S": [["样本", "值"], ["S01", 12.4], ["S02", None]]})
    data = src.read_bytes()
    frame = tools._pd().read_excel(src)
    content, _ = annotate_workbook(data, sheet_name="S", header_row=1,
                                   findings=_audit(frame), row_count=2, column_count=2)
    ws = load_workbook(_io.BytesIO(content))["S"]
    assert ws["A1"].value == "样本" and ws["B2"].value == 12.4
    assert ws["B3"].value is None and ws["B3"].fill.fgColor.rgb.endswith("FDE2E1")
    # 原始文件本身没有被改写
    assert load_workbook(src)["S"]["B3"].fill.fgColor.rgb != "00FDE2E1"


def test_ranges_and_relations_from_user(tmp_path):
    rows = [["样本", "低", "高", "值"], ["S01", 10, 20, 25], ["S02", 10, 20, 15]]
    path = _make(tmp_path / "rule.xlsx", {"S": rows})
    frame = tools._pd().read_excel(path)
    findings = _audit(frame,
                      ranges={"值": {"min": 0, "max": 20}},
                      relations=[{"left": "低", "op": "<=", "right": "高"}])
    kinds = {f["check"] for f in findings}
    assert "out_of_range" in kinds
    assert not any(f["check"] == "logic_conflict" for f in findings)


def test_tool_end_to_end_writes_artifact(tmp_path, monkeypatch):
    src = _make(tmp_path / "e2e.xlsx", {"S": [["样本", "值"], ["S01", 12.4], ["S02", None]]})
    data = src.read_bytes()
    monkeypatch.setattr(tools, "get_request_tables",
                        lambda: [{"name": "e2e.xlsx", "data": data}])
    saved = {}
    monkeypatch.setattr(tools, "_save_artifact",
                        lambda name, content, mime: saved.setdefault("name", name) or "artifact/e2e.xlsx")
    text = tools.audit_uploaded_data.func(0)
    assert "待核查信号" in text and "e2e" in text
    assert saved["name"].endswith("_数据审查.xlsx")

def test_empty_column_is_reported_once_and_located_by_excel_letter(tmp_path):
    """整列为空（公式列/预留列）只报一条「空列」，并定位到 Excel 列字母。"""
    path = _make(tmp_path / "empty.xlsx", {"产量": [
        ["品种", "单果重g", "备注"],
        ["脐橙", 210, None],
        ["砂糖橘", 65, None],
        ["温州蜜柑", 95, None],
    ]})
    frame = tools._pd().read_excel(path)
    findings = _audit(frame)
    empty = [f for f in findings if f["check"] == "empty_column"]
    assert len(empty) == 1
    assert not [f for f in findings if f["check"] == "missing_cell" and f["field"] == "备注"]

    payload, stats = annotate_workbook(
        path.read_bytes(), sheet_name="产量", header_row=1, findings=findings)
    book = load_workbook(_io.BytesIO(payload))
    issues = list(book["问题清单"].iter_rows(min_row=2, values_only=True))
    rows = [row for row in issues if row[5] == "空列"]
    assert len(rows) == 1 and rows[0][2] == "C列" and rows[0][8] == "empty_column"


def test_column_level_missing_only_when_mostly_empty(tmp_path):
    """少数缺失只逐格标注；大面积缺失（>=50%）才额外报一条列级提示。"""
    mostly = _make(tmp_path / "mostly.xlsx", {"S": [
        ["品种", "备注"], ["a", None], ["b", None], ["c", None], ["d", "有"]]})
    few = _make(tmp_path / "few.xlsx", {"S": [
        ["品种", "备注"], ["a", None], ["b", "x"], ["c", "y"], ["d", "z"]]})
    for path, expect in ((mostly, True), (few, False)):
        findings = _audit(tools._pd().read_excel(path))
        column_level = [f for f in findings if f["check"] == "missing_values"]
        assert bool(column_level) is expect
        assert [f for f in findings if f["check"] == "missing_cell"]

def test_secondary_header_row_is_not_painted_as_type_errors(tmp_path):
    """多行表头时，第二行是“土壤水分(%)”这类副表头：不应逐格染红（噪音）。"""
    rows = [["样本", "土壤水分(%)", "pH"]]
    rows += [[f"S{i:02d}", 30 + i, 6.1 + i * 0.05] for i in range(1, 9)]
    path = _make(tmp_path / "two_row_header.xlsx", {"S": rows})
    import pandas as pd
    df = pd.read_excel(path)                     # 默认把第一行当表头，副表头成了数据第 0 行
    frame = pd.concat([pd.DataFrame([["样本", "含水量", "酸碱度"]], columns=df.columns), df],
                      ignore_index=True)
    findings = _audit(frame)
    assert [f for f in findings if f["check"] == "non_numeric_cell" and f["row"] == 2] == []
    notes = [f for f in findings if f["check"] == "multi_header_suspected"]
    assert len(notes) == 1 and "疑似二级表头" in notes[0]["detail"]
    assert notes[0]["row"] == ""            # 只进问题清单，不定位到格子


def test_row_level_finding_marks_every_data_cell_of_that_row(tmp_path):
    """行级问题（完全重复行）以前只在问题清单里有，原始单元格一个都不标；
    现在必须把该行所有数据列都标红，用户才能在表里直接定位。"""
    path = _make(tmp_path / "row.xlsx", {"S": [
        ["样本", "处理", "值"],
        ["S01", "CK", 12.4],
        ["S01", "CK", 12.4],
        ["S02", "T1", 13.1],
    ]})
    frame = tools._pd().read_excel(path)
    findings = _audit(frame)
    content, stats = annotate_workbook(path.read_bytes(), sheet_name="S", header_row=1,
                                       findings=findings, row_count=3, column_count=3)
    wb = load_workbook(_io.BytesIO(content))
    ws = wb["S"]
    marked = sorted(c.coordinate for row in ws.iter_rows() for c in row
                    if c.fill and c.fill.patternType == "solid" and c.comment is not None)
    # 两行内容完全一致，两行都算重复：A2:C2 与 A3:C3 都要标；表头不标。
    assert marked == ["A2", "A3", "B2", "B3", "C2", "C3"], marked
    assert stats["cells"] == 6
    issue = wb["问题清单"]
    cells = [issue.cell(row=r, column=3).value for r in range(2, issue.max_row + 1)]
    assert "第 3 行（整行）" in cells


def test_kind_labels_are_chinese_for_audit_checks():
    from src.core.data_audit_xlsx import _kind
    for check in ("iqr_insufficient", "iqr_outlier", "mixed_numeric_text", "duplicate_row"):
        label, severity, action = _kind(check)
        assert label != check, check
        assert severity and action


def test_preview_renderer_reads_flat_sheetjs_fill():
    """预览层必须认 SheetJS 读 xlsx 时的扁平填充形态（s.patternType + s.fgColor），
    否则标注色在浏览器里一块都显示不出来。"""
    src = pathlib.Path("web/assets/session_files.js").read_text(encoding="utf-8")
    assert "cellStyles: true" in src
    assert "st.patternType" in src and "st.fgColor" in src
    assert "sf-cell-boxed" in src and "sf-cell-mark" in src

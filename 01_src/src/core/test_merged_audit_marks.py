"""Merged-cell annotations preserve source geometry and every issue coordinate."""
import io

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment

from src.core.data_audit_xlsx import annotate_workbook


def test_merged_anchor_and_member_issues_share_a_writable_comment():
    wb = Workbook()
    ws = wb.active
    ws.title = "S"
    ws.append(["样本", "值", "公式"])
    ws.append(["原始备注", None, "=1+1"])
    ws.merge_cells("A2:B2")
    ws["A2"].comment = Comment("原有批注", "原作者")
    raw = io.BytesIO()
    wb.save(raw)
    payload, stats = annotate_workbook(raw.getvalue(), sheet_name="S", header_row=1,
        findings=[
            {"check": "missing_cell", "field": "样本", "row": 2, "detail": "锚点信号"},
            {"check": "missing_cell", "field": "值", "row": 2, "detail": "成员信号"},
        ])
    out = load_workbook(io.BytesIO(payload))
    assert str(next(iter(out["S"].merged_cells.ranges))) == "A2:B2"
    assert out["S"]["A2"].value == "原始备注"
    assert out["S"]["C2"].value == "=1+1"
    comment = out["S"]["A2"].comment.text
    assert all(t in comment for t in ["原有批注", "锚点信号", "成员信号"])
    assert stats["issues"] == 2
    assert {r[2] for r in out["问题清单"].iter_rows(min_row=2, values_only=True)} == {"A2", "B2"}
    assert load_workbook(io.BytesIO(raw.getvalue()))["S"]["A2"].comment.text == "原有批注"

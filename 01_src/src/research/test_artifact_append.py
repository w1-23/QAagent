# -*- coding: utf-8 -*-
"""Append-mode deliverable tests: one file keeps accumulating instead of new files."""
import io

from openpyxl import Workbook, load_workbook

from src.research.tools import _merge_artifact_bytes


def _xlsx(rows):
    wb = Workbook(); ws = wb.active
    ws.append(["name", "value"])
    for row in rows:
        ws.append(row)
    out = io.BytesIO(); wb.save(out)
    return out.getvalue()


def test_xlsx_append_keeps_single_header(tmp_path):
    first = tmp_path / "table.xlsx"
    first.write_bytes(_xlsx([["a", 1], ["b", 2]]))
    merged = _merge_artifact_bytes(first, _xlsx([["c", 3]]), "application/xlsx")
    wb = load_workbook(io.BytesIO(merged))
    values = list(wb.active.iter_rows(values_only=True))
    assert values[0] == ("name", "value")
    assert [row[0] for row in values[1:]] == ["a", "b", "c"]


def test_html_append_before_body_close(tmp_path):
    page = tmp_path / "report.html"
    page.write_text("<html><body><h1>x</h1></body></html>", encoding="utf-8")
    merged = _merge_artifact_bytes(page, "<p>more</p>".encode("utf-8"), "text/html")
    text = merged.decode("utf-8")
    assert text.index("<p>more</p>") < text.index("</body>")
    assert "<h1>x</h1>" in text


def test_markdown_append_is_plain_text(tmp_path):
    doc = tmp_path / "note.md"
    doc.write_text("# a", encoding="utf-8")
    merged = _merge_artifact_bytes(doc, b"## b", "text/markdown")
    assert merged.decode("utf-8").strip().endswith("## b")

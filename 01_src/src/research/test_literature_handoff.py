import os

import pytest
from openpyxl import Workbook, load_workbook

from src.research import tools as research_tools


def _verification_book(path):
    wb = Workbook()
    ws = wb.active
    ws.title = "核验"
    ws.append(["DOI", "状态", "输入标题", "登记标题"])
    ws.append(["10.3390/plants11070967", "metadata_matched", "A", "A"])
    ws.append(["10.1016/j.lwt.2023.114586", "title_candidate", "B", "B"])
    wb.save(path)


def test_handoff_copies_verification_sheet_verbatim(tmp_path, monkeypatch):
    source = tmp_path / "verify.xlsx"
    _verification_book(source)
    monkeypatch.setattr(research_tools, "_latest_verification_artifact", lambda: source)
    body, count = research_tools._copy_workbook_cells(source, "核验表")
    assert count == 2
    out = tmp_path / "out.xlsx"
    out.write_bytes(body)
    ws = load_workbook(out)["核验"]
    assert [cell.value for cell in ws[1]][:3] == ["DOI", "状态", "输入标题"]
    assert ws.max_row == 3


def test_handoff_without_verification_raises_actionable_error(monkeypatch):
    monkeypatch.setattr(research_tools, "_latest_verification_artifact", lambda: None)
    func = getattr(research_tools.export_literature_evidence, "func",
                   research_tools.export_literature_evidence)
    with pytest.raises(Exception) as excinfo:
        func(title="核验表", evidence_rows_json="", output_format="xlsx",
             source="last_verification")
    assert "verify_uploaded_references" in str(excinfo.value)

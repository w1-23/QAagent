"""Reference handoff uses explicit durable audits, never a latest workbook."""
import io

import pytest
from openpyxl import load_workbook

from src.core.tool_errors import ToolArgumentError
from src.research import reference_audit as audit, tools as research_tools
from src.research.reference_tools_test import reference_workspace, workspace  # noqa: F401


def test_handoff_exports_every_original_row_from_selected_audit(reference_workspace):
    state = reference_workspace
    entries = [{"citation": "Original duplicate", "doi": "10.1234/synthetic", "title": "Synthetic title"},
               {"citation": "Original duplicate", "doi": "10.1234/synthetic", "title": "Synthetic title"},
               {"citation": "Original without DOI", "title": "No DOI title"}]
    selected = audit.create_audit(101, state.session_id, references=entries)["audit_id"]
    audit.run_audit(101, state.session_id, selected, max_rows=2)
    audit.create_audit(101, state.session_id, references=[{"title": "Newer unrelated audit"}])
    content, _, _ = audit.export_audit(101, state.session_id, selected, format="xlsx")
    book = load_workbook(io.BytesIO(content))
    sheet = book["References"]
    rows = list(sheet.iter_rows(values_only=True))
    assert len(rows) == 4
    assert sum("Original duplicate" in row for row in rows[1:]) == 2
    assert any("Original without DOI" in row for row in rows[1:])
    assert not any("Newer unrelated audit" in row for row in rows[1:])
    book.close()


def test_handoff_without_explicit_audit_raises_actionable_error(monkeypatch):
    monkeypatch.setattr(research_tools, "_save_artifact", lambda *a, **kw: pytest.fail("latest source must not publish"))
    with pytest.raises(ToolArgumentError, match="audit_id"):
        research_tools.export_literature_evidence.func(
            title="核验表", evidence_rows_json="", output_format="xlsx", source="last_verification")

"""Offline delivery contracts: real renderers and publication use temporary stores."""
import copy
import json
from pathlib import Path
import sqlite3

import pytest
from pydantic import ValidationError

from src.core.test_publication_reconcile import isolated, OWNER, SID  # noqa: F401
from src.core import tracing
from src.research import delivery_tools as delivery, tools
from src.session import files

BODY = "# Synthetic research report\n\n## Evidence\n\nThis is a complete synthetic report for offline delivery verification. No scientific conclusions are asserted.\n"
SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="50" viewBox="0 0 120 50"><title>Synthetic diagram</title><rect x="1" y="1" width="110" height="40" fill="blue"/></svg>'
REFERENCE = {"citation": "Synthetic citation", "title": "Synthetic title", "authors": "Alpha; Beta",
             "journal": "Synthetic journal", "year": 2026, "volume": "2", "issue": "1", "pages": "1-2",
             "claim": "Synthetic claim", "evidence": "Synthetic evidence", "source": "fixture",
             "doi": "10.1234/synthetic", "url": "https://example.invalid/synthetic"}
GAP = {"topic": "synthetic", "doi_or_title": "fixture", "failure": "not available",
       "local_result": "none", "source_url": "https://example.invalid"}
REF = {"kind": "analysis_result", "id": "analysis-id", "version": 2, "sha256": "a" * 64}


@pytest.mark.parametrize("name,args,expected", [
    ("export_paper_analysis", {"analysis_markdown": BODY, "title": "paper", "svg_code": SVG, "append": True},
     {"kind": "report", "purpose": "paper_analysis", "content": BODY, "svg_code": SVG, "append": True}),
    ("export_experiment_design", {"design_markdown": BODY, "title": "design", "svg_code": SVG, "append": True},
     {"kind": "report", "purpose": "experiment_design", "content": BODY, "svg_code": SVG, "append": True}),
    ("export_research_notes", {"project_name": "project", "limit": 77, "svg_code": SVG},
     {"kind": "research_notes", "project_name": "project", "limit": 77, "svg_code": SVG}),
    ("export_table", {"title": "table", "columns_json": '["A"]', "rows_json": '[[2]]', "output_format": "tsv", "append": True},
     {"kind": "table", "columns": ["A"], "rows": [[2]], "output_format": "tsv", "append": True}),
    ("export_table", {"title": "snapshot", "analysis_result_ref": REF, "result_section": "descriptive"},
     {"kind": "table", "analysis_result_ref": REF, "result_section": "descriptive"}),
    ("export_literature_evidence", {"title": "refs", "evidence_rows_json": json.dumps([REFERENCE]), "output_format": "bibtex", "append": True},
     {"kind": "bibliography", "references": [REFERENCE], "output_format": "bibtex", "append": True}),
    ("export_literature_evidence", {"title": "verified", "source": "verify"},
     {"kind": "bibliography", "source": "last_verification"}),
    ("export_corpus_gap_report", {"title": "gap", "gaps_json": json.dumps([GAP]), "append": True},
     {"kind": "corpus_gap", "gaps": [GAP], "append": True}),
])
def test_legacy_mapping_preserves_parameters_without_mutation(name, args, expected):
    before = copy.deepcopy(args)
    canonical, mapped = delivery.legacy_delivery_call(name, args)
    assert canonical == "deliver_research_artifact"
    assert args == before
    for key, value in expected.items():
        if key == "references":
            assert all(mapped[key][0][k] == v for k, v in value[0].items())
        else:
            assert mapped[key] == value
    if "title" in args:
        assert mapped["title"] == args["title"]


def test_mapping_rejects_unknown_arguments_and_leaves_editing_tools_alone():
    with pytest.raises(TypeError):
        delivery.legacy_delivery_call("export_table", {"title": "x", "unknown": 3})
    args = {"operation": "append"}
    assert delivery.legacy_delivery_call("word_document", args) == ("word_document", args)
    assert set(delivery.LEGACY_DELIVERY_NAMES) == {
        "export_paper_analysis", "export_experiment_design", "export_research_notes", "export_table",
        "export_literature_evidence", "export_corpus_gap_report"}


@pytest.mark.parametrize("payload", [
    {"kind": "bogus"}, {"kind": "report", "output_format": "docx"},
    {"kind": "table", "output_format": "pdf"}, {"kind": "bibliography", "output_format": "csv"},
    {"kind": "research_notes", "output_format": "xlsx"}, {"kind": "corpus_gap", "output_format": "html"},
    {"kind": "report", "purpose": "unknown"}, {"kind": "report", "rows": [[1]]},
    {"kind": "table", "columns": ["A"], "rows": [[{"nested": 1}]]},
    {"kind": "table", "analysis_result_ref": REF, "rows": [[1]]},
    {"kind": "bibliography", "source": "last_verification", "references": [REFERENCE]},
    {"kind": "corpus_gap", "gaps": []}, {"kind": "report", "append": "false"},
    {"kind": "report", "extra": "lost parameter"},
])
def test_invalid_requests_never_dispatch(monkeypatch, payload):
    monkeypatch.setattr(delivery, "_dispatch", lambda request: pytest.fail("invalid request reached renderer"))
    with pytest.raises(ValidationError):
        delivery.deliver_research_artifact.invoke(payload)


def test_tool_schema_and_context_are_preserved(monkeypatch):
    schema = delivery.deliver_research_artifact.args_schema.model_json_schema()
    assert schema["properties"]["kind"]["enum"] == list(delivery._FORMATS)
    assert schema["additionalProperties"] is False
    assert "references" in schema["properties"] and "rows" in schema["properties"]
    tracing.set_session_id("context_delivery_session")
    def renderer(**args):
        assert tracing.get_session_id() == "context_delivery_session"
        assert args["analysis_markdown"] == BODY
        return "[ERR_PARSE] synthetic failure"
    monkeypatch.setattr(tools.export_paper_analysis, "func", renderer)
    try:
        result = delivery.deliver_research_artifact.invoke({"args": {"kind": "report", "purpose": "paper_analysis", "content": BODY},
            "type": "tool_call", "id": "synthetic-call", "name": "deliver_research_artifact"})
        assert result.artifact["_tool_outcome"]["status"] == "failed"
    finally:
        tracing.set_session_id("")


def run(**kwargs):
    return delivery.deliver_research_artifact.func(**kwargs)


def stored_payload(root, receipt):
    with sqlite3.connect(tools._db_path()) as conn:
        path = Path(conn.execute("SELECT path FROM artifacts WHERE artifact_id=?", (receipt["artifact_id"],)).fetchone()[0])
    assert path.is_relative_to(root)
    entry = files.get_account_file(receipt["file_id"], OWNER)
    assert entry and entry["sha256"] == receipt["sha256"]
    assert files.get_account_file(receipt["file_id"], OWNER + 1) is None
    return path.read_bytes()


@pytest.mark.parametrize("kind", list(delivery._FORMATS))
def test_every_advertised_format_uses_real_renderer_and_library(isolated, kind):
    tracing.set_current_tool_name("deliver_research_artifact")
    if kind == "research_notes":
        tools.save_research_note.func(project_name="fixture", note=BODY)
    for fmt in delivery.supported_delivery_formats(kind):
        request = {"kind": kind, "output_format": fmt, "title": "synthetic-" + fmt}
        if kind == "report":
            request.update(content=BODY, svg_code=SVG if fmt == "svg" else "")
        elif kind == "table":
            request.update(columns=["Sample", "Value"], rows=[["Alpha", 1], ["Beta", 2]])
        elif kind == "bibliography":
            request["references"] = [REFERENCE]
        elif kind == "research_notes":
            request.pop("title")
            request.update(project_name="fixture", svg_code=SVG if fmt == "svg" else "")
        else:
            request["gaps"] = [GAP]
        text, artifact = run(**request)
        assert artifact["_tool_outcome"]["status"] == "complete", text
        extension = ".bib" if fmt == "bibtex" else "." + fmt
        receipt = next(r for r in artifact["receipts"] if r["filename"].endswith(extension))
        payload = stored_payload(isolated, receipt)
        if fmt in {"html", "svg"}:
            assert b"Synthetic" in payload and (b"</html>" if fmt == "html" else b"</svg>") in payload
        elif fmt in {"csv", "tsv"}:
            assert b"Alpha" in payload if kind == "table" else b"not available" in payload
        elif fmt == "xlsx":
            from openpyxl import load_workbook
            import io
            book = load_workbook(io.BytesIO(payload))
            assert book.active.max_row >= 2
            book.close()
        elif fmt == "ris":
            assert b"TY  - JOUR" in payload and b"AU  - Alpha" in payload and b"ER  - " in payload
        else:
            assert b"@article{" in payload and b"Alpha and Beta" in payload and b"10.1234/synthetic" in payload


@pytest.mark.parametrize("purpose", ["paper_analysis", "experiment_design"])
def test_report_presets_keep_real_html_and_svg(isolated, purpose):
    text, artifact = run(kind="report", purpose=purpose, content=BODY, svg_code=SVG)
    assert artifact["_tool_outcome"]["status"] == "complete", text
    assert {r["filename"].rsplit(".", 1)[-1] for r in artifact["receipts"]} == {"html", "svg"}


@pytest.mark.parametrize("text", ["", "当前账号没有课题记录可导出。", "已生成 HTML，没有真实下载链接。",
    "[ERR_PARSE] empty", "[DELIVERABLE_REJECTED:unbalanced_markup] incomplete",
    f"已生成 [下载](/api/v2/session/{SID}/artifacts/{'a' * 32})"])
def test_empty_errors_and_fabricated_success_never_succeed(isolated, monkeypatch, text):
    monkeypatch.setattr(delivery, "_dispatch", lambda request: text)
    content, artifact = run(kind="report", content=BODY)
    assert artifact["_tool_outcome"]["status"] == "failed"
    assert artifact["receipts"] == []
    assert "DELIVERY_FAILED" in content


def test_registration_failure_stays_pending_then_retry_recovers(isolated, monkeypatch):
    original = files.register_file
    monkeypatch.setattr(files, "register_file", lambda **kwargs: (_ for _ in ()).throw(OSError("synthetic register failure")))
    text, artifact = run(kind="table", title="retry", columns=["A"], rows=[[1]])
    assert artifact["_tool_outcome"] == {"status": "failed", "reason_code": "artifact_registration_failed", "artifacts": []}
    with sqlite3.connect(tools._db_path()) as conn:
        assert conn.execute("SELECT state FROM publication_outbox").fetchone()[0] == "pending"
    monkeypatch.setattr(files, "register_file", original)
    text, artifact = run(kind="table", title="retry", columns=["A"], rows=[[1]])
    assert artifact["_tool_outcome"]["status"] == "complete", text
    assert len(files.list_files(SID, OWNER)) == 1


def test_append_uses_existing_validation_and_immutable_publication(isolated):
    _, first = run(kind="table", title="append", columns=["A"], rows=[[1]])
    _, second = run(kind="table", title="append", columns=["A"], rows=[[2]], append=True)
    assert first["receipts"][0]["artifact_id"] != second["receipts"][0]["artifact_id"]
    assert b"2" not in stored_payload(isolated, first["receipts"][0])
    assert b"2" in stored_payload(isolated, second["receipts"][0])
    text, rejected = run(kind="table", title="append", columns=["Different"], rows=[[3]], append=True)
    assert rejected["_tool_outcome"]["status"] == "failed", text


@pytest.mark.parametrize("fmt", ["xlsx", "ris", "bibtex"])
def test_real_last_verification_reference_handoff(isolated, fmt):
    from openpyxl import Workbook
    import io
    book = Workbook()
    book.active.append(["DOI", "登记标题", "登记作者", "登记年份", "期刊", "来源链接", "状态"])
    book.active.append(["10.1234/synthetic", "Synthetic title", "Alpha", "2026", "Synthetic journal", "https://example.invalid", "synthetic"])
    buffer = io.BytesIO()
    book.save(buffer)
    tracing.set_current_tool_name("verify_uploaded_references")
    tools._save_artifact("verification.xlsx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    tracing.set_current_tool_name("deliver_research_artifact")
    text, artifact = run(kind="bibliography", title="handoff", source="last_verification", output_format=fmt)
    assert artifact["_tool_outcome"]["status"] == "complete", text
    assert stored_payload(isolated, artifact["receipts"][0])


def test_partial_svg_publication_preserves_html_receipt(isolated, monkeypatch):
    from src.core.deliverable_check import DeliverableRejected
    original = tools._save_artifact
    def publish(filename, content, mime, **kwargs):
        if filename.endswith(".svg"):
            raise DeliverableRejected("synthetic_svg_failure", "synthetic companion failure")
        return original(filename, content, mime, **kwargs)
    monkeypatch.setattr(tools, "_save_artifact", publish)
    text, artifact = run(kind="report", content=BODY, svg_code=SVG)
    assert artifact["_tool_outcome"]["status"] == "partial", text
    assert artifact["_tool_outcome"]["reason_code"] == "synthetic_svg_failure"
    assert len(artifact["receipts"]) == 1
    assert artifact["receipts"][0]["filename"].endswith(".html")


def test_table_analysis_reference_delegates_without_retyping(isolated, monkeypatch):
    from src.research import analysis_delivery
    calls = []
    def project(owner, ref, section):
        calls.append((owner, ref, section))
        return {"columns": ["field", "value"], "rows": [["precise", 1 / 7]], "analysis_result_ref": REF}
    monkeypatch.setattr(analysis_delivery, "result_table", project)
    text, artifact = run(kind="table", title="snapshot", analysis_result_ref=REF, result_section="descriptive")
    assert calls == [(OWNER, REF, "descriptive")]
    assert artifact["_tool_outcome"]["status"] == "complete", text
    assert str(1 / 7).encode() in stored_payload(isolated, artifact["receipts"][0])


def test_tampered_bytes_and_uncommitted_library_never_receive_success(isolated, monkeypatch):
    text, artifact = run(kind="report", content=BODY)
    receipt = artifact["receipts"][0]
    monkeypatch.setattr(delivery, "_dispatch", lambda request: text)
    with sqlite3.connect(tools._db_path()) as conn:
        conn.execute("UPDATE publication_outbox SET state='pending'")
    assert run(kind="report", content=BODY)[1]["_tool_outcome"]["status"] == "failed"
    with sqlite3.connect(tools._db_path()) as conn:
        conn.execute("UPDATE publication_outbox SET state='committed'")
        path = Path(conn.execute("SELECT path FROM artifacts WHERE artifact_id=?", (receipt["artifact_id"],)).fetchone()[0])
    path.write_bytes(b"tampered synthetic bytes")
    assert run(kind="report", content=BODY)[1]["_tool_outcome"]["status"] == "failed"


def test_actual_langchain_tool_call_returns_serializable_receipts(isolated):
    result = delivery.deliver_research_artifact.invoke({"type": "tool_call", "id": "real-tool-call",
        "name": "deliver_research_artifact", "args": {"kind": "bibliography", "references": [REFERENCE], "output_format": "bibtex"}})
    assert result.artifact["_tool_outcome"]["status"] == "complete", result.content
    assert json.loads(json.dumps(result.artifact))["receipts"][0]["file_id"]

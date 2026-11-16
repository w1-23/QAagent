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


@pytest.mark.parametrize('prefix_length', [8190, 8191, 8192])
def test_text_format_probe_accepts_multibyte_sample_boundaries(tmp_path, prefix_length):
    path = tmp_path / 'synthetic.json'
    path.write_bytes(b' ' * prefix_length + '核验'.encode('utf-8'))
    assert tools._artifact_format_matches(path, path.name, 'application/json')


@pytest.mark.parametrize('data', [b'valid prefix\xff', b'valid prefix\xe6\xa0'])
def test_text_format_probe_rejects_malformed_or_incomplete_utf8_at_eof(tmp_path, data):
    path = tmp_path / 'synthetic.json'
    path.write_bytes(data)
    assert not tools._artifact_format_matches(path, path.name, 'application/json')


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
    {"kind": "bibliography", "source": "last_verification"},
    {"kind": "bibliography", "audit_id": "latest"},
    {"kind": "bibliography", "audit_id": "a" * 32, "references": [REFERENCE]},
    {"kind": "bibliography", "audit_id": "a" * 32, "append": True},
    {"kind": "bibliography", "output_format": "json", "references": [REFERENCE]},
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
def test_every_advertised_format_uses_real_renderer_and_library(isolated, audit_store, kind):
    tracing.set_current_tool_name("deliver_research_artifact")
    if kind == "research_notes":
        tools.save_research_note.func(project_name="fixture", note=BODY)
    if kind == 'data_audit':
        from src.research.context import set_request_tables, reset_request_tables
        data = b'value,label\n1,Synthetic A\n1,Synthetic A\n3,Synthetic C\n'
        source = files.register_file(session_id=SID, owner_user_id=OWNER,
                                     filename='Synthetic-audit.csv', content=data)
        token = set_request_tables([{'name': 'Synthetic-audit.csv', 'data': data, 'file_id': source['file_id']}])
        try:
            _, scan = tools.audit_uploaded_data.func()
        finally:
            reset_request_tables(token)
        review_id = scan['integrity_review']['review_id']
    for fmt in delivery.supported_delivery_formats(kind):
        request = {"kind": kind, "output_format": fmt, "title": "synthetic-" + fmt}
        if kind == "report":
            request.update(content=BODY, svg_code=SVG if fmt == "svg" else "")
        elif kind == "table":
            request.update(columns=["Sample", "Value"], rows=[["Alpha", 1], ["Beta", 2]])
        elif kind == "bibliography":
            if fmt == "json":
                request["audit_id"] = audit_store.create_audit(OWNER, SID, references=[REFERENCE])["audit_id"]
            else:
                request["references"] = [REFERENCE]
        elif kind == "research_notes":
            request.pop("title")
            request.update(project_name="fixture", svg_code=SVG if fmt == "svg" else "")
        elif kind == 'data_audit':
            request['review_id'] = review_id
        else:
            request["gaps"] = [GAP]
        text, artifact = run(**request)
        assert artifact["_tool_outcome"]["status"] == "complete", text
        # Exercise the same normalizer/receipt boundary used by the durable
        # runner, not only the producer's own success flag.
        from src.core.op_receipt import make_receipt
        from src.tools.outcome import normalize_tool_outcome
        outcome = normalize_tool_outcome("deliver_research_artifact", text, artifact)
        assert outcome.status == "complete"
        assert outcome.artifacts == tuple(r["url"] for r in artifact["receipts"])
        assert make_receipt("deliver_research_artifact", "delivery-call", text, artifact)["status"] == "complete"
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
        elif fmt == 'json' and kind == 'data_audit':
            saved = json.loads(payload)
            assert saved['review_id'] == review_id
            assert saved['table_results'][0]['findings'] == scan['results'][0]['findings']
            assert any(item['category'] == 'duplicate_row' and item['cells'] for item in saved['issues'])
        elif fmt == "json":
            saved = json.loads(payload)
            assert saved["audit_id"] == request["audit_id"]
            assert saved["rows"][0]["original"] == REFERENCE
            assert saved["coverage"]["pending"] == 1
        else:
            assert b"@article{" in payload and b"Alpha and Beta" in payload and b"10.1234/synthetic" in payload


@pytest.mark.parametrize('fmt', ['csv', 'tsv', 'xlsx'])
def test_signed_numeric_tokens_survive_delivery_without_disabling_formula_guard(isolated, fmt):
    import csv
    import io
    from openpyxl import load_workbook
    values = ['-3.125', '-0.000000000000000123', '+1.2500', '-1.23e-14',
              '-.5', '-1E+4', -2.75, '=1+1', '+SUM(1,2)', '-2+3', '@SUM(A1)', '\t=1']
    text, artifact = run(kind='table', title='synthetic-numeric-tokens', output_format=fmt,
                         columns=['Value'], rows=[[value] for value in values])
    assert artifact['_tool_outcome']['status'] == 'complete', text
    payload = stored_payload(isolated, artifact['receipts'][0])
    if fmt == 'xlsx':
        book = load_workbook(io.BytesIO(payload), data_only=False)
        cells = list(book.active.iter_rows(min_row=2))
        actual = [row[0].value for row in cells]
        assert all(row[0].data_type != 'f' for row in cells)
        book.close()
    else:
        actual = [row[0] for row in list(csv.reader(io.StringIO(payload.decode('utf-8-sig')),
                  delimiter='\t' if fmt == 'tsv' else ','))[1:]]
    assert actual[:6] == values[:6]
    assert float(actual[6]) == -2.75
    assert actual[7:] == ["'" + value for value in values[7:]]


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


@pytest.fixture
def audit_store(isolated, monkeypatch):
    files.init_store()
    from src import config
    from src.research import reference_audit
    from src.research.reference_tools_test import fake_verified
    monkeypatch.setattr(config, "PROJECT_ROOT", isolated)
    monkeypatch.setattr(reference_audit, "verify_record", fake_verified)
    previous = tracing.web_search_enabled(), tracing.get_job_id()
    tracing.set_web_search_enabled(True)
    tracing.set_job_id("")
    try:
        yield reference_audit
    finally:
        tracing.set_web_search_enabled(previous[0])
        tracing.set_job_id(previous[1])


@pytest.mark.parametrize("source", ["verify", "verification", "last_verification"])
def test_legacy_latest_mapping_is_explicitly_rejected_without_mutation(isolated, source):
    files.init_store()
    args = {"title": "verified", "source": source}
    before = copy.deepcopy(args)
    with pytest.raises(ValidationError, match="audit_id"):
        delivery.legacy_delivery_call("export_literature_evidence", args)
    assert args == before
    with pytest.raises(ValidationError, match="audit_id"):
        run(kind="bibliography", source="last_verification")
    assert files.list_files(SID, OWNER) == []


@pytest.mark.parametrize("fmt", ["json", "xlsx", "ris", "bibtex"])
def test_full_audit_export_retains_every_original_error_and_unchecked_entry(isolated, audit_store, monkeypatch, fmt):
    from src.research.reference_tools_test import fake_verified
    references = [dict(REFERENCE) for _ in range(104)] + [
        {"citation": "Original citation without DOI", "title": "No DOI original"}]
    calls = []

    def verify(claims, **kwargs):
        result = fake_verified(claims, **kwargs)
        calls.append(dict(claims))
        if len(calls) == 2:
            result.update(status="network_error", retryable=True, existence="not_checked", identity="not_checked",
                          fields=audit_store.compare_fields(claims, None),
                          candidates=[], evidence=[], errors=["synthetic registry outage"])
        return result

    monkeypatch.setattr(audit_store, "verify_record", verify)
    result = json.loads(tools.verify_uploaded_references.func(references_json=references, max_rows=2))
    aid = result["audit_id"]
    assert len(calls) == 2
    saved = audit_store.get_audit(OWNER, SID, aid, limit=5000)
    assert saved["counts"] == {"metadata_matched": 1, "network_error": 1, "not_checked": 103}
    assert saved["_tool_outcome"]["status"] == "partial"
    assert files.list_files(SID, OWNER) == []
    # A newer inventory must not replace the explicitly selected audit.
    other = audit_store.create_audit(OWNER, SID, references=[{"title": "Newer unrelated inventory"}])
    assert other["audit_id"] != aid
    monkeypatch.setattr(audit_store, "verify_record", lambda *a, **kw: pytest.fail("export replayed providers"))
    text, artifact = run(kind="bibliography", audit_id=aid, output_format=fmt)
    assert artifact["_tool_outcome"]["status"] == "complete", text
    assert len(artifact["receipts"]) == 1
    payload = stored_payload(isolated, artifact["receipts"][0])
    if fmt == "json":
        exported = json.loads(payload)
        assert exported["rows"] == saved["rows"]
        assert exported["coverage"] == saved["coverage"]
        assert exported["budget"] == saved["budget"]
        assert exported["_tool_outcome"]["status"] == "partial"
    elif fmt == "xlsx":
        import io
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(payload))
        rows = list(book["References"].values)[1:]
        assert len(rows) == len(references)
        assert [row[1] for row in rows] == [row["record_id"] for row in saved["rows"]]
        assert [row[3] for row in rows] == [row["status"] for row in saved["rows"]]
        parts = {}
        for record_id, kind, sequence, part, value in list(book["Evidence"].values)[1:]:
            parts.setdefault((record_id or "", kind, sequence), {})[part] = value
        evidence = {key: json.loads("".join(value[index] for index in sorted(value)))
                    for key, value in parts.items()}
        for row in saved["rows"]:
            assert evidence[(row["record_id"], "original_entry", 0)] == {k: v for k, v in row.items() if k != "result"}
            assert evidence[(row["record_id"], "full_result", 0)] == (row["result"] or {})
        assert evidence[("", "audit_metadata", 0)]["coverage"] == saved["coverage"]
        book.close()
    else:
        marker = b"TY  - GEN" if fmt == "ris" else b"@misc{"
        assert payload.count(marker) == len(references)
        assert b"status=network_error" in payload
        assert payload.count(b"status=not_checked") == 103
        assert b"full evidence in JSON/XLSX" in payload
        assert b"Original citation without DOI" not in payload  # RIS/BibTeX project submitted bibliographic fields.
        assert b"No DOI original" in payload
        for row in saved["rows"]:
            assert ("record_id=" + row["record_id"]).encode() in payload
    assert audit_store.get_audit(OWNER, SID, aid, limit=5000) == saved
    assert len(files.list_files(SID, OWNER)) == 1


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


@pytest.fixture
def prepared_dataset(isolated, monkeypatch):
    import pandas as pd
    from src import config
    from src.research.analysis_results import persist_prepared_dataset
    monkeypatch.setattr(config, "PROJECT_ROOT", isolated)
    frame = pd.DataFrame({
        "sample": ["fixture A", "=SUM(1,2)"],
        "identifier": pd.array([9007199254740993, None], dtype="Int64"),
        "mean": [1 / 7, float("nan")],
        "measured": pd.to_datetime(["2026-11-15", None]),
    })
    return persist_prepared_dataset(OWNER, frame, session_id=SID,
        execution_id="synthetic-delivery", script_sha256="a" * 64)


@pytest.mark.parametrize("fmt", ["csv", "tsv", "xlsx"])
def test_dataset_reference_real_storage_and_export(isolated, prepared_dataset, fmt):
    import csv
    import io
    from src.research.analysis_results import load_dataset_snapshot
    original = prepared_dataset.to_dict()
    text, artifact = run(kind="table", title="dataset", dataset_ref=prepared_dataset.ref,
                         output_format=fmt)
    assert artifact["_tool_outcome"]["status"] == "complete", text
    assert artifact["dataset_ref"] == prepared_dataset.ref
    payload = stored_payload(isolated, artifact["receipts"][0])
    if fmt == "xlsx":
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(payload))
        rows = list(book.active.values)
        assert rows[1][1] == "9007199254740993"
        assert rows[1][2] == pytest.approx(1 / 7)
        assert rows[2][1:] == (None, None, None)
        assert book.active["A3"].data_type == "s"
        book.close()
    else:
        rows = list(csv.reader(io.StringIO(payload.decode("utf-8-sig")),
                               delimiter="\t" if fmt == "tsv" else ","))
        assert rows[1][1] == "9007199254740993"
        assert float(rows[1][2]) == 1 / 7
        assert rows[2][1:] == ["", "", ""]
    assert list(rows[0]) == ["sample", "identifier", "mean", "measured"]
    assert rows[1][3] == "2026-11-15T00:00:00"
    assert rows[2][0] == "'=SUM(1,2)"
    assert load_dataset_snapshot(OWNER, prepared_dataset.ref).to_dict() == original


@pytest.mark.parametrize("extra", [
    {"columns": ["replacement"]}, {"rows": [[1]]}, {"append": True},
    {"analysis_result_ref": REF}, {"result_section": "descriptive"},
])
def test_dataset_reference_rejects_overrides(prepared_dataset, monkeypatch, extra):
    monkeypatch.setattr(delivery, "_dispatch", lambda request: pytest.fail("invalid request dispatched"))
    with pytest.raises(ValidationError):
        run(kind="table", dataset_ref=prepared_dataset.ref, **extra)


@pytest.mark.parametrize("mutation", ["owner", "hash", "version"])
def test_dataset_reference_rejects_wrong_identity(prepared_dataset, monkeypatch, mutation):
    ref = dict(prepared_dataset.ref)
    if mutation == "owner":
        tracing.set_user_id(OWNER + 1)
    elif mutation == "hash":
        ref["sha256"] = "b" * 64
    else:
        ref["version"] += 1
    monkeypatch.setattr(tools.export_table, "func", lambda **kwargs: pytest.fail("invalid snapshot published"))
    with pytest.raises((LookupError, ValueError)):
        run(kind="table", dataset_ref=ref)


@pytest.mark.parametrize("case", ["empty", "rows", "cells", "column_whitespace", "infinity"])
def test_dataset_export_rejects_lossy_or_oversized_projection(isolated, monkeypatch, case):
    import pandas as pd
    from src import config
    from src.research.analysis_results import persist_prepared_dataset
    monkeypatch.setattr(config, "PROJECT_ROOT", isolated)
    frame = {
        "empty": lambda: pd.DataFrame({"A": []}),
        "rows": lambda: pd.DataFrame({"A": range(5001)}),
        "cells": lambda: pd.DataFrame({f"C{i}": range(1000) for i in range(101)}),
        "column_whitespace": lambda: pd.DataFrame({" A ": [1]}),
        "infinity": lambda: pd.DataFrame({"A": [float("inf")]}),
    }[case]()
    snapshot = persist_prepared_dataset(OWNER, frame, session_id=SID,
        execution_id="synthetic-invalid-delivery", script_sha256="a" * 64)
    monkeypatch.setattr(tools.export_table, "func", lambda **kwargs: pytest.fail("invalid dataset published"))
    with pytest.raises(ValueError):
        run(kind="table", dataset_ref=snapshot.ref)


def test_actual_langchain_tool_call_returns_serializable_receipts(isolated):
    result = delivery.deliver_research_artifact.invoke({"type": "tool_call", "id": "real-tool-call",
        "name": "deliver_research_artifact", "args": {"kind": "bibliography", "references": [REFERENCE], "output_format": "bibtex"}})
    assert result.artifact["_tool_outcome"]["status"] == "complete", result.content
    assert json.loads(json.dumps(result.artifact))["receipts"][0]["file_id"]

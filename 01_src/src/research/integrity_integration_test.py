"""Synthetic cross-file audit integration with real temporary artifact stores.

All sources are generated engineering fixtures; no real user files, network,
models, historical outputs or benchmark data are touched.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest
from openpyxl import Workbook, load_workbook


X = [0, 1, 3, 7, 12, 20]
OTHER = [9, 4, 7, 2, 6, 3]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    import src.config as config
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)

    def no_network(*args, **kwargs):
        raise AssertionError("Synthetic integration tests cannot access the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    from src import auth
    from src.core import account_leases, tracing
    from src.research import tools
    from src.research.context import set_request_tables, reset_request_tables
    from src.session import files, manager

    monkeypatch.setattr(auth, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "state/accounts.sqlite3")
    monkeypatch.setattr(account_leases, "_states", {})
    auth.init_store()
    with auth._connect() as conn:
        for owner in (101, 202):
            conn.execute(
                "INSERT OR IGNORE INTO users(user_id,username,salt,password_hash,created_at) VALUES(?,?,?,?,?)",
                (owner, f"SYNTHETIC_integrity_{owner}", b"synthetic-salt", b"synthetic-hash", "2026-01-01"))
    monkeypatch.setattr(manager, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manager, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(manager, "DB_PATH", tmp_path / "state/sessions.db")
    monkeypatch.setattr(manager.session_manager, "db_path", str(tmp_path / "state/sessions.db"))
    manager.session_manager._init_db_sync()
    session_id = "synthetic-integrity-session"
    manager.session_manager._create_session_sync(session_id, 101)
    monkeypatch.setattr(files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "DB_PATH", tmp_path / "state/session_files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", tmp_path / "state/session_files")
    monkeypatch.setattr(tools, "PROJECT_ROOT", tmp_path)
    files.init_store()
    previous = (tracing.get_user_id(), tracing.get_session_id(),
                tracing.get_client_request_id(), tracing.get_current_tool_name())
    tracing.set_user_id(101)
    tracing.set_session_id(session_id)
    tracing.set_client_request_id("synthetic-integrity-turn")
    tracing.set_current_tool_name("audit_uploaded_data")
    tokens = []
    uploads = []

    def upload(payloads):
        uploads.clear()
        for i, (name, data) in enumerate(payloads):
            entry = files.register_file(session_id=session_id, owner_user_id=101,
                                        filename=name, content=data,
                                        client_request_id=f"synthetic-upload-{i}")
            uploads.append({"name": name, "data": data, "file_id": entry["file_id"]})
        tokens.append(set_request_tables(uploads))
        return list(uploads)

    def resolve(url):
        assert url.startswith(f"/api/v2/session/{session_id}/artifacts/")
        artifact = tools.resolve_artifact(session_id, url.rsplit("/", 1)[-1])
        assert artifact is not None
        path = artifact["path"]
        assert path.is_relative_to(tmp_path)
        assert path.is_file()
        return artifact

    def report(result):
        summary = result["cross_file_integrity"]
        artifact = resolve(summary["artifact"])
        assert artifact["mime"] == "application/json"
        data = artifact["path"].read_bytes()
        assert hashlib.sha256(data).hexdigest() == summary["artifact_sha256"]
        assert len(data) == summary["artifact_bytes"]
        return json.loads(data)

    try:
        yield SimpleNamespace(root=tmp_path, tools=tools, files=files, session_id=session_id,
                              upload=upload, resolve=resolve, report=report, uploads=uploads)
    finally:
        for token in reversed(tokens):
            reset_request_tables(token)
        tracing.set_user_id(previous[0])
        tracing.set_session_id(previous[1])
        tracing.set_client_request_id(previous[2])
        tracing.set_current_tool_name(previous[3])


def _csv(values=X, other=OTHER, names=("value", "other")):
    return (",".join(names) + "\n" + "".join(f"{x},{y}\n" for x, y in zip(values, other))).encode("utf-8")


def _pair(workspace, *, values=X, other=OTHER):
    return workspace.upload([
        ("SYNTHETIC_source_a.csv", _csv()),
        ("SYNTHETIC_source_b.csv", _csv(values, other, names=("copy", "second"))),
    ])


def test_two_real_temporary_attachments_publish_parseable_dual_source_report(workspace):
    uploads = _pair(workspace)
    text, result = workspace.tools.audit_uploaded_data.func()
    assert result["_tool_outcome"]["status"] == "complete"
    assert len(result["results"]) == 2
    summary = result["cross_file_integrity"]
    assert summary["status"] == "complete"
    report = workspace.report(result)
    assert {source["source_sha256"] for source in report["sources"]} == {
        hashlib.sha256(item["data"]).hexdigest() for item in uploads}
    assert any(item["method"] == "exact_column" for item in report["findings"])
    assert any(item["method"] == "exact_block" for item in report["findings"])
    evidence = next(item for item in report["findings"] if item["method"] == "exact_column")
    assert evidence["left"]["cells"][0] == ["A2"]
    assert evidence["right"]["cells"][0] == ["A2"]
    assert all(item["review_status"] == "pending" for item in report["findings"])
    assert summary["artifact"] in result["_tool_outcome"]["artifacts"]
    assert "独立跨文件证据报告" in text
    generated = [entry for entry in workspace.files.list_files(workspace.session_id, 101)
                 if entry["origin"] == workspace.files.ORIGIN_GENERATED]
    assert len(generated) == 3
    for entry in generated:
        owned = workspace.files.get_account_file(entry["file_id"], 101)
        assert owned and owned["path"].is_file()
        assert workspace.files.get_account_file(entry["file_id"], 202) is None
    for upload in uploads:
        assert workspace.files.get_account_file(upload["file_id"], 101)["path"].read_bytes() == upload["data"]


def test_nondefault_header_preserves_original_xlsx_row_coordinates(workspace):
    payloads = []
    for label in ("a", "b"):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = f"SYNTHETIC_{label}"
        sheet.append(["SYNTHETIC ENGINEERING FIXTURE"])
        sheet.append(["SYNTHETIC header note"])
        sheet.append(["value", "other"])
        for x, other in zip(X, OTHER):
            sheet.append([x, other])
        buffer = io.BytesIO()
        workbook.save(buffer)
        payloads.append((f"SYNTHETIC_header_{label}.xlsx", buffer.getvalue()))
    workspace.upload(payloads)
    _, result = workspace.tools.audit_uploaded_data.func(header_row=3)
    report = workspace.report(result)
    evidence = next(item for item in report["findings"] if item["method"] == "exact_column")
    assert evidence["left"]["cells"][0] == ["A4"]
    assert evidence["right"]["cells"][0] == ["A4"]
    assert evidence["left"]["sheet"] == "SYNTHETIC_a"
    assert evidence["right"]["sheet"] == "SYNTHETIC_b"


def test_normal_unit_conversion_is_pending_review_never_misconduct(workspace):
    _pair(workspace, values=[1000*x for x in X], other=[1000*x for x in OTHER])
    _, result = workspace.tools.audit_uploaded_data.func()
    report = workspace.report(result)
    ratios = [item for item in report["findings"] if item["method"] == "constant_ratio"]
    assert ratios and ratios[0]["relationship"]["scale"] == "1000"
    assert all(item["review_status"] == "pending" for item in report["findings"])
    assert all(item["conclusion"] == "numeric_relationship_only" for item in report["findings"])
    assert any("单位换算" in item for item in ratios[0]["normal_explanations"])


def test_duplicate_requested_indexes_do_not_duplicate_work_or_evidence(workspace):
    _pair(workspace)
    _, result = workspace.tools.audit_uploaded_data.func(file_indexes_json=[0, 0, 1, 1, 0])
    report = workspace.report(result)
    assert len(result["results"]) == 2
    assert result["cross_file_integrity"]["selected_attachment_count"] == 2
    assert report["input_coverage"]["selected_attachment_indexes"] == [0, 1]
    assert len(report["sources"]) == 2
    assert len({item["evidence_id"] for item in report["findings"]}) == len(report["findings"])
    generated = [entry for entry in workspace.files.list_files(workspace.session_id, 101)
                 if entry["origin"] == workspace.files.ORIGIN_GENERATED]
    assert len(generated) == 3


def test_cross_file_report_never_rewrites_published_single_file_workbooks(workspace, monkeypatch):
    _pair(workspace)
    original_save = workspace.tools._save_artifact
    published = {}

    def observe_save(name, data, mime, **kwargs):
        url = original_save(name, data, mime, **kwargs)
        if name.endswith(".xlsx"):
            published[url] = workspace.resolve(url)["path"].read_bytes()
        return url

    monkeypatch.setattr(workspace.tools, "_save_artifact", observe_save)
    _, result = workspace.tools.audit_uploaded_data.func()
    workspace.report(result)
    assert len(published) == 2
    for url, before in published.items():
        after = workspace.resolve(url)["path"].read_bytes()
        assert after == before
        workbook = load_workbook(io.BytesIO(after))
        assert "审查概览" in workbook.sheetnames


def test_manual_annotations_do_not_run_cross_file_detection(workspace, monkeypatch):
    from src.research import integrity
    _pair(workspace)
    monkeypatch.setattr(integrity, "table_from_frame", lambda *a, **kw: pytest.fail("Manual mode must not snapshot"))
    monkeypatch.setattr(integrity, "audit_numeric_reuse", lambda *a, **kw: pytest.fail("Manual mode must not scan"))
    _, result = workspace.tools.audit_uploaded_data.func(annotations_json=[
        {"row": 2, "verdict": "需核对", "reason": "SYNTHETIC manual annotation"}])
    assert result["cross_file_integrity"]["status"] == "not_run"
    assert result["cross_file_integrity"]["reason_code"] == "manual_annotations"
    assert "artifact" not in result["cross_file_integrity"]
    assert len(result["_tool_outcome"]["artifacts"]) == 2


def test_single_distinct_attachment_does_not_trigger_cross_file_review(workspace, monkeypatch):
    from src.research import integrity
    _pair(workspace)
    monkeypatch.setattr(integrity, "audit_numeric_reuse", lambda *a, **kw: pytest.fail("One source must not scan"))
    _, result = workspace.tools.audit_uploaded_data.func(file_indexes_json=[0, 0])
    assert len(result["results"]) == 1
    assert result["cross_file_integrity"]["reason_code"] == "fewer_than_two_attachments"
    assert len(result["_tool_outcome"]["artifacts"]) == 1


def test_report_publication_failure_is_partial_without_invented_url(workspace, monkeypatch):
    _pair(workspace)
    original_save = workspace.tools._save_artifact

    def fail_json(name, data, mime, **kwargs):
        if mime == "application/json":
            raise OSError("SYNTHETIC cross-file report publication failure")
        return original_save(name, data, mime, **kwargs)

    monkeypatch.setattr(workspace.tools, "_save_artifact", fail_json)
    text, result = workspace.tools.audit_uploaded_data.func()
    assert result["_tool_outcome"]["status"] == "partial"
    assert result["cross_file_integrity"]["reason_code"] == "cross_file_report_publish_failed"
    assert "artifact" not in result["cross_file_integrity"]
    assert "独立跨文件证据报告（" not in text
    assert len(result["_tool_outcome"]["artifacts"]) == 2
    for url in result["_tool_outcome"]["artifacts"]:
        assert workspace.resolve(url)["filename"].endswith(".xlsx")


def test_cross_file_analysis_failure_keeps_real_workbooks_and_partial_status(workspace, monkeypatch):
    from src.research import integrity
    _pair(workspace)

    def fail(*args, **kwargs):
        raise RuntimeError("SYNTHETIC analysis failure")

    monkeypatch.setattr(integrity, "audit_numeric_reuse", fail)
    _, result = workspace.tools.audit_uploaded_data.func()
    assert result["_tool_outcome"]["status"] == "partial"
    assert result["cross_file_integrity"]["reason_code"] == "cross_file_analysis_failed"
    assert "artifact" not in result["cross_file_integrity"]
    for url in result["_tool_outcome"]["artifacts"]:
        assert workspace.resolve(url)["path"].is_file()


def test_snapshot_truncation_and_model_context_samples_are_explicit(workspace):
    values = list(range(501))  # One row over the declared snapshot boundary.
    payload = _csv(values, [x*x+1 for x in values])
    workspace.upload([("SYNTHETIC_large_a.csv", payload), ("SYNTHETIC_large_b.csv", payload)])
    _, result = workspace.tools.audit_uploaded_data.func()
    report = workspace.report(result)
    summary = result["cross_file_integrity"]
    assert summary["status"] == "partial"
    assert result["_tool_outcome"]["status"] == "partial"
    assert all(source["parsed_rows"] == 500 and source["source_rows"] == 501 for source in report["sources"])
    assert all("cross_file_row_or_cell_limit" in source["truncation_reasons"] for source in report["sources"])
    assert summary["snapshot_cells"] == 2000
    assert len(summary["evidence_samples"]) <= 3
    assert "findings" not in summary
    for sample in summary["evidence_samples"]:
        for side in ("left", "right"):
            assert len(sample[side]["rows"]) <= 5
            assert len(sample[side]["columns"]) <= 4
            assert sample[side]["location_sample_only"] is True
    assert len(json.dumps(summary, ensure_ascii=False)) < 15_000


def test_global_table_budget_is_bounded_and_skips_are_reported(workspace):
    workbook = Workbook()
    workbook.remove(workbook.active)
    for i in range(11):
        sheet = workbook.create_sheet(f"SYNTHETIC_{i}")
        sheet.append(["value", "other"])
        for x, other in zip(X, OTHER):
            sheet.append([x, other])
    buffer = io.BytesIO()
    workbook.save(buffer)
    workspace.upload([("SYNTHETIC_many_a.xlsx", buffer.getvalue()),
                      ("SYNTHETIC_many_b.xlsx", buffer.getvalue())])
    _, result = workspace.tools.audit_uploaded_data.func()
    report = workspace.report(result)
    assert report["input_coverage"]["snapshot_tables"] == 20
    assert report["input_coverage"]["snapshot_cells"] <= 100_000
    assert len(report["input_coverage"]["snapshot_skipped"]) == 2
    assert report["coverage_status"] == "partial"
    assert result["_tool_outcome"]["status"] == "partial"


def test_existing_summary_precision_parameters_and_receipts_are_preserved(workspace, monkeypatch):
    _pair(workspace)
    original = workspace.tools._audit_frame
    seen = []

    def inspect(*args, **kwargs):
        seen.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(workspace.tools, "_audit_frame", inspect)
    _, result = workspace.tools.audit_uploaded_data.func(checks_json={"mean_decimals": 2, "sd_decimals": 3})
    assert len(seen) == 2
    assert all(item["mean_decimals"] == 2 and item["sd_decimals"] == 3 for item in seen)
    assert all(isinstance(item["execution_receipt"], dict) for item in seen)
    assert all(item["rule_execution"] for item in result["results"])

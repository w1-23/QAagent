"""Computation is the default; explicit publication uses real temporary stores."""
import json

import pytest
from openpyxl import load_workbook

from src.research.integrity_integration_test import workspace, _pair  # noqa: F401
from src.research import delivery_tools as delivery, reference_audit, tools
from src.research.reference_tools_test import reference_workspace, fake_verified  # noqa: F401
from src.research.statistics_tools import analyze_research_data


def generated(workspace):
    return [f for f in workspace.files.list_files(workspace.session_id, 101) if f["origin"] == "generated"]


@pytest.mark.parametrize("manual", [False, True])
def test_audit_default_computes_without_building_or_publishing_workbooks(workspace, monkeypatch, manual):
    workspace.upload([("synthetic.csv", b"x,y\n1,2\n1,2\n3,\n")])
    from src.core import data_audit_xlsx
    monkeypatch.setattr(data_audit_xlsx, "annotate_workbook_multi", lambda *a, **kw: pytest.fail("workbook was rendered"))
    monkeypatch.setattr(tools, "_save_artifact", lambda *a, **kw: pytest.fail("artifact was published"))
    args = {"annotations_json": [{"row": 2, "verdict": "需核对", "reason": "synthetic check"}]} if manual else {}
    text, result = tools.audit_uploaded_data.func(**args)
    assert result["results"][0]["findings"]
    assert result["results"][0]["source_sha256"]
    assert result["results"][0]["coverage"]["processed_rows"] == 3
    assert result["_tool_outcome"]["artifacts"] == []
    assert result["publish_status"] == "not_requested"
    assert "标注文件：" not in text and "原表发现：" in text
    assert generated(workspace) == []


def test_cross_file_default_keeps_complete_internal_evidence(workspace, monkeypatch):
    _pair(workspace)
    monkeypatch.setattr(tools, "_save_artifact", lambda *a, **kw: pytest.fail("artifact was published"))
    text, result = tools.audit_uploaded_data.func()
    assert result["cross_file_integrity"]["status"] == "complete", text
    assert result["cross_file_report"]["findings"]
    assert result["result_refs"][0]["kind"] == "integrity_review"
    from src.research.integrity_review import get_review
    saved = get_review(101, result["integrity_review"]["review_id"])
    assert saved["report"]["cross_file_report"]["findings"] == result["cross_file_report"]["findings"]
    assert generated(workspace) == []


@pytest.mark.parametrize("manual", [False, True])
def test_explicit_audit_publication_keeps_real_annotated_workbook(workspace, manual):
    workspace.upload([("synthetic.csv", b"x,y\n1,2\n1,2\n3,\n")])
    args = {"annotations_json": [{"row": 2, "verdict": "需核对", "reason": "synthetic check"}]} if manual else {}
    text, result = tools.audit_uploaded_data.func(publish=True, **args)
    receipt = result["results"][0]
    book = load_workbook(workspace.resolve(receipt["artifact"])["path"])
    assert any(cell.comment for sheet in book for row in sheet for cell in row)
    assert len(book.sheetnames) >= 3
    book.close()
    assert receipt["publish_status"] == "complete"
    assert len(generated(workspace)) == 1


def test_explicit_cross_file_publication_keeps_xlsx_and_json(workspace):
    _pair(workspace)
    _, result = tools.audit_uploaded_data.func(publish=True)
    assert len(generated(workspace)) == 3
    assert workspace.report(result)["findings"]


@pytest.mark.parametrize("publish", [False, True])
def test_reference_verification_keeps_rows_sources_and_optional_xlsx(reference_workspace, publish):
    workspace = reference_workspace
    result = json.loads(tools.verify_uploaded_references.func(
        references_json=[{"doi": "10.1234/synthetic", "title": "Synthetic"}], allow_web=False, publish=publish))
    row = result["rows"][0]
    assert row["status"] == "metadata_matched"
    assert row["result"]["evidence"][0]["url"] == "https://example.invalid/source"
    assert result["coverage"]["processed"] == result["coverage"]["total"] == 1
    assert result["result_ref"] == {"kind": "reference_audit", "audit_id": result["audit_id"]}
    saved = reference_audit.get_audit(101, workspace.session_id, result["audit_id"])
    assert saved["rows"] == result["rows"]
    assert result["_tool_outcome"]["status"] == "complete"
    assert len(generated(workspace)) == int(publish)
    if publish:
        book = load_workbook(workspace.resolve(result["download_url"])["path"])
        assert book.sheetnames == ["References", "Fields", "Evidence"]
        assert book["References"].max_row == 2
        assert book["References"]["D2"].value == "metadata_matched"
        assert book["Evidence"].max_row >= 4
        book.close()
        assert result["export_status"] == "complete"
        receipt = result["_tool_outcome"]["artifacts"][0]
        assert workspace.files.get_account_file(receipt["file_id"], 101)
    else:
        assert result["export_status"] == "not_requested"
        assert result["_tool_outcome"]["artifacts"] == []
        assert workspace.files.list_files(workspace.session_id, 101) == []
        assert not (workspace.root / "state/artifacts").exists()


def test_reference_publication_failure_does_not_erase_verified_rows(reference_workspace, monkeypatch):
    workspace = reference_workspace
    original = workspace.files.register_file
    monkeypatch.setattr(workspace.files, "register_file",
                        lambda **kw: (_ for _ in ()).throw(OSError("synthetic failure")))
    result = json.loads(tools.verify_uploaded_references.func(
        references_json=[{"doi": "10.1234/synthetic", "title": "Synthetic"}], allow_web=False, publish=True))
    assert result["rows"][0]["status"] == "metadata_matched"
    assert result["_tool_outcome"]["status"] == "partial"
    assert result["_tool_outcome"]["reason_code"] == "publication_failed"
    assert result["export_status"] == "failed" and "artifact_registration_failed" in result["export_error"]
    assert result["result_ref"] == {"kind": "reference_audit", "audit_id": result["audit_id"]}
    assert reference_audit.get_audit(101, workspace.session_id, result["audit_id"])["rows"] == result["rows"]
    assert generated(workspace) == []
    monkeypatch.setattr(workspace.files, "register_file", original)
    monkeypatch.setattr(reference_audit, "verify_record", lambda *a, **kw: pytest.fail("verification replayed"))
    text, exported = delivery.deliver_research_artifact.func(
        kind="bibliography", audit_id=result["audit_id"], output_format="xlsx")
    assert exported["_tool_outcome"]["status"] == "complete", text
    assert len(generated(workspace)) == 1


@pytest.mark.parametrize("publish,fmt", [(False, "none"), (False, "csv"), (True, "csv"), (True, "xlsx"), (True, "none")])
def test_statistics_preserve_full_precision_snapshots_and_optional_files(workspace, monkeypatch, publish, fmt):
    workspace.upload([("synthetic.csv", b"value\n0.1\n0.2\n0.7\n")])
    if not publish:
        monkeypatch.setattr(tools, "_save_artifact", lambda *a, **kw: pytest.fail("artifact was published"))
    result = json.loads(analyze_research_data.func(metrics_json=["value"], publish=publish, output_format=fmt))
    assert result["snapshot_status"] == "complete", result
    assert result["analysis_result_ref"] and result["dataset_ref"]
    assert result["descriptive"]["value"]["mean"] == 1 / 3
    from src.research.analysis_results import load_analysis_result
    saved = load_analysis_result(101, result["analysis_result_ref"])
    assert saved.to_dict()["outputs"]["descriptive"]["value"]["mean"] == 1 / 3
    assert result["coverage"]["processed_rows"] == 3
    assert len(generated(workspace)) == int(publish)
    assert result["export_status"] == ("complete" if publish else "not_requested")
    if not publish and fmt != "none":
        assert "publish=True" in result["export_note"]


@pytest.mark.parametrize("fmt", ["json", "xlsx", "ris", "bibtex"])
def test_default_verification_audit_can_later_export_without_rechecking(reference_workspace, monkeypatch, fmt):
    workspace = reference_workspace
    result = json.loads(tools.verify_uploaded_references.func(
        references_json=[{"doi": "10.1234/synthetic", "title": "Synthetic"}], allow_web=False))
    assert result["result_ref"] == {"kind": "reference_audit", "audit_id": result["audit_id"]}
    assert workspace.files.list_files(workspace.session_id, 101) == []
    saved = reference_audit.get_audit(101, workspace.session_id, result["audit_id"])
    assert saved["coverage"]["source_sha256"] == result["coverage"]["source_sha256"]
    monkeypatch.setattr(reference_audit, "verify_record", lambda *a, **kw: pytest.fail("verification replayed"))
    text, exported = delivery.deliver_research_artifact.func(
        kind="bibliography", title="deferred", audit_id=result["audit_id"], output_format=fmt)
    assert exported["_tool_outcome"]["status"] == "complete", text
    artifact = workspace.resolve(exported["receipts"][0]["url"])
    if fmt == "xlsx":
        book = load_workbook(artifact["path"])
        assert book["References"].max_row == 2
        assert book["References"]["D2"].value == "metadata_matched"
        assert {"Fields", "Evidence"} <= set(book.sheetnames)
        book.close()
    elif fmt == "json":
        payload = json.loads(artifact["path"].read_bytes())
        assert payload["audit_id"] == result["audit_id"]
        assert payload["rows"] == saved["rows"]
        assert payload["coverage"] == saved["coverage"]
    else:
        payload = artifact["path"].read_text(encoding="utf-8")
        assert "10.1234/synthetic" in payload and "status=metadata_matched" in payload
        assert result["audit_id"] in payload
    assert reference_audit.get_audit(101, workspace.session_id, result["audit_id"])["budget"] == saved["budget"]
    assert len(generated(workspace)) == 1


@pytest.mark.parametrize("fmt", ["json", "xlsx", "ris", "bibtex"])
def test_persistent_audit_handoff_is_owner_and_session_scoped(reference_workspace, fmt):
    from src.core import tracing
    from src.session.manager import session_manager
    workspace = reference_workspace
    result = json.loads(tools.verify_uploaded_references.func(
        references_json=[{"doi": "10.1234/synthetic", "title": "Synthetic"}], allow_web=False))
    session_manager._create_session_sync("other-owner-session", 202)
    session_manager._create_session_sync("other-synthetic-session", 101)
    request = {"kind": "bibliography", "audit_id": result["audit_id"], "output_format": fmt}
    tracing.set_user_id(202)
    with pytest.raises(PermissionError):
        delivery.deliver_research_artifact.func(**request)
    tracing.set_session_id("other-owner-session")
    with pytest.raises(LookupError):
        delivery.deliver_research_artifact.func(**request)
    tracing.set_user_id(101)
    tracing.set_session_id("other-synthetic-session")
    with pytest.raises(LookupError):
        delivery.deliver_research_artifact.func(**request)
    tracing.set_session_id(workspace.session_id)
    assert generated(workspace) == []
    saved = reference_audit.get_audit(101, workspace.session_id, result["audit_id"])
    assert saved["rows"] == result["rows"]
    assert saved["coverage"]["source_sha256"] == result["coverage"]["source_sha256"]


def test_network_errors_remain_in_audit_but_never_become_success(reference_workspace, monkeypatch):
    workspace = reference_workspace

    def network_error(claims, **kwargs):
        result = fake_verified(claims, **kwargs)
        result.update(status="network_error", retryable=True, existence="not_checked", identity="not_checked",
                      fields=reference_audit.compare_fields(claims, None),
                      evidence=[], candidates=[], errors=["synthetic outage"])
        return result

    monkeypatch.setattr(reference_audit, "verify_record", network_error)
    result = json.loads(tools.verify_uploaded_references.func(
        references_json=[{"doi": "10.1234/synthetic"}], allow_web=False))
    assert result["_tool_outcome"]["status"] == "partial"
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["retryable"] == 1
    aid = result["audit_id"]
    assert result["result_ref"] == {"kind": "reference_audit", "audit_id": aid}
    row = reference_audit.get_audit(101, workspace.session_id, aid)["rows"][0]
    assert row["status"] == "network_error"
    assert row["result"]["errors"] == ["synthetic outage"]
    assert row["attempt_history"][0]["result"]["errors"] == ["synthetic outage"]
    assert generated(workspace) == []
    monkeypatch.setattr(reference_audit, "verify_record", lambda *a, **kw: pytest.fail("verification replayed"))
    text, exported = delivery.deliver_research_artifact.func(
        kind="bibliography", audit_id=aid, output_format="json")
    assert exported["_tool_outcome"]["status"] == "complete", text  # Export success is separate from verification.
    payload = json.loads(workspace.resolve(exported["receipts"][0]["url"])["path"].read_bytes())
    assert payload["_tool_outcome"]["status"] == "partial"
    assert payload["rows"][0]["result"]["errors"] == ["synthetic outage"]
    assert payload["coverage"]["complete"] is False


def test_shared_format_validator_accepts_bibliography_mime_parameters(workspace):
    for fmt in ("ris", "bibtex"):
        text = tools.export_literature_evidence.func(title="synthetic", output_format=fmt,
            evidence_rows_json=[{"title": "Synthetic", "doi": "10.1234/synthetic"}])
        import re
        url = re.search(r"\]\(([^)]+)\)", text).group(1)
        checks = tools.validate_artifact_links([url])
        assert checks[0]["valid"] and checks[0]["format_matches"], checks

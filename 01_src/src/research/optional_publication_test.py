"""Computation is the default; explicit publication uses real temporary stores."""
import json

import pytest
from openpyxl import load_workbook

from src.research.integrity_integration_test import workspace, _pair  # noqa: F401
from src.research import tools
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


def fake_verification(raw, allow_web):
    assert allow_web is False
    return {"doi": raw["doi"], "status": "metadata_matched", "provider": "synthetic registry",
            "input_title": raw["title"], "metadata_title": raw["title"],
            "source_url": "https://example.invalid/source", "evidence_url": "https://example.invalid/source",
            "detail": "synthetic offline metadata", "field_conflicts": ""}


@pytest.mark.parametrize("publish", [False, True])
def test_reference_verification_keeps_rows_sources_and_optional_xlsx(workspace, monkeypatch, publish):
    monkeypatch.setattr(tools, "_verify_reference_record", fake_verification)
    if not publish:
        monkeypatch.setattr(tools, "_save_artifact", lambda *a, **kw: pytest.fail("artifact was published"))
    result = json.loads(tools.verify_uploaded_references.func(
        references_json=[{"doi": "10.1234/synthetic", "title": "Synthetic"}], allow_web=False, publish=publish))
    assert result["rows"][0]["status"] == "metadata_matched"
    assert result["result_refs"][0]["url"] == "https://example.invalid/source"
    assert result["coverage"]["selected"] == 1
    assert result["_tool_outcome"]["status"] == "complete"
    assert len(generated(workspace)) == int(publish)
    if publish:
        book = load_workbook(workspace.resolve(result["download_url"])["path"])
        assert book.active.max_column == 18 and book.active.max_row == 2
        book.close()
    else:
        assert result["export_status"] == "not_requested"


def test_reference_publication_failure_does_not_erase_verified_rows(workspace, monkeypatch):
    monkeypatch.setattr(tools, "_verify_reference_record", fake_verification)
    monkeypatch.setattr(tools, "_save_artifact", lambda *a, **kw: (_ for _ in ()).throw(OSError("synthetic failure")))
    result = json.loads(tools.verify_uploaded_references.func(
        references_json=[{"doi": "10.1234/synthetic", "title": "Synthetic"}], allow_web=False, publish=True))
    assert result["rows"][0]["status"] == "metadata_matched"
    assert result["_tool_outcome"]["status"] == "partial"
    assert result["export_status"] == "failed" and "synthetic failure" in result["export_error"]


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


@pytest.mark.parametrize("fmt", ["xlsx", "ris", "bibtex"])
def test_default_verification_internal_ref_can_later_export_without_rechecking(workspace, monkeypatch, fmt):
    monkeypatch.setattr(tools, "_verify_reference_record", fake_verification)
    result = json.loads(tools.verify_uploaded_references.func(
        references_json=[{"doi": "10.1234/synthetic", "title": "Synthetic"}], allow_web=False))
    ref = result["result_ref"]
    assert ref["kind"] == "reference_verification_result"
    assert workspace.files.get_account_file(ref["file_id"], 101) is None
    assert workspace.files.get_account_file(ref["file_id"], 101, allow_internal=True)
    assert generated(workspace) == []
    monkeypatch.setattr(tools, "_verify_reference_record", lambda *a, **kw: pytest.fail("verification replayed"))
    text = tools.export_literature_evidence.func(title="deferred", source="last_verification", output_format=fmt)
    import re
    artifact = workspace.resolve(re.search(r"\]\(([^)]+)\)", text).group(1))
    if fmt == "xlsx":
        book = load_workbook(artifact["path"])
        assert book.active.max_column == 18 and book.active.max_row == 2
        assert book.active["B2"].value == "metadata_matched"
        book.close()
    else:
        assert "10.1234/synthetic" in artifact["path"].read_text(encoding="utf-8")
    assert len(generated(workspace)) == 1


def test_internal_verification_handoff_is_owner_session_and_hash_scoped(workspace, monkeypatch):
    from src.core import tracing
    from src.session.manager import session_manager
    monkeypatch.setattr(tools, "_verify_reference_record", fake_verification)
    result = json.loads(tools.verify_uploaded_references.func(
        references_json=[{"doi": "10.1234/synthetic", "title": "Synthetic"}], allow_web=False))
    tracing.set_user_id(202)
    with pytest.raises(ValueError, match="当前账号"):
        tools.export_literature_evidence.func(title="private", source="last_verification")
    tracing.set_user_id(101)
    session_manager._create_session_sync("other-synthetic-session", 101)
    tracing.set_session_id("other-synthetic-session")
    with pytest.raises(ValueError, match="没有找到"):
        tools.export_literature_evidence.func(title="private", source="last_verification")
    tracing.set_session_id(workspace.session_id)
    entry = workspace.files.get_account_file(result["result_ref"]["file_id"], 101, allow_internal=True)
    entry["path"].write_bytes(b"{}")
    with pytest.raises(ValueError, match="校验失败"):
        tools.export_literature_evidence.func(title="tampered", source="last_verification")


def test_failed_verification_does_not_create_reusable_success_reference(workspace, monkeypatch):
    monkeypatch.setattr(tools, "_verify_reference_record", lambda raw, allow_web: {"doi": raw["doi"], "status": "network_error", "detail": "synthetic outage"})
    result = json.loads(tools.verify_uploaded_references.func(
        references_json=[{"doi": "10.1234/synthetic"}], allow_web=False))
    assert result["_tool_outcome"]["status"] == "partial"
    assert "result_ref" not in result
    with pytest.raises(ValueError, match="没有找到"):
        tools.export_literature_evidence.func(title="failed", source="last_verification")


def test_shared_format_validator_accepts_bibliography_mime_parameters(workspace):
    for fmt in ("ris", "bibtex"):
        text = tools.export_literature_evidence.func(title="synthetic", output_format=fmt,
            evidence_rows_json=[{"title": "Synthetic", "doi": "10.1234/synthetic"}])
        import re
        url = re.search(r"\]\(([^)]+)\)", text).group(1)
        checks = tools.validate_artifact_links([url])
        assert checks[0]["valid"] and checks[0]["format_matches"], checks

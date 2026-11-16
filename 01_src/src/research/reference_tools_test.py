"""Chat ReferenceAudit integration with real temporary stores and no providers."""
import json
import sqlite3
import time

import pytest

from src.core import tracing
from src.research import reference_audit as audit, reference_tools, tools
from src.research.integrity_integration_test import workspace  # noqa: F401
from src.research.reference_extraction import digest

REFERENCE = {"doi": "10.1234/synthetic", "title": "Synthetic title"}


def fake_verified(claims, *, check, debit, **kwargs):
    """Replace only provider verification; exercise the real checks and debit."""
    check()
    debit()
    candidate = dict(claims, source="synthetic registry", url="https://example.invalid/source")
    return {"status": "metadata_matched", "existence": "record_found", "identity": "identifier_match",
            "fields": audit.compare_fields(claims, candidate), "rule_version": audit.RULE_VERSION,
            "claims_sha256": digest(claims), "retryable": False, "candidates": [candidate],
            "evidence": [{"provider": "synthetic registry", "url": candidate["url"], "status": "returned"}],
            "errors": [], "doi": claims.get("doi", ""), "source_url": candidate["url"],
            "evidence_url": candidate["url"]}


@pytest.fixture
def reference_workspace(workspace, monkeypatch):
    previous = tracing.web_search_enabled(), tracing.get_job_id()
    tracing.set_web_search_enabled(True)
    tracing.set_job_id("")
    monkeypatch.setattr(audit, "verify_record", fake_verified)
    try:
        yield workspace
    finally:
        tracing.set_web_search_enabled(previous[0])
        tracing.set_job_id(previous[1])


def chat(**kwargs):
    return json.loads(tools.verify_uploaded_references.func(**kwargs))


def test_chat_alias_creates_once_then_runs_reads_cancels_and_resumes(reference_workspace):
    assert tools.verify_uploaded_references is reference_tools.verify_uploaded_references
    references = [REFERENCE, dict(REFERENCE), {"citation": "No DOI original", "title": "No DOI"}]
    created = chat(operation="create", references_json=references)
    aid = created["audit_id"]
    ids = [row["record_id"] for row in created["rows"]]
    assert len(set(ids)) == 3  # Duplicate citations are separate inventory entries.
    assert created["coverage"]["total"] == 3
    assert created["budget"]["requests_used"] == 0
    assert all(row["status"] == "not_checked" for row in created["rows"])
    assert all(row["positions"] for row in created["rows"])
    first = chat(audit_id=aid, max_rows=1, allow_web=False)
    assert first["processed_indices"] == [0]
    assert first["coverage"]["pending"] == 2
    assert first["budget"]["requests_used"] == 1
    assert first["result_ref"] == {"kind": "reference_audit", "audit_id": aid}
    cancelled = chat(operation="cancel", audit_id=aid)
    assert cancelled["status"] == "cancelled"
    assert chat(audit_id=aid)["run_reason"] == "cancelled_use_resume"
    assert chat(operation="read", audit_id=aid)["rows"] == cancelled["rows"]
    resumed = chat(audit_id=aid, resume=True)
    assert resumed["coverage"]["complete"] is True
    assert resumed["_tool_outcome"]["status"] == "complete"
    assert resumed["budget"]["requests_used"] == 3
    assert [row["record_id"] for row in resumed["rows"]] == ids
    assert [row["original"] for row in resumed["rows"]] == references
    assert all(row["attempts"] == 1 for row in resumed["rows"])
    assert [item["audit_id"] for item in chat(operation="list")["audits"]] == [aid]
    with sqlite3.connect(reference_workspace.root / "state/reference_audits.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM reference_audits").fetchone()[0] == 1
    assert reference_workspace.files.list_files(reference_workspace.session_id, 101) == []


def test_chat_completed_with_review_gives_same_audit_export_actions(reference_workspace):
    result = chat(text='[1] Smith A. (2020). A clear title. Synthetic Journal. DOI: 10.1234/synthetic\n')
    aid = result['audit_id']
    assert result['status'] == 'completed_with_review'
    assert result['terminal'] is True and result['can_resume'] is False
    assert result['coverage']['complete'] is False
    assert result['coverage']['processing_complete'] is True
    assert result['next_action'] == 'export_and_review'
    assert result['read_more'] is None
    assert result['export_requests'] == [
        {'kind': 'bibliography', 'audit_id': aid, 'output_format': 'json'},
        {'kind': 'bibliography', 'audit_id': aid, 'output_format': 'xlsx'}]
    again = chat(audit_id=aid, resume=True)
    assert again['run_reason'] == 'already_completed'
    assert again['budget'] == result['budget']
    assert again['rows'][0]['attempts'] == 1


def test_compact_rows_keep_results_and_source_while_full_evidence_stays_readable(reference_workspace, monkeypatch):
    def verbose_verified(claims, **kwargs):
        result = fake_verified(claims, **kwargs)
        result['candidates'][0]['provider_payload'] = 'durable candidate evidence ' * 2000
        result['evidence'][0]['response'] = 'durable HTTP evidence ' * 2000
        return result

    monkeypatch.setattr(audit, 'verify_record', verbose_verified)
    references = [REFERENCE, dict(REFERENCE), {'citation': 'No DOI original', 'title': 'No DOI'}]
    result = chat(references_json=references)
    aid = result['audit_id']
    full = audit.get_audit(101, reference_workspace.session_id, aid)
    assert result['result_view'] == 'compact'
    assert len(json.dumps(result)) < len(json.dumps(full)) / 10
    for compact, stored in zip(result['rows'], full['rows']):
        assert not {'result', 'candidates', 'evidence', 'attempt_history'} & compact.keys()
        for key in ('status', 'existence', 'identity', 'fields', 'errors', 'attempts',
                    'original', 'citation', 'record_id', 'record_index', 'positions'):
            assert compact[key] == stored[key]
        read = compact['evidence_read']
        assert read['tool'] == 'verify_uploaded_references'
        evidence = chat(**read['args'])
        assert evidence['result_view'] == 'full_evidence'
        assert evidence['rows'] == [stored]
    exported = json.loads(audit.export_audit(101, reference_workspace.session_id, aid)[0])
    assert exported['rows'] == full['rows']
    assert all(row['attempt_history'] for row in exported['rows'])
    assert result['export_requests'][0] == {
        'kind': 'bibliography', 'audit_id': aid, 'output_format': 'json'}


def test_compact_rows_do_not_hide_retryable_failure_or_require_more_default_runs(reference_workspace, monkeypatch):
    def failed_source(claims, **kwargs):
        return dict(fake_verified(claims, **kwargs), retryable=True, errors={'registry:doi': 'http_429'})

    monkeypatch.setattr(audit, 'verify_record', failed_source)
    first = chat(references_json=[REFERENCE])
    assert first['rows'][0]['errors'] == {'registry:doi': 'http_429'}
    assert first['rows'][0]['retryable'] is True
    assert first['next_action'] == 'export_and_review'
    assert first['_tool_outcome']['status'] == 'partial'
    assert first['can_resume'] is True and first['terminal'] is False
    again = chat(audit_id=first['audit_id'])
    assert again['processed_indices'] == []
    assert again['budget'] == first['budget']
    assert again['rows'] == first['rows']
    assert chat(audit_id=first['audit_id'], resume=True)['rows'][0]['attempts'] == 2


def test_registered_chat_tool_accepts_durable_audit_operations(reference_workspace):
    schema = tools.verify_uploaded_references.args_schema.model_json_schema()
    assert {"audit_id", "operation", "resume", "offset", "max_rows"} <= set(schema["properties"])
    created = json.loads(tools.verify_uploaded_references.invoke(
        {"operation": "create", "references_json": [REFERENCE]}))
    result = json.loads(tools.verify_uploaded_references.invoke(
        {"operation": "run", "audit_id": created["audit_id"], "max_rows": 1}))
    assert result["audit_id"] == created["audit_id"]
    assert result["rows"][0]["status"] == "metadata_matched"
    assert result["_tool_outcome"]["status"] == "complete"


def test_published_audit_keeps_normalized_delivery_receipt(reference_workspace):
    from src.tools.outcome import normalize_tool_outcome
    from src.core.op_receipt import make_receipt
    result = chat(references_json=[REFERENCE], publish=True)
    assert result['export_status'] == 'complete'
    assert result['_tool_outcome']['artifacts'] == [result['download_url']]
    text = json.dumps(result, ensure_ascii=False)
    assert normalize_tool_outcome('verify_uploaded_references', text, None).status == 'complete'
    receipt = make_receipt('verify_uploaded_references', 'published-audit', text, None)
    assert receipt['status'] == 'complete'
    assert receipt['coverage']['_tool_outcome']['artifacts'] == (result['download_url'],)


def test_chat_reads_all_pages_without_choosing_latest(reference_workspace):
    first = chat(operation="create", references_json=[dict(REFERENCE, citation=f"Entry {i}") for i in range(103)])
    newer = chat(operation="create", references_json=[{"title": "A different audit"}])
    assert newer["audit_id"] != first["audit_id"]
    page = chat(operation="read", audit_id=first["audit_id"], max_rows=100)
    assert len(page["rows"]) == 100 and page["read_more"]["offset"] == 100
    tail = chat(**page["read_more"])
    assert [row["record_index"] for row in tail["rows"]] == [100, 101, 102]
    assert tail["next_offset"] is None
    assert tail["coverage"]["total"] == 103
    for operation in ("read", "cancel"):
        result = chat(operation=operation)
        assert result["_tool_outcome"]["status"] == "not_run"
        assert result["_tool_outcome"]["reason_code"] == "reference_audit_rejected"
    assert chat(operation="read", audit_id=newer["audit_id"])["status"] == "pending"


def test_chat_inherits_real_job_deadline(reference_workspace, monkeypatch):
    from src.core import jobs, task_events
    monkeypatch.setattr(jobs, "_DB_PATH", reference_workspace.root / "state/sessions.db")
    job = jobs.claim_job(reference_workspace.session_id, "synthetic-reference-job", owner_user_id=101)
    task_events.init_budget(job.job_id, time.time() + 10)
    tracing.set_job_id(job.job_id)
    observed = []

    def verify(claims, **kwargs):
        remaining = kwargs["deadline"] - time.monotonic()
        assert 0 < remaining <= task_events.remaining_seconds(job.job_id) + 1
        observed.append(remaining)
        return fake_verified(claims, **kwargs)

    monkeypatch.setattr(audit, "verify_record", verify)
    first = chat(references_json=[REFERENCE, REFERENCE], max_rows=1)
    assert len(observed) == 1
    with task_events.transaction() as conn:
        conn.execute("UPDATE task_budgets SET deadline=? WHERE job_id=?", (time.time() - 1, job.job_id))
    stopped = chat(audit_id=first["audit_id"], resume=True)
    assert stopped["run_reason"] == "deadline"
    assert stopped["coverage"]["pending"] == 1
    assert stopped["budget"]["requests_used"] == 1
    assert len(observed) == 1


def test_chat_resume_never_refills_durable_request_budget(reference_workspace):
    aid = audit.create_audit(101, reference_workspace.session_id,
                             references=[REFERENCE, REFERENCE], request_budget=1)["audit_id"]
    for resume in (False, True):
        result = chat(audit_id=aid, resume=resume)
        assert result["run_reason"] == "budget_exhausted"
        assert result["budget"] == {"request_limit": 1, "requests_used": 1, "requests_remaining": 0}
        assert result["coverage"]["processed"] == 1
        assert result["coverage"]["pending"] == 1
        assert result["rows"][0]["attempts"] == 1
        assert result["_tool_outcome"]["status"] == "partial"


def test_network_authorization_blocks_verification_even_with_legacy_allow_web(reference_workspace, monkeypatch):
    monkeypatch.setattr(audit, "verify_record", lambda *a, **kw: pytest.fail("network-disabled verification ran"))
    tracing.set_web_search_enabled(False)
    result = chat(references_json=[REFERENCE], allow_web=True)
    assert result["run_reason"] == "network_disabled"
    assert result["rows"][0]["status"] == "not_checked"
    assert result["budget"]["requests_used"] == 0
    assert result["_tool_outcome"]["status"] == "partial"


@pytest.mark.parametrize("kwargs", [
    {"start_index": 1}, {"max_rows": 101}, {"operation": "latest"},
    {"references_json": {"title": "not an array"}}, {"include_evidence": True},
])
def test_invalid_chat_requests_never_verify(reference_workspace, monkeypatch, kwargs):
    monkeypatch.setattr(audit, "verify_record", lambda *a, **kw: pytest.fail("invalid request verified"))
    result = chat(**kwargs)
    assert result["_tool_outcome"]["status"] == "not_run"
    assert result["rows"] == []
    assert chat(operation="list")["audits"] == []


def test_file_source_is_hash_checked_before_creation_and_snapshot_stays_durable(reference_workspace):
    workspace = reference_workspace
    upload = workspace.upload([("references.txt", b"References\n[1] Synthetic original. 10.1234/synthetic\n")])[0]
    result = chat(operation="create", file_id=upload["file_id"])
    aid = result["audit_id"]
    source_hash = digest(upload["data"])
    assert result["source"]["ref"] == {"file_id": upload["file_id"], "sha256": source_hash}
    assert result["coverage"]["source_sha256"] == source_hash
    assert result["rows"][0]["positions"][0]["line"] == 2
    entry = workspace.files.get_account_file(upload["file_id"], 101)
    entry["path"].write_bytes(b"tampered source")
    rejected = chat(operation="create", file_id=upload["file_id"])
    assert rejected["_tool_outcome"]["status"] == "not_run"
    assert rejected["_tool_outcome"]["reason_code"] == "reference_audit_rejected"
    assert len(chat(operation="list")["audits"]) == 1
    saved = chat(operation="read", audit_id=aid)
    assert saved["rows"] == result["rows"]
    assert saved["coverage"]["source_sha256"] == source_hash


@pytest.mark.parametrize("operation", ["read", "run", "cancel"])
def test_chat_audit_access_is_owner_and_session_bound(reference_workspace, operation):
    from src.session.manager import session_manager
    workspace = reference_workspace
    created = chat(operation="create", references_json=[REFERENCE])
    aid = created["audit_id"]
    session_manager._create_session_sync("other-reference-session", 101)
    session_manager._create_session_sync("other-owner-reference-session", 202)
    for owner, session in ((202, workspace.session_id), (202, "other-owner-reference-session"),
                           (101, "other-reference-session")):
        tracing.set_user_id(owner)
        tracing.set_session_id(session)
        result = chat(operation=operation, audit_id=aid)
        assert result["_tool_outcome"]["status"] == "not_run"
        assert result["_tool_outcome"]["reason_code"] == "reference_audit_rejected"
        assert result["rows"] == []
    tracing.set_user_id(101)
    tracing.set_session_id(workspace.session_id)
    saved = chat(operation="read", audit_id=aid)
    assert saved["status"] == "pending"
    assert saved["rows"] == created["rows"]
    assert saved["budget"]["requests_used"] == 0


def test_existing_audit_cannot_be_replaced_by_new_input(reference_workspace):
    aid = chat(operation="create", references_json=[REFERENCE])["audit_id"]
    result = chat(audit_id=aid, references_json=[{"title": "Replacement"}])
    assert result["_tool_outcome"]["status"] == "not_run"
    saved = chat(operation="read", audit_id=aid)
    assert saved["rows"][0]["original"] == REFERENCE
    assert saved["budget"]["requests_used"] == 0
    assert len(chat(operation="list")["audits"]) == 1

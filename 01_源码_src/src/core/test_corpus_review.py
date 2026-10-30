# -*- coding: utf-8 -*-
"""文献入库审核候选池的单元测试（不调模型、不跑真实嵌入）。"""

import json
import pathlib

import pytest

from src.core import corpus_review as cr


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """把候选库、队列目录、语料目录都指到临时路径。"""
    state = tmp_path / "state"
    state.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(cr, "_state_dir", lambda: state)
    monkeypatch.setattr(cr, "_data_dir", lambda: data)
    monkeypatch.setattr(cr, "_schema_ready", False)
    yield {"state": state, "data": data}


def _artifact(doi: str, title: str = "柑橘黄龙病防控研究", chars: int = 4000) -> dict:
    body = "摘要：" + ("黄龙病防控与媒介昆虫防治。" * (chars // 12))[:chars]
    return {"doi": doi, "title": title, "url": f"https://example.org/{doi}",
            "content": body, "provider": "crossref"}


def test_capture_fetch_creates_pending_candidate(store):
    result = cr.capture_fetch(_artifact("10.1000/abc123"), session_id="s1", request_id="r1")
    assert result and result["status"] == "pending"
    listing = cr.list_candidates(status="pending")
    assert len(listing["candidates"]) == 1
    item = listing["candidates"][0]
    assert item["doi"] == "10.1000/abc123"
    assert item["source_kind"] == "fetch_paper"
    path = pathlib.Path(store["data"] / cr.QUEUE_DIRNAME)
    fulltext = list(path.rglob("fulltext.md"))
    assert len(fulltext) == 1 and fulltext[0].stat().st_size > 100


def test_same_doi_accumulates_frequency_and_providers(store):
    cr.capture_fetch(_artifact("10.1000/dup"), session_id="s1")
    cr.capture_fetch(_artifact("10.1000/dup"), session_id="s2")
    listing = cr.list_candidates(status="all")
    assert len(listing["candidates"]) == 1
    assert listing["candidates"][0]["frequency"] == 2


def test_short_content_is_ignored(store):
    assert cr.capture_fetch({"doi": "10.1000/short", "content": "太短"}) is None
    assert cr.capture_upload("x.pdf", "短") is None


def test_capture_upload_records_source_kind(store):
    text = "引言\n" + ("上传论文正文内容。" * 400)
    result = cr.capture_upload("my_paper.pdf", text)
    assert result and result["status"] == "pending"
    item = cr.list_candidates(status="pending")["candidates"][0]
    assert item["source_kind"] == "upload"
    assert item["title"] == "my_paper"


def test_approve_is_idempotent(store):
    cid = cr.capture_fetch(_artifact("10.1000/idem"))["id"]
    first = cr.decide(cid, "approve", admin_user_id=1)
    second = cr.decide(cid, "approve", admin_user_id=1)
    assert first["changed"] is True and first["candidate"]["status"] == "approved"
    assert second["changed"] is False and second["idempotent"] is True
    assert second["candidate"]["status"] == "approved"


def test_reject_then_reopen(store):
    cid = cr.capture_fetch(_artifact("10.1000/rj"))["id"]
    cr.decide(cid, "reject", admin_user_id=1, note="不是柑橘文献")
    assert cr.get_candidate(cid)["status"] == "rejected"
    cr.decide(cid, "reopen", admin_user_id=1)
    assert cr.get_candidate(cid)["status"] == "pending"


def test_apply_batch_requires_approved(store):
    cr.capture_fetch(_artifact("10.1000/pending-only"))
    result = cr.apply_batch(admin_user_id=1)
    assert result["ok"] is False and result["error"] == "no_approved_candidates"


def test_apply_batch_success_marks_ingested(store, monkeypatch):
    cid = cr.capture_fetch(_artifact("10.1000/ok"))["id"]
    cr.decide(cid, "approve", admin_user_id=1)

    def fake_ingest(batch_dir, corpus_id, display_name, *, activate, chunk_size):
        names = sorted(p.name for p in pathlib.Path(batch_dir).glob("*.md"))
        assert names, "批目录里应有 markdown 全文"
        return True, f"VERIFIED corpus {corpus_id}; not activated, generation=gTEST0001", ""

    monkeypatch.setattr(cr, "_run_ingest", fake_ingest)
    monkeypatch.setattr(cr, "_active_generation", lambda: "gPREV0001")
    result = cr.apply_batch(admin_user_id=1)
    assert result["ok"] is True
    assert result["generation"] == "gTEST0001"
    assert result["prev_generation"] == "gPREV0001"
    assert cr.get_candidate(cid)["status"] == "ingested"
    batches = cr.list_batches()
    assert batches[0]["status"] == "applied"
    assert result["candidate_ids"] == [cid]


def test_apply_batch_failure_keeps_candidate_approved(store, monkeypatch):
    cid = cr.capture_fetch(_artifact("10.1000/fail"))["id"]
    cr.decide(cid, "approve", admin_user_id=1)
    monkeypatch.setattr(cr, "_run_ingest",
                        lambda *a, **k: (False, "", "IMPORT FAILED; active index was not changed"))
    monkeypatch.setattr(cr, "_active_generation", lambda: "gPREV0002")
    result = cr.apply_batch(admin_user_id=1)
    assert result["ok"] is False and result["status"] == "failed"
    assert cr.get_candidate(cid)["status"] == "approved"
    assert cr.list_batches()[0]["status"] == "failed"


def test_rollback_batch_restores_pointer_and_candidates(store, monkeypatch):
    cid = cr.capture_fetch(_artifact("10.1000/rb"))["id"]
    cr.decide(cid, "approve", admin_user_id=1)
    monkeypatch.setattr(cr, "_run_ingest",
                        lambda *a, **k: (True, "generation=gNEW0001", ""))
    active = {"gen": "gPREV0003"}
    monkeypatch.setattr(cr, "_active_generation", lambda: active["gen"])
    applied = cr.apply_batch(admin_user_id=1)
    batch_id = applied["batch_id"]
    assert cr.get_candidate(cid)["status"] == "ingested"

    class _Proc:
        returncode = 0
        stdout = "ACTIVATED gPREV0003"
        stderr = ""

    monkeypatch.setattr(cr.subprocess, "run", lambda *a, **k: _Proc())
    active["gen"] = "gNEW0001"
    result = cr.rollback_batch(batch_id, admin_user_id=1)
    assert result["ok"] is True and result["generation"] == "gPREV0003"
    assert cr.get_candidate(cid)["status"] == "approved"
    assert cr.list_batches()[0]["status"] == "rolled_back"


def test_frequency_ordering_favours_hot_papers(store):
    cold = cr.capture_fetch(_artifact("10.1000/cold"))["id"]
    hot = cr.capture_fetch(_artifact("10.1000/hot"))["id"]
    cr.bump_frequency(hot, 4)
    ordered = [c["id"] for c in cr.list_candidates(status="pending")["candidates"]]
    assert ordered[0] == hot and cold in ordered


def test_batch_manifest_lists_copied_files(store, monkeypatch):
    seen = {}

    def fake_ingest(batch_dir, corpus_id, display_name, *, activate, chunk_size):
        seen["files"] = sorted(p.name for p in pathlib.Path(batch_dir).glob("*.md"))
        seen["activate"] = activate
        seen["chunk_size"] = chunk_size
        return True, "generation=gX", ""

    cid = cr.capture_fetch(_artifact("10.1000/manifest", title="黄龙病/防控 研究"))["id"]
    cr.decide(cid, "approve", admin_user_id=1)
    monkeypatch.setattr(cr, "_run_ingest", fake_ingest)
    monkeypatch.setattr(cr, "_active_generation", lambda: "")
    cr.apply_batch(admin_user_id=1, activate=True, chunk_size=700)
    assert seen["activate"] is True and seen["chunk_size"] == 700
    assert seen["files"] and seen["files"][0].endswith(".md")
    assert "/" not in seen["files"][0]

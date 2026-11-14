"""Real isolated file pins for Word drafts; no scientific case/benchmark work."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
import sqlite3
import threading

import pandas as pd
from PIL import Image
import pytest

from src.core import word_documents as documents
from src.core.tracing import set_session_id, set_user_id
from src.research import word_tools, analysis_results
from src.session import files

OWNER = 813151
SESSION = "word-pins-session-001"


@pytest.fixture
def env(tmp_path, monkeypatch):
    from src import config, auth
    from src.session.manager import session_manager
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(word_tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "DB_PATH", tmp_path / "state" / "session_files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", tmp_path / "state" / "session_files")
    monkeypatch.setattr(auth, "user_by_id", lambda owner: {"id": owner, "active": True})
    monkeypatch.setattr(session_manager, "get_session_owner", lambda sid: OWNER)
    set_session_id(SESSION)
    set_user_id(OWNER)
    files.init_store()
    yield tmp_path
    set_session_id("")
    set_user_id(None)


def upload_image():
    payload = io.BytesIO()
    Image.new("RGB", (12, 6), "white").save(payload, format="PNG")
    return files.register_file(session_id=SESSION, owner_user_id=OWNER,
        filename="synthetic.png", content=payload.getvalue(), mime="image/png")


def create(store=None):
    store = store or word_tools._store()
    result = store.create(title="工程源文件引用测试", profile="general", batch_id="create",
        outline=[{"section_id": "s", "title": "来源"}])
    return store, result["document_id"]


def image_block(file):
    return {"type": "image", "block_id": "image", "section_id": "s",
            "file_id": file["file_id"], "caption": "图1 合成图片"}


def append(store, did, blocks, **kwargs):
    return store.mutate(did, operation="append_blocks", batch_id=kwargs.pop("batch_id", "append"),
                        expected_version=kwargs.pop("expected_version", 1), blocks=blocks, **kwargs)


def test_image_is_pinned_before_draft_commit_and_history_survives_replacement(env):
    file = upload_image()
    store, did = create()
    receipt = append(store, did, [image_block(file)])
    deps = files.file_dependencies(file["file_id"], OWNER)
    assert len(deps) == 1 and deps[0]["resource_kind"] == "word_draft"
    assert deps[0]["sha256"] == file["sha256"]
    assert not files.delete_account_file(file["file_id"], OWNER)
    assert append(store, did, [image_block(file)]) == receipt
    assert files.file_dependencies(file["file_id"], OWNER) == deps
    store.mutate(did, operation="replace_blocks", batch_id="replace", expected_version=2,
        block_ids=["image"], blocks=[{"type": "paragraph", "block_id": "p", "section_id": "s", "text": "当前版本移除图片。"}])
    assert store.inspect(did)["total_images"] == 0
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM word_versions WHERE id=?", (did,)).fetchone()[0] == 3
        assert conn.execute("SELECT sources FROM word_version_sources WHERE id=? AND version=2", (did,)).fetchone()
    assert not files.delete_account_file(file["file_id"], OWNER)
    documents.purge_account_drafts(store.db_path, OWNER)
    assert not files.file_dependencies(file["file_id"], OWNER)
    assert files.delete_account_file(file["file_id"], OWNER)


def test_analysis_table_sources_have_independent_word_pins(env):
    file = files.register_file(session_id=SESSION, owner_user_id=OWNER,
        filename="synthetic.csv", content=b"value\n1\n2\n", mime="text/csv")
    dataset = analysis_results.persist_dataset_snapshot(OWNER, pd.DataFrame({"value": [1, 2]}),
        source={"file_id": file["file_id"], "sha256": file["sha256"]})
    analysis = analysis_results.persist_analysis_result(OWNER, dataset.ref,
        outputs={"value": {"n": 2}}, parameters={}, design={}, exclusions=[], coverage={})
    store, did = create()
    text, result = word_tools.word_document.func(operation="append_blocks", document_id=did,
        batch_id="analysis", expected_version=1, blocks=[{"type": "analysis_table", "block_id": "table",
        "section_id": "s", "analysis_result_ref": analysis.ref}])
    assert result["_tool_outcome"]["status"] == "complete", text
    assert result["analysis_result_refs"] == [analysis.ref]
    assert {d["resource_kind"] for d in files.file_dependencies(file["file_id"], OWNER)} == {"dataset_snapshot", "word_draft"}
    analysis_results.purge_owner(OWNER)
    assert [d["resource_kind"] for d in files.file_dependencies(file["file_id"], OWNER)] == ["word_draft"]
    assert not files.delete_account_file(file["file_id"], OWNER)
    documents.purge_account_drafts(store.db_path, OWNER)
    assert files.delete_account_file(file["file_id"], OWNER)


@pytest.mark.parametrize("fault", ["owner", "hash", "bytes"])
def test_wrong_owner_and_changed_source_hash_never_commit_or_leak_pins(env, fault):
    file = upload_image()
    owner = OWNER + 1 if fault == "owner" else OWNER
    store, did = create(documents.WordDocuments(env / "state" / "word_drafts.sqlite3", owner=owner, session=SESSION))
    digest = "0" * 64 if fault == "hash" else file["sha256"]
    if fault == "bytes":
        files.get_account_file(file["file_id"], OWNER)["path"].write_bytes(b"changed bytes")
    with pytest.raises(ValueError):
        append(store, did, [{"type": "paragraph", "block_id": "p", "section_id": "s", "text": "合成文本。"}],
            source_files=[{"file_id": file["file_id"], "sha256": digest}])
    assert store.inspect(did)["version"] == 1
    assert files.file_dependencies(file["file_id"], OWNER) == []
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM word_version_sources").fetchone()[0] == 0


def test_failed_sql_after_pin_releases_only_failed_attempt_and_keeps_previous_history(env):
    file = upload_image()
    store, did = create()
    append(store, did, [image_block(file)])
    previous = files.file_dependencies(file["file_id"], OWNER)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("CREATE TRIGGER fail_version BEFORE INSERT ON word_versions WHEN NEW.version=3 "
                     "BEGIN SELECT RAISE(ABORT, 'engineering SQL failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="engineering SQL failure"):
        append(store, did, [{"type": "paragraph", "block_id": "p", "section_id": "s", "text": "合成补充。"}],
            expected_version=2, batch_id="failed")
    assert store.inspect(did)["version"] == 2
    assert files.file_dependencies(file["file_id"], OWNER) == previous
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM word_batches WHERE batch='failed'").fetchone()[0] == 0


def test_failed_commit_compensates_pins(env, monkeypatch):
    file = upload_image()
    store, did = create()
    with sqlite3.connect(store.db_path) as conn:
        conn.executescript("CREATE TABLE pin_parent (id INTEGER PRIMARY KEY); "
            "CREATE TABLE pin_child (id INTEGER REFERENCES pin_parent(id) DEFERRABLE INITIALLY DEFERRED); "
            "CREATE TRIGGER fail_commit AFTER INSERT ON word_versions WHEN NEW.version=2 "
            "BEGIN INSERT INTO pin_child VALUES(99); END;")
    original = documents._DraftConnection

    class ForeignKeysConnection(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.execute("PRAGMA foreign_keys=ON")

    monkeypatch.setattr(documents, "_DraftConnection", ForeignKeysConnection)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        append(store, did, [image_block(file)])
    assert store.inspect(did)["version"] == 1
    assert not files.file_dependencies(file["file_id"], OWNER)
    assert files.delete_account_file(file["file_id"], OWNER)


def test_failed_render_never_creates_pins(env, monkeypatch):
    file = upload_image()
    store, did = create()
    monkeypatch.setattr(documents, "render_document", lambda state: (_ for _ in ()).throw(OSError("render failed")))
    with pytest.raises(OSError):
        append(store, did, [image_block(file)])
    assert store.inspect(did)["version"] == 1
    assert not files.file_dependencies(file["file_id"], OWNER)


def test_concurrent_cas_pins_only_committed_winner(env):
    file = upload_image()
    store, did = create()
    def writer(index):
        try:
            return append(store, did, [image_block(file)], batch_id=f"write-{index}")["version"]
        except documents.DocumentError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(writer, range(2)))
    assert sorted(map(str, outcomes)) == ["2", "version_conflict"]
    assert len(files.file_dependencies(file["file_id"], OWNER)) == 1


def test_failed_attempt_cleanup_cannot_unpin_concurrent_success_at_same_version(env, monkeypatch):
    file = upload_image()
    store, did = create()
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("CREATE TRIGGER reject_loser BEFORE UPDATE ON word_drafts "
            "WHEN instr(NEW.state, '\"block_id\":\"loser\"') > 0 "
            "BEGIN SELECT RAISE(ABORT, 'reject loser'); END")
    cleanup_started, winner_committed = threading.Event(), threading.Event()
    released = []
    original = files.release_resource_references
    def delayed_release(owner, kind, resource_id):
        cleanup_started.set()
        assert winner_committed.wait(10), "winner did not commit while failed writer compensated"
        released.append(resource_id)
        return original(owner, kind, resource_id)
    monkeypatch.setattr(files, "release_resource_references", delayed_release)
    def loser():
        with pytest.raises(sqlite3.IntegrityError, match="reject loser"):
            append(store, did, [{**image_block(file), "block_id": "loser"}], batch_id="loser")
    with ThreadPoolExecutor(max_workers=2) as pool:
        failed = pool.submit(loser)
        try:
            assert cleanup_started.wait(10)
            assert append(store, did, [image_block(file)], batch_id="winner")["version"] == 2
        finally:
            winner_committed.set()
        failed.result(timeout=10)
    deps = files.file_dependencies(file["file_id"], OWNER)
    assert len(deps) == 1 and deps[0]["resource_id"] not in released
    assert not files.delete_account_file(file["file_id"], OWNER)


def test_purge_release_failure_retains_inventory_for_retry_and_other_owner(env, monkeypatch):
    file = upload_image()
    store, did = create()
    append(store, did, [image_block(file)])
    other, other_id = create(documents.WordDocuments(store.db_path, owner=OWNER + 1, session=SESSION))
    original = files.release_resource_references
    monkeypatch.setattr(files, "release_resource_references", lambda *args: (_ for _ in ()).throw(OSError("release unavailable")))
    with pytest.raises(OSError, match="release unavailable"):
        documents.purge_account_drafts(store.db_path, OWNER)
    assert store.inspect(did)["version"] == 2
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM word_version_sources WHERE id=?", (did,)).fetchone()[0] == 1
    monkeypatch.setattr(files, "release_resource_references", original)
    documents.purge_account_drafts(store.db_path, OWNER)
    assert other.inspect(other_id)["version"] == 1
    assert files.delete_account_file(file["file_id"], OWNER)


def test_version_cap_preserves_history_reads_retries_publication_and_source_pins(env, monkeypatch):
    monkeypatch.setenv("CITRUS_WORD_MAX_VERSIONS", "2")
    file = upload_image()
    store, did = create()
    blocks = [{"type": "heading", "block_id": "h", "section_id": "s", "text": "来源"},
              image_block(file), {"type": "section_end", "block_id": "end", "section_id": "s"},
              {"type": "document_end", "block_id": "done"}]
    receipt = append(store, did, blocks)
    prior_pins = files.file_dependencies(file["file_id"], OWNER)
    with sqlite3.connect(store.db_path) as conn:
        versions = conn.execute("SELECT version,payload FROM word_versions WHERE id=? ORDER BY version", (did,)).fetchall()
    second = files.register_file(session_id=SESSION, owner_user_id=OWNER,
        filename="second.csv", content=b"synthetic\n2\n", mime="text/csv")
    with pytest.raises(documents.DocumentError) as error:
        append(store, did, [{"type": "paragraph", "block_id": "extra", "section_id": "s", "text": "合成补充。"}],
            expected_version=2, batch_id="over-cap", source_files=[{"file_id": second["file_id"], "sha256": second["sha256"]}])
    assert error.value.code == "version_limit"
    assert "CITRUS_WORD_MAX_VERSIONS=2" in str(error.value)
    assert store.inspect(did)["version"] == 2
    assert files.file_dependencies(file["file_id"], OWNER) == prior_pins
    assert files.file_dependencies(second["file_id"], OWNER) == []
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT version,payload FROM word_versions WHERE id=? ORDER BY version", (did,)).fetchall() == versions
        assert conn.execute("SELECT count(*) FROM word_batches WHERE batch='over-cap'").fetchone()[0] == 0
    # A lower new cap must not block legacy reads, successful batch retry or publication.
    monkeypatch.setenv("CITRUS_WORD_MAX_VERSIONS", "1")
    assert append(store, did, blocks) == receipt
    assert create(store)[1] == did
    assert store.inspect(did)["total_images"] == 1
    published = store.finalize(did, expected_version=2, expected_last_block_id="done", filename="cap.docx",
        publisher=lambda filename, payload: {"file_id": "synthetic-publication", "filename": filename}, verifier=lambda result: None)
    assert published["version"] == 2
    assert not files.delete_account_file(file["file_id"], OWNER)
    documents.purge_account_drafts(store.db_path, OWNER)
    assert files.delete_account_file(file["file_id"], OWNER)


def test_version_cap_can_increase_without_lost_retry_or_pruned_versions(env, monkeypatch):
    monkeypatch.setenv("CITRUS_WORD_MAX_VERSIONS", "1")
    store, did = create()
    blocks = [{"type": "paragraph", "block_id": "p", "section_id": "s", "text": "合成正文。"}]
    with pytest.raises(documents.DocumentError) as error:
        append(store, did, blocks)
    assert error.value.code == "version_limit"  # creation counts toward the cap
    monkeypatch.setenv("CITRUS_WORD_MAX_VERSIONS", "2")
    assert append(store, did, blocks)["version"] == 2
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM word_versions WHERE id=?", (did,)).fetchone()[0] == 2


@pytest.mark.parametrize("value", ["0", "-1", "invalid", "1.5", ""])
def test_version_cap_requires_positive_configuration_only_for_new_mutations(env, monkeypatch, value):
    store, did = create()
    monkeypatch.setenv("CITRUS_WORD_MAX_VERSIONS", value)
    with pytest.raises(documents.DocumentError) as error:
        append(store, did, [{"type": "paragraph", "block_id": "p", "section_id": "s", "text": "合成正文。"}])
    assert error.value.code == "invalid_configuration"
    assert store.inspect(did)["version"] == 1
    assert create(store)[1] == did


@pytest.mark.parametrize("fault", ["owner", "hash"])
def test_analysis_ref_authorization_failure_is_structured_and_does_not_save_draft(env, fault):
    dataset = analysis_results.persist_dataset_snapshot(OWNER, pd.DataFrame({"value": [1]}),
        source={"file_id": "upload:synthetic", "sha256": "a" * 64})
    analysis = analysis_results.persist_analysis_result(OWNER, dataset.ref,
        outputs={"n": 1}, parameters={}, design={}, exclusions=[], coverage={})
    if fault == "owner":
        set_user_id(OWNER + 1)
    store, did = create()
    reference = {**analysis.ref, "sha256": "0" * 64} if fault == "hash" else analysis.ref
    text, result = word_tools.word_document.func(operation="append_blocks", document_id=did,
        batch_id="bad-ref", expected_version=1, blocks=[{"type": "analysis_table", "block_id": "t",
        "section_id": "s", "analysis_result_ref": reference}])
    assert result["_tool_outcome"]["status"] == "failed", text
    assert store.inspect(did)["version"] == 1

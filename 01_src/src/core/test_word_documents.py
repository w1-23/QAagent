"""Offline engineering tests; synthetic text only, no scientific case runs."""
import io
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from docx import Document

from src.core.word_documents import WordDocuments, DocumentError


@pytest.fixture
def store(tmp_path):
    return WordDocuments(tmp_path / "word.sqlite3", owner=101, session="word-session-001")


def create(store, sections=1):
    return store.create(title="工程验证文档", profile="research_report", batch_id="create-1",
        outline=[{"section_id": f"s{i}", "title": f"第{i}节"} for i in range(sections)])


def section(i, text="这是用于验证增量保存和重新打开的合成测试文本。"):
    return [{"block_id": f"h{i}", "type": "heading", "section_id": f"s{i}", "text": f"第{i}节"},
            {"block_id": f"p{i}", "type": "paragraph", "section_id": f"s{i}", "text": text},
            {"block_id": f"e{i}", "type": "section_end", "section_id": f"s{i}"}]


def test_long_document_checkpoint_recovery_and_batch_idempotency(store):
    initial = create(store, 10)
    did, version = initial["document_id"], 1
    final_text = "最后一段完整保留。"
    for i in range(10):
        blocks = section(i, "合成测试文本用于确认正文逐批完整保存。" * 90 + (final_text if i == 9 else ""))
        if i < 3:
            blocks.insert(2, {"block_id": f"t{i}", "type": "table", "section_id": f"s{i}",
                              "columns": ["标识", "数值"], "rows": [[f"test-{i}", i]], "caption": f"表{i+1} 工程表"})
        receipt = store.mutate(did, operation="append_blocks", batch_id=f"batch-{i}", expected_version=version, blocks=blocks)
        assert store.mutate(did, operation="append_blocks", batch_id=f"batch-{i}", expected_version=version, blocks=blocks) == receipt
        version = receipt["version"]
        # Recreate the service object after every commit: no process-memory dependency.
        store = WordDocuments(store.db_path, owner=101, session="word-session-001")
        assert store.inspect(did)["version"] == version
    receipt = store.mutate(did, operation="append_blocks", batch_id="end", expected_version=version,
                           blocks=[{"block_id": "done", "type": "document_end"}])
    captured = {}
    def publish(filename, payload):
        captured["payload"] = payload
        return {"file_id": "test", "filename": filename}
    result = store.finalize(did, expected_version=receipt["version"], expected_last_block_id="done",
                            filename="工程测试.docx", publisher=publish, verifier=lambda r: None)
    assert result["validation"]["total_chars"] >= 12000
    assert result["validation"]["tables"] == 3
    doc = Document(io.BytesIO(captured["payload"]))
    assert final_text in "\n".join(p.text for p in doc.paragraphs)
    assert doc.paragraphs[0].style.name == "Title"
    assert doc.paragraphs[1].style.name == "Heading 1"
    assert len(doc.tables) == 3
    assert round(doc.sections[0].left_margin.cm, 1) == 2.2
    again = store.finalize(did, expected_version=receipt["version"], expected_last_block_id="done", filename="工程测试.docx",
                          publisher=lambda *a: pytest.fail("must not publish twice"), verifier=lambda r: None)
    assert again == result


def test_batch_payload_conflict_version_conflict_and_cross_account(store):
    did = create(store)["document_id"]
    store.mutate(did, operation="append_blocks", batch_id="first", expected_version=1, blocks=section(0))
    for blocks, batch in [(section(0, "不同内容"), "first"), (section(0), "new")]:
        with pytest.raises(DocumentError):
            store.mutate(did, operation="append_blocks", batch_id=batch, expected_version=1, blocks=blocks)
    with pytest.raises(DocumentError, match="没有该文档"):
        WordDocuments(store.db_path, owner=202, session=store.session).inspect(did)
    assert store.inspect(did)["block_count"] == 3


def test_concurrent_cas_accepts_one_writer(store):
    did = create(store)["document_id"]
    def writer(i):
        try:
            return store.mutate(did, operation="append_blocks", batch_id=f"b{i}", expected_version=1, blocks=section(0))["version"]
        except DocumentError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(writer, range(2)))
    assert sorted(str(r) for r in results) == ["2", "version_conflict"]


def test_failed_checkpoint_render_rolls_back_batch(store, monkeypatch):
    from src.core import word_documents as module
    did = create(store)["document_id"]
    original = module.render_document
    monkeypatch.setattr(module, "render_document", lambda s: (_ for _ in ()).throw(OSError("disk failed")))
    with pytest.raises(OSError):
        store.mutate(did, operation="append_blocks", batch_id="first", expected_version=1, blocks=section(0))
    assert store.inspect(did)["block_count"] == 0
    monkeypatch.setattr(module, "render_document", original)
    assert store.mutate(did, operation="append_blocks", batch_id="first", expected_version=1, blocks=section(0))["block_count"] == 3


def test_heading_only_cannot_be_finalized(store):
    did = create(store)["document_id"]
    blocks = [b for b in section(0) if b["type"] != "paragraph"] + [{"block_id": "done", "type": "document_end"}]
    store.mutate(did, operation="append_blocks", batch_id="first", expected_version=1, blocks=blocks)
    with pytest.raises(DocumentError, match="没有实际正文"):
        store.finalize(did, expected_version=2, expected_last_block_id="done", filename="test.docx",
                       publisher=lambda *a: pytest.fail("incomplete must not publish"), verifier=lambda r: None)


def test_replacement_preserves_unselected_blocks(store):
    did = create(store)["document_id"]
    store.mutate(did, operation="append_blocks", batch_id="first", expected_version=1, blocks=section(0))
    replacement = [{"block_id": "p0", "type": "paragraph", "section_id": "s0", "text": "替换后的完整段落。"}]
    store.mutate(did, operation="replace_blocks", batch_id="replace", expected_version=2, blocks=replacement, block_ids=["p0"])
    assert [b["block_id"] for b in store.inspect(did)["blocks"]] == ["h0", "p0", "e0"]
    with sqlite3.connect(store.db_path) as conn:
        old, new = [r[0] for r in conn.execute("SELECT payload FROM word_versions WHERE id=? AND version IN (2,3) ORDER BY version", (did,))]
    assert "替换后" not in "".join(p.text for p in Document(io.BytesIO(old)).paragraphs)
    assert "替换后" in "".join(p.text for p in Document(io.BytesIO(new)).paragraphs)


def test_account_purge_removes_all_private_versions_only_for_target(store):
    from src.core.word_documents import purge_account_drafts
    did = create(store)["document_id"]
    store.mutate(did, operation="append_blocks", batch_id="first", expected_version=1, blocks=section(0))
    other = WordDocuments(store.db_path, owner=202, session=store.session)
    other_id = create(other)["document_id"]
    purge_account_drafts(store.db_path, 101)
    with sqlite3.connect(store.db_path) as conn:
        for table in ("word_drafts", "word_batches", "word_versions"):
            assert conn.execute(f"SELECT count(*) FROM {table} WHERE id=?", (did,)).fetchone()[0] == 0
            assert conn.execute(f"SELECT count(*) FROM {table} WHERE id=?", (other_id,)).fetchone()[0] == 1
    assert other.inspect(other_id)["version"] == 1


def test_inspect_does_not_call_a_reopened_section_complete(store):
    did = create(store)["document_id"]
    receipt = store.mutate(did, operation="append_blocks", batch_id="first", expected_version=1, blocks=section(0))
    assert receipt["completed_sections"] == ["s0"]
    receipt = store.mutate(did, operation="append_blocks", batch_id="reopen", expected_version=2,
        blocks=[{"block_id": "extra", "type": "paragraph", "section_id": "s0", "text": "补写段落，还需要新的章节结束标记。"}])
    assert receipt["completed_sections"] == []

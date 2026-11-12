"""The real Word tool publishes through the existing account file library."""
import io
import re

import pytest
from docx import Document

from src.core.tracing import set_current_tool_name, set_user_id, set_client_request_id
from src.research import word_tools
from src.research.test_single_report import isolated
from src.session import files


@pytest.fixture
def word_env(isolated, monkeypatch):
    monkeypatch.setattr(word_tools, "PROJECT_ROOT", isolated)
    set_current_tool_name("word_document")
    files.init_store()
    return isolated


def prepared():
    _, state = word_tools.word_document.func(operation="create", title="工程测试报告", profile="data_audit",
        outline=[{"section_id": "s1", "title": "核查范围"}], batch_id="create")
    did = state["document_id"]
    text, saved = word_tools.word_document.func(operation="append_blocks", document_id=did,
        expected_version=1, batch_id="batch-1", blocks=[
            {"block_id": "h1", "type": "heading", "section_id": "s1", "text": "核查范围"},
            {"block_id": "p1", "type": "paragraph", "section_id": "s1", "text": "仅使用无科学结论的合成工程材料进行验证。"},
            {"block_id": "t1", "type": "table", "section_id": "s1", "columns": ["项目", "状态"], "rows": [["合成项", "已核对"]]},
            {"block_id": "e1", "type": "section_end", "section_id": "s1"},
            {"block_id": "done", "type": "document_end"},
        ])
    assert saved["_tool_outcome"]["artifacts"] == []
    return did


def finalize(did):
    return word_tools.word_document.func(operation="finalize", document_id=did, expected_version=2,
                                          expected_last_block_id="done", filename="工程测试报告.docx")


def test_registered_docx_is_real_editable_and_idempotent(word_env):
    did = prepared()
    assert files.list_files("session-publish-001", 101) == []
    text, result = finalize(did)
    assert result["_tool_outcome"]["status"] == "complete", text
    assert result["file_id"] and result["sha256"] and result["size"] > 0
    assert re.search(r"\[下载.*\]\(/api/v2/session/", text)
    assert len(text) < 200
    entry = files.get_account_file(result["file_id"], 101)
    doc = Document(entry["path"])
    assert len(doc.tables) == 1
    doc.paragraphs[1].text = "可编辑的新标题"
    out = io.BytesIO()
    doc.save(out)
    assert Document(io.BytesIO(out.getvalue())).paragraphs[1].text == "可编辑的新标题"
    assert finalize(did)[1]["file_id"] == result["file_id"]
    assert len(files.list_files("session-publish-001", 101)) == 1
    set_user_id(202)
    assert files.get_account_file(result["file_id"], 202) is None
    rejected, details = finalize(did)
    assert details["_tool_outcome"]["status"] == "failed"
    assert "下载" not in rejected


def test_registration_failure_can_resume_finalize_without_losing_the_draft(word_env, monkeypatch):
    did = prepared()
    original = files.register_file
    monkeypatch.setattr(files, "register_file", lambda **kwargs: (_ for _ in ()).throw(OSError("registry unavailable")))
    text, result = finalize(did)
    assert "已生成" not in text
    assert result["_tool_outcome"]["status"] == "failed"
    assert word_tools.word_document.func(operation="inspect", document_id=did)[1]["block_count"] == 5
    monkeypatch.setattr(files, "register_file", original)
    assert finalize(did)[1]["_tool_outcome"]["status"] == "complete"


def test_same_document_requested_in_a_new_turn_gets_current_attribution(word_env):
    did = prepared()
    first = finalize(did)[1]
    set_client_request_id("request-002")
    second = finalize(did)[1]
    assert first["artifact_id"] != second["artifact_id"]
    assert first["sha256"] == second["sha256"]
    assert files.get_account_file(second["file_id"], 101)["turn_request_id"] == "request-002"


def test_image_file_id_is_account_scoped(word_env):
    from PIL import Image
    image = io.BytesIO()
    Image.new("RGB", (300, 150), "white").save(image, format="PNG")
    files.init_store()
    file = files.register_file(session_id="session-publish-001", owner_user_id=101,
                                filename="example.png", content=image.getvalue(), mime="image/png")
    _, state = word_tools.word_document.func(operation="create", title="图片工程文档", profile="general",
        outline=[{"section_id": "s1", "title": "示意"}], batch_id="image-create")
    _, receipt = word_tools.word_document.func(operation="append_blocks", document_id=state["document_id"],
        expected_version=1, batch_id="image-batch", blocks=[
            {"block_id": "h1", "type": "heading", "section_id": "s1", "text": "示意"},
            {"block_id": "img1", "type": "image", "section_id": "s1", "file_id": file["file_id"], "caption": "图1 合成示意", "width_cm": 10},
            {"block_id": "e1", "type": "section_end", "section_id": "s1"}, {"block_id": "done", "type": "document_end"}])
    assert receipt["total_images"] == 1
    _, published = finalize(state["document_id"])
    doc = Document(files.get_account_file(published["file_id"], 101)["path"])
    assert round(doc.inline_shapes[0].width / doc.inline_shapes[0].height, 2) == 2
    set_user_id(202)
    _, other = word_tools.word_document.func(operation="create", title="其他账号", profile="general",
        outline=[{"section_id": "s1", "title": "示意"}], batch_id="image-create")
    text, failure = word_tools.word_document.func(operation="append_blocks", document_id=other["document_id"],
        expected_version=1, batch_id="foreign", blocks=[{"block_id": "img", "type": "image", "section_id": "s1", "file_id": file["file_id"], "caption": "图1"}])
    assert failure["_tool_outcome"]["status"] == "failed"


@pytest.mark.parametrize("filename", ["report draft.docx", "report(1).docx", "x" * 80 + ".docx"])
def test_publisher_normalized_filename_is_verified_and_reusable(word_env, filename):
    did = prepared()
    def publish():
        return word_tools.word_document.func(operation="finalize", document_id=did, expected_version=2,
            expected_last_block_id="done", filename=filename)
    text, result = publish()
    assert result["_tool_outcome"]["status"] == "complete", text
    entry = files.get_account_file(result["file_id"], 101)
    assert result["filename"] == entry["filename"]
    assert result["requested_filename"] == filename
    assert publish()[1]["file_id"] == result["file_id"]


def test_batch_size_fault_is_correctable_but_conflicts_are_not(word_env):
    from src.tools.outcome import safe_tool_step
    did = prepared()
    text, failure = word_tools.word_document.func(operation="append_blocks", document_id=did,
        batch_id="too-big", expected_version=2, blocks=[
            {"block_id": "big", "type": "paragraph", "section_id": "s1", "text": "合成文本" * 20000}])
    assert safe_tool_step("word_document", text, failure)["reason_code"] == "invalid_arguments"
    assert safe_tool_step("word_document", text, failure)["retryable"] is True
    text, conflict = word_tools.word_document.func(operation="append_blocks", document_id=did,
        batch_id="batch-1", expected_version=2, blocks=[
            {"block_id": "h1", "type": "heading", "section_id": "s1", "text": "不同内容"}])
    step = safe_tool_step("word_document", text, conflict)
    assert step["reason_code"] == "batch_conflict" and step["retryable"] is False
    assert word_tools.word_document.func(operation="inspect", document_id=did)[1]["block_count"] == 5

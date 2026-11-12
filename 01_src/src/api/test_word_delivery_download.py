"""Run in a separate pytest process: API imports bind runtime stores.

Uses the actual account-download handler in an isolated ASGI app, with trusted
identity supplied by the test. No production server, login or model is called.
"""
import hashlib
import io


def test_real_word_download_bytes_and_cross_account_denial(tmp_path, monkeypatch):
    import src.config as config
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    from src.api import main
    from src.session import files
    from src.research import tools, word_tools
    from src.core import tracing
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from docx import Document

    monkeypatch.setattr(files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "DB_PATH", tmp_path / "state/session_files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", tmp_path / "state/session_files")
    monkeypatch.setattr(tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(word_tools, "PROJECT_ROOT", tmp_path)
    tracing.set_user_id(101)
    tracing.set_session_id("session-download-001")
    tracing.set_client_request_id("request-download")
    tracing.set_current_tool_name("word_document")
    try:
        _, created = word_tools.word_document.func(operation="create", title="下载工程测试", profile="general",
            outline=[{"section_id": "s1", "title": "范围"}], batch_id="create")
        did = created["document_id"]
        _, saved = word_tools.word_document.func(operation="append_blocks", document_id=did,
            batch_id="body", expected_version=1, blocks=[
                {"block_id": "h", "type": "heading", "section_id": "s1", "text": "范围"},
                {"block_id": "p", "type": "paragraph", "section_id": "s1", "text": "只检查文件下载字节与账号边界。"},
                {"block_id": "e", "type": "section_end", "section_id": "s1"},
                {"block_id": "done", "type": "document_end"}])
        text, final = word_tools.word_document.func(operation="finalize", document_id=did,
            expected_version=2, expected_last_block_id="done", filename="下载测试.docx")
        assert final["_tool_outcome"]["status"] == "complete", text
        identity = {"user_id": 101}
        monkeypatch.setattr(main, "current_user", lambda request: identity)
        app = FastAPI()
        app.add_api_route("/api/v2/files/{file_id}/download", main.download_account_file, methods=["GET"])
        with TestClient(app) as client:
            response = client.get(final["download_url"])
            assert response.status_code == 200
            assert hashlib.sha256(response.content).hexdigest() == final["sha256"]
            assert len(response.content) == final["size"]
            assert response.headers["cache-control"] == "private, no-store"
            assert "wordprocessingml.document" in response.headers["content-type"]
            assert "attachment" in response.headers["content-disposition"]
            assert "只检查文件下载" in "".join(p.text for p in Document(io.BytesIO(response.content)).paragraphs)
            identity["user_id"] = 202
            assert client.get(final["download_url"]).status_code == 404
    finally:
        tracing.set_user_id(0)
        tracing.set_session_id("")
        tracing.set_client_request_id("")
        tracing.set_current_tool_name("")

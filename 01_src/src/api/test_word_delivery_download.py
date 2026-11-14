"""Run in a separate pytest process: API imports bind runtime stores.

Uses the actual account-download handler in an isolated ASGI app with registered
accounts, owned chat sessions and real browser tokens. No production server or model is called.
"""
import hashlib
import io


def test_real_word_download_bytes_and_cross_account_denial(tmp_path, monkeypatch):
    import src.config as config
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    from src.api import main
    from src.session import files
    from src.research import tools, word_tools
    from src.core import tracing, account_leases
    from src import auth
    from src.session import manager
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from docx import Document

    monkeypatch.setattr(files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "DB_PATH", tmp_path / "state/session_files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", tmp_path / "state/session_files")
    monkeypatch.setattr(tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(word_tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auth, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "state/accounts.sqlite3")
    monkeypatch.setattr(account_leases, "_states", {})
    auth.init_store()
    owner, owner_token, error = auth.register("word_download_owner", "WordFixture!2026")
    assert owner and owner_token and error is None
    other, other_token, error = auth.register("word_download_other", "WordFixture!2026")
    assert other and other_token and error is None
    monkeypatch.setattr(manager.session_manager, "db_path", str(tmp_path / "state/sessions.db"))
    manager.session_manager._init_db_sync()
    manager.session_manager._create_session_sync("session-download-001", owner["user_id"])
    manager.session_manager._create_session_sync("session-download-002", other["user_id"])
    assert manager.session_manager.get_session_owner("session-download-001") == owner["user_id"]
    tracing.set_user_id(owner["user_id"])
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
        app = FastAPI()

        @app.middleware("http")
        async def authenticate(request, call_next):
            request.state.user = auth.user_for_token(request.cookies.get(auth.COOKIE_NAME, ""))
            return await call_next(request)

        app.add_api_route("/api/v2/files/{file_id}/download", main.download_account_file, methods=["GET"])
        with TestClient(app) as client:
            assert client.get(final["download_url"]).status_code == 401
            client.cookies.set(auth.COOKIE_NAME, owner_token)
            response = client.get(final["download_url"])
            assert response.status_code == 200
            assert hashlib.sha256(response.content).hexdigest() == final["sha256"]
            assert len(response.content) == final["size"]
            assert response.headers["cache-control"] == "private, no-store"
            assert "wordprocessingml.document" in response.headers["content-type"]
            assert "attachment" in response.headers["content-disposition"]
            assert "只检查文件下载" in "".join(p.text for p in Document(io.BytesIO(response.content)).paragraphs)
            client.cookies.set(auth.COOKIE_NAME, other_token)
            assert client.get(final["download_url"]).status_code == 404
            assert auth.user_by_id(owner["user_id"])["active"]
            assert auth.user_by_id(other["user_id"])["active"]
    finally:
        tracing.set_user_id(0)
        tracing.set_session_id("")
        tracing.set_client_request_id("")
        tracing.set_current_tool_name("")

"""S4 回归：预览用的 MIME 必须能内联渲染。

历史记录里存过空 MIME 或 ``application/octet-stream``，浏览器拿到后不会渲染而是
下载，iframe 里就表现为“空白”。这里把“按后缀归一”这条规则钉住。

运行：`python -m pytest src/session/test_file_preview_mime.py -q`
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from src.session.files import _canonical_mime, _row_to_public, kind_for  # noqa: E402


def test_legacy_octet_stream_pdf_becomes_pdf():
    assert _canonical_mime("report.pdf", "application/octet-stream") == "application/pdf"


def test_empty_mime_pdf_becomes_pdf():
    assert _canonical_mime("report.PDF", "") == "application/pdf"


def test_pdf_with_charset_parameter_is_normalized():
    assert _canonical_mime("x.pdf", "application/pdf; charset=utf-8") == "application/pdf"


def test_images_are_pinned_by_suffix():
    assert _canonical_mime("a.png", "") == "image/png"
    assert _canonical_mime("a.jpg", "application/octet-stream") == "image/jpeg"


def test_text_and_tables_keep_a_charset():
    assert _canonical_mime("a.csv", "") == "text/csv; charset=utf-8"
    assert _canonical_mime("a.txt", "text/plain") == "text/plain; charset=utf-8"


def test_unknown_suffix_keeps_reported_mime():
    assert _canonical_mime("a.docx",
                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                           ) .startswith("application/vnd.openxmlformats")


def test_empty_mime_unknown_suffix_falls_back_to_guess():
    assert _canonical_mime("a.bin", "") == "application/octet-stream"


def test_row_to_public_repairs_mime_and_kind():
    row = {"file_id": "c" * 32, "session_id": "s1", "filename": "report.pdf",
           "mime_type": "application/octet-stream", "origin": "uploaded",
           "size_bytes": 10, "sha256": "z", "storage_path": "/tmp/z",
           "artifact_id": "", "created_at": 1.0, "last_accessed_at": 1.0,
           "client_request_id": ""}
    public = _row_to_public(row)
    assert public["mime_type"] == "application/pdf"
    assert public["kind"] == "pdf"


def test_kind_for_pdf_is_stable():
    assert kind_for("x.pdf", "application/pdf") == "pdf"
    assert kind_for("x.pdf", "application/octet-stream") == "pdf"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as exc:
                fails += 1
                print("FAIL", name, exc)
    print("FAILS:", fails)
    raise SystemExit(1 if fails else 0)

def test_preview_route_serves_legacy_row_as_pdf(monkeypatch):
    """路由层：老记录的空 MIME 不能把 PDF 退化成 octet-stream（否则浏览器下载）。"""
    import asyncio
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import src.session.files as session_files

    tmp = Path(__file__).resolve().parent / "_tmp_preview_probe.pdf"
    tmp.write_bytes(b"%PDF-1.4\nprobe\n%%EOF\n")
    try:
        legacy = {
            "file_id": "d" * 32, "session_id": "s-route", "filename": "report.pdf",
            "mime_type": "", "origin": "uploaded", "size_bytes": tmp.stat().st_size,
            "sha256": "z", "path": str(tmp),
            "download_url": "/api/v2/files/" + "d" * 32 + "/download",
        }
        monkeypatch.setattr(session_files, "get_account_file",
                            lambda file_id, owner_user_id, touch=False: dict(legacy))

        # 只挂一个最小路由，直接复用真实端点函数，避免整机启动。
        from src.api.main import preview_account_file

        app = FastAPI()

        @app.get("/api/v2/files/{file_id}/preview")
        async def _preview(file_id: str):  # noqa: ANN001
            class _Req:  # 端点只用到 request 之外的 user，这里直接绕过鉴权
                pass
            return await preview_account_file(file_id, _Req())  # type: ignore[arg-type]

        monkeypatch.setattr("src.api.main.current_user", lambda request: {"user_id": 1})
        with TestClient(app) as client:
            resp = client.get("/api/v2/files/" + "d" * 32 + "/preview")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/pdf")
        assert resp.content.startswith(b"%PDF-")
    finally:
        tmp.unlink(missing_ok=True)

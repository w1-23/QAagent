"""Boundary matrix for chat-attachment parsing: PDF / XLSX / DOCX.

Each case pins one real limit that exists in ``src/api/main.py`` so a future
refactor cannot silently drop the guard.  Fixtures are generated in-memory; no
network, no model call, no production data.
"""
from __future__ import annotations

import base64
import io
import tempfile
import atexit
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException

import src.config as config
_test_runtime = tempfile.TemporaryDirectory(
    prefix=".test-file-boundary-", dir=Path(__file__).resolve().parents[2],
    ignore_cleanup_errors=True)
config.PROJECT_ROOT = Path(_test_runtime.name)

from src.api import main as api_main
from src.api.main import _prepare_chat_attachments
from src.core import file_safety

atexit.register(_test_runtime.cleanup)


def _item(name: str, payload: bytes, mime: str) -> dict[str, str]:
    encoded = base64.b64encode(payload).decode("ascii")
    return {"name": name, "type": mime, "data": f"data:{mime};base64,{encoded}"}


def _pdf(pages: list[str]) -> bytes:
    import fitz
    document = fitz.open()
    try:
        for text in pages:
            page = document.new_page()
            if text:
                page.insert_textbox(fitz.Rect(36, 36, 559, 806), text, fontsize=9)
        return document.tobytes()
    finally:
        document.close()


def _scanned_pdf() -> bytes:
    import fitz
    document = fitz.open()
    try:
        page = document.new_page()
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 64, 64), 0)
        pixmap.clear_with(180)
        page.insert_image(fitz.Rect(60, 60, 260, 260), pixmap=pixmap)
        return document.tobytes()
    finally:
        document.close()


def _xlsx(sheets: dict[str, list[list[object]]]) -> bytes:
    from openpyxl import Workbook
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets.items():
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _docx(paragraphs: list[str]) -> bytes:
    from docx import Document
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------- PDF ----

def test_pdf_text_pages_are_extracted_without_images():
    # fitz 的内置字体不含 CJK，夹具用 ASCII 文本；中文路径另由真实上传探针覆盖。
    text, images, tables = _prepare_chat_attachments([
        _item("protocol.pdf", _pdf(["Page one: sampling protocol",
                                    "Page two: statistics"]), "application/pdf")])
    assert "sampling protocol" in text and "statistics" in text
    assert images == []
    assert len(tables) == 1 and tables[0]["name"] == "protocol.pdf"
    assert "扫描版" not in text


def test_scanned_pdf_falls_back_to_page_images():
    text, images, _ = _prepare_chat_attachments([
        _item("scan.pdf", _scanned_pdf(), "application/pdf")])
    assert "扫描版 PDF" in text
    assert len(images) == 1
    assert images[0]["url"].startswith("data:image/png;base64,")


def test_pdf_page_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(api_main, "_MAX_CHAT_PDF_PAGES", 2)
    text, _, _ = _prepare_chat_attachments([
        _item("long.pdf", _pdf(["A" * 50, "B" * 50, "C" * 50]), "application/pdf")])
    assert "处理范围：最多 2 页" in text
    assert "C" * 50 not in text


def test_pdf_char_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(api_main, "_MAX_CHAT_DOCUMENT_TEXT_CHARS", 20)
    text, _, _ = _prepare_chat_attachments([
        _item("big.pdf", _pdf(["0123456789" * 5]), "application/pdf")])
    assert "处理范围" in text
    assert "0123456789" * 5 not in text


def test_pdf_without_magic_header_is_rejected():
    with pytest.raises(HTTPException) as excinfo:
        _prepare_chat_attachments([_item("fake.pdf", b"just text", "application/pdf")])
    assert excinfo.value.status_code == 400
    assert "格式与内容不匹配" in excinfo.value.detail


def test_corrupt_pdf_is_rejected_with_named_error():
    with pytest.raises(HTTPException) as excinfo:
        _prepare_chat_attachments([
            _item("broken.pdf", b"%PDF-1.7\n" + b"\x00" * 64, "application/pdf")])
    assert excinfo.value.status_code == 400
    assert "无法读取 PDF：broken.pdf" in excinfo.value.detail


# --------------------------------------------------------------- XLSX ----

def test_xlsx_multi_sheet_is_registered_as_table():
    payload = _xlsx({"原始数据": [["样本", "糖度"], ["A1", 11.2]], "统计": [["指标", "均值"]]})
    text, _, tables = _prepare_chat_attachments([
        _item("data.xlsx", payload, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")])
    assert len(tables) == 1
    assert "表格附件：data.xlsx" in text
    assert "inspect_uploaded_table" in text


def test_xlsx_without_zip_header_is_rejected():
    with pytest.raises(HTTPException) as excinfo:
        _prepare_chat_attachments([_item("fake.xlsx", b"id,sugar\nA1,11", "application/vnd.ms-excel")])
    assert excinfo.value.status_code == 400
    assert "XLSX 文件格式与内容不匹配" in excinfo.value.detail


def test_xlsx_member_size_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(file_safety, "MAX_ARCHIVE_MEMBER_BYTES", 10)
    payload = _xlsx({"数据": [["a", "b"], [1, 2]]})
    with pytest.raises(HTTPException) as excinfo:
        _prepare_chat_attachments([_item("big.xlsx", payload, "application/vnd.ms-excel")])
    assert excinfo.value.status_code == 413
    assert "64 MB" in excinfo.value.detail or "超过" in excinfo.value.detail


def test_xlsx_member_count_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(file_safety, "MAX_ARCHIVE_MEMBERS", 1)
    payload = _xlsx({"数据": [["a", "b"]]})
    with pytest.raises(HTTPException) as excinfo:
        _prepare_chat_attachments([_item("many.xlsx", payload, "application/vnd.ms-excel")])
    assert excinfo.value.status_code == 413
    assert "成员" in excinfo.value.detail


def test_xlsx_corrupt_zip_is_rejected():
    buffer = io.BytesIO()
    buffer.write(b"PK\x03\x04")
    buffer.write(b"garbage" * 8)
    with pytest.raises(HTTPException) as excinfo:
        _prepare_chat_attachments([_item("bad.xlsx", buffer.getvalue(), "application/vnd.ms-excel")])
    assert excinfo.value.status_code == 413
    assert "不是有效的 ZIP" in excinfo.value.detail


# --------------------------------------------------------------- DOCX ----

def test_docx_paragraphs_are_extracted():
    text, images, tables = _prepare_chat_attachments([
        _item("notes.docx", _docx(["处理一：40℃ 烘干", "处理二：冷冻保存"]),
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document")])
    assert "40℃ 烘干" in text and "冷冻保存" in text
    assert images == []
    assert len(tables) == 1 and tables[0]["name"] == "notes.docx"


def test_docx_document_xml_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(api_main, "_MAX_OOXML_DOCUMENT_XML_BYTES", 64)
    with pytest.raises(HTTPException) as excinfo:
        _prepare_chat_attachments([
            _item("huge.docx", _docx(["一段足够长的正文" * 20]),
                  "application/vnd.openxmlformats-officedocument.wordprocessingml.document")])
    assert excinfo.value.status_code == 413
    assert "Word 正文 XML" in excinfo.value.detail


def test_docx_missing_document_xml_is_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("docProps/app.xml", "<app/>")
    with pytest.raises(HTTPException) as excinfo:
        _prepare_chat_attachments([
            _item("nodoc.docx", buffer.getvalue(),
                  "application/vnd.openxmlformats-officedocument.wordprocessingml.document")])
    assert excinfo.value.status_code in {400, 413}

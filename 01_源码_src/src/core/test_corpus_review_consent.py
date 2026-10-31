# -*- coding: utf-8 -*-
"""上传共享授权（consent）单元测试：默认不归档，需明确授权或管理员角色。

不联网、不调模型、不碰真实数据目录。
"""

import pytest

from src.core import corpus_review as cr


@pytest.fixture()
def store(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(cr, "_state_dir", lambda: state)
    monkeypatch.setattr(cr, "_data_dir", lambda: data)
    monkeypatch.setattr(cr, "_schema_ready", False)
    return {"state": state, "data": data}


PDF = b"%PDF-1.7\n" + b"x" * 3000 + b"\n%%EOF\n"


def _archived(store):
    root = store["data"] / "_review_queue" / "archive"
    return list(root.rglob("original*")) if root.exists() else []


def test_consent_defaults_to_off_and_can_be_toggled(store):
    assert cr.consent_enabled(7) is False
    assert cr.set_consent(7, True)["enabled"] is True
    assert cr.consent_enabled(7) is True
    assert cr.set_consent(7, False)["enabled"] is False
    assert cr.consent_enabled(7) is False
    assert cr.consent_enabled(0) is False


def test_unknown_user_has_no_consent(store):
    assert cr.consent_enabled(999) is False


def test_upload_without_consent_is_not_archived(store):
    assert cr.archive_upload("paper.pdf", PDF, "正文", consent=False, is_admin=False) is None
    assert _archived(store) == []
    assert cr.list_candidates(status="pending")["candidates"] == []


def test_upload_with_explicit_consent_is_archived(store):
    result = cr.archive_upload("paper.pdf", PDF, "正文", consent=True, user_id=7)
    assert result and result["status"] == "pending"
    files = _archived(store)
    assert len(files) == 1 and files[0].name == "original.pdf"
    assert files[0].read_bytes() == PDF


def test_admin_upload_is_archived_without_user_consent(store):
    result = cr.archive_upload("admin.pdf", PDF, "正文", consent=False, is_admin=True)
    assert result and result["status"] == "pending"


def test_non_pdf_masquerading_is_rejected_even_with_consent(store):
    assert cr.archive_upload("fake.pdf", b"not a pdf at all" * 100, "x", consent=True) is None


def test_capture_hook_defaults_to_no_archive(store, monkeypatch):
    """没有请求上下文（默认 (0,0)）时，上传钩子不得归档。"""
    from src.api import main as api_main
    api_main._REVIEW_UPLOAD_SCOPE.set((0, 0))
    api_main._capture_upload_candidate("paper.pdf", "正文", PDF)
    assert _archived(store) == []


def test_capture_hook_archives_for_consenting_user(store):
    from src.api import main as api_main
    cr.set_consent(7, True)
    api_main._REVIEW_UPLOAD_SCOPE.set((7, 0))
    try:
        api_main._capture_upload_candidate("paper.pdf", "正文", PDF)
    finally:
        api_main._REVIEW_UPLOAD_SCOPE.set((0, 0))
    assert len(_archived(store)) == 1


def test_capture_hook_archives_for_admin(store):
    from src.api import main as api_main
    api_main._REVIEW_UPLOAD_SCOPE.set((7, 1))
    try:
        api_main._capture_upload_candidate("paper.pdf", "正文", PDF)
    finally:
        api_main._REVIEW_UPLOAD_SCOPE.set((0, 0))
    assert len(_archived(store)) == 1

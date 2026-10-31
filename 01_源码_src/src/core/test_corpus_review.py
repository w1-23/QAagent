# -*- coding: utf-8 -*-
"""文献候审库单元测试：只存原始资料，不切块、不建索引、不激活语料。

测试不联网、不调模型、不碰真实语料目录。
"""

import json
import pathlib

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


def _winner(doi: str, title: str = "柑橘黄龙病防控研究", chars: int = 4000,
            content: str | None = None, fmt: str = "html") -> dict:
    body = content if content is not None else (
        "<html><body><h1>柑橘黄龙病防控研究</h1>"
        + "<p>" + ("黄龙病防控与媒介昆虫防治。" * (chars // 12)) + "</p>"
        + f"<p>DOI: {doi}</p></body></html>")
    return {"title": title, "url": f"https://example.org/{doi}",
            "content": body, "format": fmt}


# ------------------------------------------------------------------ 抓取归档

def test_archive_fetch_saves_original_markdown_and_meta(store):
    result = cr.archive_fetch(_winner("10.1000/abc123"), session_id="s1", request_id="r1")
    assert result and result["status"] == "pending" and result["deduped"] is False

    archive = store["data"] / "_review_queue" / "archive"
    papers = list(archive.rglob("paper.md"))
    assert len(papers) == 1
    text = papers[0].read_text(encoding="utf-8")
    assert "# 柑橘黄龙病防控研究" in text          # HTML 标题转成了 Markdown
    assert "10.1000/abc123" in text               # DOI 保留
    assert "<p>" not in text and "<h1>" not in text  # 标签已转换

    meta = json.loads((papers[0].parent / "meta.json").read_text(encoding="utf-8"))
    assert meta["raw_format"] == "html"
    assert meta["conversion"] == "html_to_markdown"
    assert meta["doi"] == "10.1000/abc123"
    assert meta["sha256_of_saved_bytes"] == meta["sha256_of_saved_bytes"].lower()
    assert meta["session_id"] == "s1" and meta["request_id"] == "r1"

    item = cr.list_candidates(status="pending")["candidates"][0]
    assert item["raw_format"] == "html"
    assert item["raw_bytes"] > 0
    assert item["stored_path"].endswith("paper.md")


def test_archive_fetch_is_idempotent_per_doi_and_bumps_frequency(store):
    cr.archive_fetch(_winner("10.1000/same"), session_id="s1")
    cr.archive_fetch(_winner("10.1000/same"), session_id="s2")
    listing = cr.list_candidates(status="pending")
    assert len(listing["candidates"]) == 1
    assert listing["candidates"][0]["frequency"] == 2
    assert len(list(list((store["data"] / "_review_queue" / "archive").rglob("paper.md")))) == 1


def test_archive_fetch_skips_short_body(store):
    assert cr.archive_fetch(_winner("10.1000/short", chars=400)) is None
    assert cr.list_candidates(status="pending")["candidates"] == []


def test_archive_fetch_never_raises_on_bad_input(store):
    assert cr.archive_fetch(None) is None          # type: ignore[arg-type]
    assert cr.archive_fetch({"content": None}) is None
    assert cr.list_candidates(status="pending")["candidates"] == []


def test_plain_text_stays_plain_text(store):
    cr.archive_fetch(_winner("10.1000/txt", fmt="plain",
                             content="柑橘黄龙病防控研究\n" + "正文。" * 1200))
    item = cr.list_candidates(status="pending")["candidates"][0]
    assert item["raw_format"] == "plain_text"
    assert item["conversion"] == "none(plain_text_passthrough)"
    # 纯文本不冒充 Markdown：落盘扩展名跟随真实格式
    assert item["stored_path"].endswith("paper.txt")


def test_archive_fetch_does_not_touch_corpus_index(store):
    cr.archive_fetch(_winner("10.1000/noindex"))
    assert not (store["data"] / "corpus_generations").exists()
    assert not (store["data"] / "active_index.json").exists()


# ------------------------------------------------------------------ 上传归档

def _pdf_bytes(pages: int = 2) -> bytes:
    body = b"%PDF-1.4\n"
    for index in range(pages):
        body += f"{index + 1} 0 obj<<>>stream\nBT /F1 12 Tf (page {index}) Tj ET\nendstream\nendobj\n".encode()
    return body + b"%%EOF\n"


def test_archive_upload_keeps_raw_pdf_bytes(store):
    raw = _pdf_bytes()
    result = cr.archive_upload("黄龙病综述.pdf", raw, "提取文本" * 600, consent=True)
    assert result and result["status"] == "pending"
    saved = list((store["data"] / "_review_queue" / "archive").rglob("original.pdf"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == raw          # 原始字节完全一致，不做转换
    meta = json.loads((saved[0].parent / "meta.json").read_text(encoding="utf-8"))
    assert meta["raw_format"] == "pdf_binary"
    assert meta["bytes"] == len(raw)


def test_archive_upload_requires_consent(store):
    assert cr.archive_upload("x.pdf", _pdf_bytes(), "text" * 600) is None
    assert cr.list_candidates(status="pending")["candidates"] == []


def test_archive_upload_rejects_fake_pdf(store):
    assert cr.archive_upload("fake.pdf", b"not a pdf at all", "text" * 600, consent=True) is None


# ------------------------------------------------------------------ 决策与查询

def test_decide_transitions_are_idempotent(store):
    created = cr.archive_fetch(_winner("10.1000/flow"))
    cid = created["id"]
    first = cr.decide(cid, "approve", admin_user_id=7)
    assert first["candidate"]["status"] == "approved" and first["changed"] is True
    again = cr.decide(cid, "approve", admin_user_id=7)
    assert again["candidate"]["status"] == "approved" and again["idempotent"] is True

    rejected = cr.decide(cid, "reject", admin_user_id=7, note="重复")
    assert rejected["candidate"]["status"] == "rejected"
    assert rejected["candidate"]["note"] == "重复"

    reopened = cr.decide(cid, "reopen", admin_user_id=7)
    assert reopened["candidate"]["status"] == "pending"

    with pytest.raises(ValueError):
        cr.decide(cid, "explode", admin_user_id=7)


def test_list_orders_by_frequency(store):
    low = cr.archive_fetch(_winner("10.1000/low"))
    cr.archive_fetch(_winner("10.1000/high"))
    cr.archive_fetch(_winner("10.1000/high"))
    items = cr.list_candidates(status="pending", order="frequency")["candidates"]
    assert items[0]["frequency"] == 2 and items[-1]["frequency"] == 1
    newest = cr.list_candidates(status="pending", order="recent")["candidates"]
    assert newest[0]["id"] >= low["id"]


def test_raw_file_points_at_real_bytes(store):
    created = cr.archive_fetch(_winner("10.1000/raw"))
    info = cr.raw_file(created["id"])
    assert pathlib.Path(info["path"]).is_file()
    assert info["mime"].startswith("text/markdown")
    with pytest.raises(KeyError):
        cr.raw_file(999999)


def test_counts_and_pagination_shape(store):
    for index in range(3):
        cr.archive_fetch(_winner(f"10.1000/page{index}"))
    first = cr.list_candidates(status="pending", limit=2)
    assert len(first["candidates"]) == 2 and first["has_more"] is True
    rest = cr.list_candidates(status="pending", limit=2, before_id=first["next_before_id"])
    assert len(rest["candidates"]) == 1 and rest["has_more"] is False
    assert first["counts"]["pending"] == 3

"""本地文献原文只读接口（``GET /api/v2/corpus/source``）的契约测试。

覆盖：成功响应字段、chunks/chars 数字、相邻同名章节合并、空章节名回退「正文」、
题名标签不出现裸内部键、无 DOI 记录照常返回、未知/非法/空 record 404、未登录 401、
私有语料（cqa_methods）管理员门禁、绝对路径与内部键脱敏、索引缓存与失效、
语料根目录为空时的 404，以及（本机有真实语料时）三个真实键的数字验收。

测试用 ASGI 直连（不启真实服务、不加载模型），语料用 tmp_path 合成，只碰临时目录。
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from starlette.requests import Request

from src import auth
from src.api.main import app
from src.api.main import corpus_source_endpoint
from src.core import corpus_source

URI = "/api/v2/corpus/source"

REGULAR = {"user_id": 7, "username": "member", "role": "user", "active": True}
ADMIN = {"user_id": 1, "username": "owner", "role": "admin", "active": True}

# 主记录：有 DOI、正文含绝对路径与裸内部键、末尾有空章节名分块。
LIT = "paper_aaaabbbb_000001_B_2017_Nature_Genetics_"
# 无 DOI 记录：section_name 只有通用章节名 → 题名回退 display_label。
NO_DOI = "paper_ccccdddd_000042_B_2019_BMC_Plant_Biolog"
# 行内 year 为空 → 用键里自带的 4 位年份；source_file 是绝对路径（须被清空）。
KEY_YEAR = "paper_eeeeffff_000007_A_2016_Nature_Plants__"
# 同键同时出现在两个语料 → 按 CORPUS_ORDER 取 cqa_literature。
DUPLICATE = "paper_gggghhhh_000009_A_2015_Science_"
# 私有语料记录。
METHOD = "paper_iiiijjjj_000003_A_2020_Methods_X"

# 真实语料根：优先取运行环境里的 settings.DATA_DIR（Windows 本机 = agent/data，
# Linux 生产 = /home/WZQ/CitrusQA/agent/data），让同一份验收在本机也能跑。
def _real_data_dir() -> Path:
    try:
        from src.config import settings as _settings

        return Path(_settings.DATA_DIR)
    except Exception:  # pragma: no cover - 配置不可用时退回生产路径
        return Path("/home/WZQ/CitrusQA/agent/data")


REAL_DATA_DIR = _real_data_dir()
REAL_KEYS = (
    ("paper_45bbcac3_000004_B_2017_Nature_Genetics_", 24, 30810),
    ("paper_1c63bb9b_000593_F_2018_BMC_Plant_Biolog", 42, 53668),
    ("paper_82f00138_000125_E_2021_DNA_research_an_", 42, 52433),
)


@asynccontextmanager
async def _test_lifespan(_app):
    yield


app.router.lifespan_context = _test_lifespan


def _request(path: str, cookies: dict | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)

    async def _go():
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://testserver",
                                     follow_redirects=False) as client:
            if cookies:
                client.cookies.update(cookies)
            return await client.get(path)

    return asyncio.run(_go())


def _source(record: str, cookies: dict | None) -> httpx.Response:
    return _request(f"{URI}?record={quote(record, safe='')}", cookies)


def _as_regular(monkeypatch) -> dict:
    monkeypatch.setattr(auth, "user_for_token",
                        lambda token: REGULAR if token == "member-session" else None)
    return {"citrus_auth": "member-session"}


def _as_admin(monkeypatch) -> dict:
    monkeypatch.setattr(auth, "user_for_token",
                        lambda token: ADMIN if token == "owner-session" else None)
    return {"citrus_auth": "owner-session"}


def _row(paper_id: str, chunk_index: int, section: str, text: str, **extra) -> dict:
    row = {
        "paper_id": paper_id,
        "chunk_index": chunk_index,
        "section_name": section,
        "text": text,
        "title": paper_id,
        "year": "",
        "doi": "",
        "source_file": "english_literature/sample.md",
        "source_type": "literature",
        "chunk_id": f"{paper_id}::{chunk_index:04d}",
    }
    row.update(extra)
    return row


def _write_corpus(root: Path, name: str, rows: list[dict]) -> None:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "chunks.jsonl").open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


@pytest.fixture()
def corpus(tmp_path, monkeypatch) -> Path:
    """合成 5 个语料里的 4 个（cqa_deng_book 故意缺失，验证缺目录不炸）。"""
    root = tmp_path / "agent_data"
    _write_corpus(root, "cqa_literature", [
        _row(LIT, 0, "Genomic analyses of citrus apomixis",
             "Abstract: we re-sequenced 12 accessions stored at "
             "/home/secret/corpus.txt and D:\\hidden\\raw.txt .",
             doi="10.1038/ng.1234", year="2017",
             source_file="english_literature/001_x.md"),
        _row(LIT, 1, "Abstract",
             "Pathogen-free material. See "
             "004_paper_aaaabbbb_000001_B_2017_Nature_Genetics_ for details."),
        _row(LIT, 2, "Results", "Result one."),
        _row(LIT, 3, "Results", "Result two."),
        _row(LIT, 4, "", "Tail text without a section."),
        _row(NO_DOI, 0, "Abstract", "Background: somatic embryogenesis.", year="2019"),
        _row(NO_DOI, 1, "Introduction", "Intro.", year="2019"),
        _row(KEY_YEAR, 0, "Letter", "Letter body.",
             source_file="/home/WZQ/leak/sample.md"),
        _row(DUPLICATE, 0, "Report", "Science report body."),
    ])
    _write_corpus(root, "cqa_translated", [
        _row(DUPLICATE, 0, "Report", "译稿正文。", source_type="translated literature"),
    ])
    _write_corpus(root, "cqa_methods", [
        _row(METHOD, 0, "Protocol", "Protocol body.", source_type="experiment method"),
    ])
    _write_corpus(root, "cqa_ucr_varieties", [
        _row("paper_kkkkllll_000011_A_2014_HortScience_", 0, "Cultivar",
             "UCR variety body.", source_type="UCR citrus variety"),
    ])

    monkeypatch.setattr(corpus_source, "DATA_DIR", root)
    corpus_source.clear_index_cache()
    yield root
    corpus_source.clear_index_cache()


# --- 1. 成功响应的字段与数字 -------------------------------------------------

def test_success_payload_has_contract_fields(monkeypatch, corpus) -> None:
    cookies = _as_regular(monkeypatch)
    response = _source(LIT, cookies)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {
        "ok", "record_key", "corpus", "source_type", "title_label", "year", "doi",
        "journal", "has_doi", "chunks", "chars", "sections", "markdown",
    }
    assert body["ok"] is True
    assert body["record_key"] == LIT
    assert body["corpus"] == "cqa_literature"
    assert body["source_type"] == "literature"
    assert isinstance(body["sections"], list) and body["sections"]
    for section in body["sections"]:
        assert set(section) == {"name", "text", "chunk_ids"}
        assert isinstance(section["chunk_ids"], list)


def test_chunks_and_chars_cover_all_chunks(monkeypatch, corpus) -> None:
    body = _source(LIT, _as_regular(monkeypatch)).json()
    # 5 个分块，字符数是原始 text 长度之和（未脱敏时计）。
    assert body["chunks"] == 5
    assert body["chars"] == sum(len(row["text"]) for row in _lit_rows())


def _lit_rows() -> list[dict]:
    texts = [
        "Abstract: we re-sequenced 12 accessions stored at "
        "/home/secret/corpus.txt and D:\\hidden\\raw.txt .",
        "Pathogen-free material. See "
        "004_paper_aaaabbbb_000001_B_2017_Nature_Genetics_ for details.",
        "Result one.",
        "Result two.",
        "Tail text without a section.",
    ]
    return [{"text": text} for text in texts]


# --- 2. 章节合并 / 空章节名 ---------------------------------------------------

def test_adjacent_same_section_is_merged(monkeypatch, corpus) -> None:
    body = _source(LIT, _as_regular(monkeypatch)).json()
    names = [section["name"] for section in body["sections"]]
    # Results 出现两次相邻 → 合并为一个；末尾空章节名回退「正文」。
    assert names == ["Genomic analyses of citrus apomixis", "Abstract", "Results", "正文"]
    results = body["sections"][2]
    assert results["text"] == "Result one.\n\nResult two."
    assert results["chunk_ids"] == [f"{LIT}::0002", f"{LIT}::0003"]


def test_sections_follow_chunk_index_order(monkeypatch, corpus) -> None:
    body = _source(LIT, _as_regular(monkeypatch)).json()
    ids = [chunk_id for section in body["sections"] for chunk_id in section["chunk_ids"]]
    assert ids == [f"{LIT}::{index:04d}" for index in range(5)]
    assert body["markdown"].index("Result one.") < body["markdown"].index("Tail text")


def test_markdown_structure_and_empty_section_label(monkeypatch, corpus) -> None:
    body = _source(LIT, _as_regular(monkeypatch)).json()
    markdown = body["markdown"]
    assert markdown.startswith(f"# {body['title_label']}\n")
    assert "## Genomic analyses of citrus apomixis" in markdown
    assert "## Results" in markdown
    assert "## 正文" in markdown


# --- 3. 题名标签 -------------------------------------------------------------

def test_title_label_prefers_real_article_title(monkeypatch, corpus) -> None:
    body = _source(LIT, _as_regular(monkeypatch)).json()
    assert body["title_label"] == "Genomic analyses of citrus apomixis"
    assert "paper_" not in body["title_label"]


def test_title_label_falls_back_to_display_label(monkeypatch, corpus) -> None:
    body = _source(NO_DOI, _as_regular(monkeypatch)).json()
    label = body["title_label"]
    assert "未具题名文献" in label
    assert "BMC Plant Biolog" in label and "42" in label
    # 任何情况下都不能出现裸内部键。
    assert "paper_" not in label
    assert label in body["markdown"]


# --- 4. year / doi -----------------------------------------------------------

def test_record_without_doi_is_returned_normally(monkeypatch, corpus) -> None:
    response = _source(NO_DOI, _as_regular(monkeypatch))
    assert response.status_code == 200
    body = response.json()
    assert body["has_doi"] is False
    assert body["doi"] == ""
    assert body["year"] == "2019"
    assert body["chunks"] == 2


def test_doi_is_returned_verbatim_when_present(monkeypatch, corpus) -> None:
    body = _source(LIT, _as_regular(monkeypatch)).json()
    assert body["doi"] == "10.1038/ng.1234"
    assert body["has_doi"] is True
    assert body["year"] == "2017"


def test_year_falls_back_to_record_key_when_row_empty(monkeypatch, corpus) -> None:
    body = _source(KEY_YEAR, _as_regular(monkeypatch)).json()
    assert body["year"] == "2016"
    assert body["doi"] == ""
    assert body["has_doi"] is False


# --- 5. 未知 / 非法 / 空 record ----------------------------------------------

def test_unknown_record_is_404(monkeypatch, corpus) -> None:
    response = _source("paper_doesnotexist_000000_X_2020_Nowhere_", _as_regular(monkeypatch))
    assert response.status_code == 404
    assert response.json() == {"ok": False, "reason": "not_found", "message": "本地原文不可用"}


@pytest.mark.parametrize("record", [
    "../../etc/passwd",
    "/etc/passwd",
    "..%2F..%2Fetc%2Fpasswd",
    "a" * 200,
    "!bad!key!",
    "short",
])
def test_invalid_record_is_404_without_exception(monkeypatch, corpus, record) -> None:
    response = _source(record, _as_regular(monkeypatch))
    assert response.status_code == 404, record
    assert response.json()["reason"] == "not_found"


def test_empty_record_is_not_500(monkeypatch, corpus) -> None:
    response = _request(URI, _as_regular(monkeypatch))
    assert response.status_code in (404, 422), response.status_code


def test_missing_record_parameter_is_not_500(monkeypatch, corpus) -> None:
    response = _request(f"{URI}?record=", _as_regular(monkeypatch))
    assert response.status_code in (404, 422)


def test_empty_corpus_root_is_404(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(corpus_source, "DATA_DIR", tmp_path / "nothing_here")
    corpus_source.clear_index_cache()
    response = _source(LIT, _as_regular(monkeypatch))
    assert response.status_code == 404
    assert response.json()["reason"] == "not_found"


# --- 6. 鉴权与私有语料门禁 ---------------------------------------------------

def test_unauthenticated_is_401(monkeypatch, corpus) -> None:
    monkeypatch.setattr(auth, "user_for_token", lambda _token: None)
    response = _source(LIT, {"citrus_auth": "nope"})
    assert response.status_code == 401
    assert response.json().get("ok") is not True


def test_endpoint_returns_contract_401_body(corpus) -> None:
    """中间件之外的兜底分支：端点自身也必须给契约 401 体。"""
    request = Request({"type": "http", "method": "GET", "path": URI,
                       "query_string": b"", "headers": [(b"host", b"testserver")]})
    response = asyncio.run(corpus_source_endpoint(request, record=LIT))
    assert response.status_code == 401
    assert json.loads(response.body) == {"ok": False, "reason": "unauthenticated"}


def test_private_corpus_requires_admin(monkeypatch, corpus) -> None:
    response = _source(METHOD, _as_regular(monkeypatch))
    assert response.status_code == 403
    assert response.json() == {"ok": False, "reason": "forbidden",
                               "message": "该来源需要更高权限"}


def test_private_corpus_allows_admin(monkeypatch, corpus) -> None:
    response = _source(METHOD, _as_admin(monkeypatch))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["corpus"] == "cqa_methods"
    assert body["source_type"] == "experiment method"
    assert body["chunks"] == 1


def test_public_corpus_allows_regular_account(monkeypatch, corpus) -> None:
    assert _source(LIT, _as_regular(monkeypatch)).status_code == 200


# --- 7. 脱敏：绝对路径与裸内部键 ---------------------------------------------

def test_absolute_paths_are_redacted(monkeypatch, corpus) -> None:
    body = _source(LIT, _as_regular(monkeypatch)).json()
    raw = json.dumps(body, ensure_ascii=False)
    assert "/home/" not in raw
    assert "D:\\\\hidden" not in raw and "D:\\hidden" not in raw
    assert "[路径已隐藏]" in raw


def test_bare_internal_key_is_scrubbed(monkeypatch, corpus) -> None:
    body = _source(LIT, _as_regular(monkeypatch)).json()
    assert "paper_aaaabbbb" not in body["markdown"]
    # chunk_ids 是契约要求保留的分块标识（前端做“来自第几段”），正文/章节名才需脱敏。
    visible = [(section["name"], section["text"]) for section in body["sections"]]
    assert "paper_aaaabbbb" not in json.dumps(visible, ensure_ascii=False)
    assert "一篇 2017 年 Nature Genetics 文献" in body["sections"][1]["text"]


def test_source_file_is_relative_metadata_only(monkeypatch, corpus) -> None:
    body = _source(KEY_YEAR, _as_regular(monkeypatch)).json()
    # 绝对路径的 source_file 不返回（只作元数据，永不用于 IO）。
    assert body["source_file"] == ""
    body = _source(LIT, _as_regular(monkeypatch)).json()
    assert body["source_file"] == "english_literature/001_x.md"
    assert not body["source_file"].startswith("/")


# --- 8. 语料优先级与索引缓存 -------------------------------------------------

def test_corpus_priority_follows_contract_order(monkeypatch, corpus) -> None:
    body = _source(DUPLICATE, _as_regular(monkeypatch)).json()
    assert body["corpus"] == "cqa_literature"
    assert body["source_type"] == "literature"
    assert corpus_source.locate_record(DUPLICATE) == "cqa_literature"


def test_index_is_built_once_and_reused(monkeypatch, corpus) -> None:
    calls: list[str] = []
    original = corpus_source._build_index

    def _counting(path, stat):
        calls.append(str(path))
        return original(path, stat)

    monkeypatch.setattr(corpus_source, "_build_index", _counting)
    cookies = _as_regular(monkeypatch)
    assert _source(LIT, cookies).status_code == 200
    built = len(calls)
    assert _source(LIT, cookies).status_code == 200
    assert _source(NO_DOI, cookies).status_code == 200
    assert len(calls) == built, "第二次请求不应重建索引"


def test_index_invalidated_when_chunks_file_changes(monkeypatch, corpus) -> None:
    cookies = _as_regular(monkeypatch)
    assert _source(LIT, cookies).json()["chunks"] == 5
    path = corpus / "cqa_literature" / "chunks.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(_row(LIT, 5, "Discussion", "New chunk."),
                                ensure_ascii=False) + "\n")
    body = _source(LIT, cookies).json()
    assert body["chunks"] == 6
    assert [section["name"] for section in body["sections"]][-2:] == ["正文", "Discussion"]
    assert body["sections"][-1]["text"] == "New chunk."


# --- 9. 真实语料验收（本机有语料时才跑） -------------------------------------

@pytest.mark.skipif(not (REAL_DATA_DIR / "cqa_literature" / "chunks.jsonl").is_file(),
                    reason="本机没有真实语料目录")
def test_real_corpus_three_keys(monkeypatch) -> None:
    monkeypatch.setattr(corpus_source, "DATA_DIR", REAL_DATA_DIR)
    corpus_source.clear_index_cache()
    try:
        cookies = _as_regular(monkeypatch)
        for key, chunks, chars in REAL_KEYS:
            response = _source(key, cookies)
            assert response.status_code == 200, key
            body = response.json()
            assert body["corpus"] == "cqa_literature"
            assert abs(body["chunks"] - chunks) <= max(1, chunks * 0.05), (key, body["chunks"])
            assert abs(body["chars"] - chars) <= chars * 0.05, (key, body["chars"])
            # 外部反查可能给这批文献补上 DOI，所以只断言「有无自洽」，
            # 不再断言「一定没有 DOI」。
            if body["has_doi"]:
                assert body["doi"] and "paper_" not in body["doi"], key
            else:
                assert body["doi"] == "", key
            assert "paper_" not in body["title_label"], key
            raw = json.dumps(body, ensure_ascii=False)
            assert "/home/" not in raw
            assert "paper_" not in body["markdown"]
    finally:
        corpus_source.clear_index_cache()

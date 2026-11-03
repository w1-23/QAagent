"""本地文献原文只读数据层（v9.8）。

背景：交付包里的 Markdown 原文件已不在这台机器上，本地库只保留了**分块文本**
（``<DATA_DIR>/<corpus>/chunks.jsonl``，一行一个 chunk）。所以“读原文”只能是：

    按 ``paper_id`` 取回该文献全部分块 → 按 ``chunk_index`` 升序 → 用
    ``section_name`` 还原章节。

这是本地库真实保留的内容，**不是**原始 ``.md`` 文件；对外接口必须如实标注，
不得声称返回的是原始文件。

与 ``src/api/main.py`` 的 ``GET /api/v2/corpus/source`` 契约配套，本模块只负责
数据层，鉴权/权限门禁在端点里做。硬约束：

- **只读**：不写盘、不改库、不改语料；
- 内容只来自 ``chunks.jsonl`` 的行；``record`` 永不参与文件系统路径拼接，
  语料目录名只来自本模块的常量白名单（先正则校验，再查索引，查不到即 404）；
- 每个语料扫一次建 ``paper_id -> [行字节偏移]`` 索引，进程内缓存，
  以文件 ``mtime + size`` 失效；分块正文按字节偏移 seek 按需读取
  （与 ``src/retrieval/multi_retriever.py`` 的读法一致），避免整库正文驻留内存；
- 展示标签一律复用 ``src/core/record_label.py``：任何情况下不出现裸内部键。
"""
from __future__ import annotations

import json
import re
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from src.config import settings
from src.core.record_label import (
    _GENERIC_SECTIONS,
    display_label,
    is_internal_record_key,
    parse_record_key,
    scrub_internal_keys,
)

__all__ = [
    "ADMIN_ONLY_CORPORA",
    "CORPUS_ORDER",
    "DATA_DIR",
    "clear_index_cache",
    "is_valid_record_key",
    "locate_record",
    "describe",
    "build_payload",
]

# 检索顺序即优先级：同一 paper_id 只会在其中一个语料里出现，顺序决定“先命中谁”。
CORPUS_ORDER: tuple[str, ...] = (
    "cqa_literature",
    "cqa_methods",
    "cqa_deng_book",
    "cqa_translated",
    "cqa_ucr_varieties",
)

# 行内 source_type 缺失时的语料级兜底（与各语料 metadata.json 的 summary.source_type 一致）。
DEFAULT_SOURCE_TYPE: dict[str, str] = {
    "cqa_literature": "literature",
    "cqa_methods": "experiment method",
    "cqa_deng_book": "Chinese citrus variety",
    "cqa_translated": "translated literature",
    "cqa_ucr_varieties": "UCR citrus variety",
}

# Kept as an empty compatibility export for older imports. Access levels now
# come from the corpus registry and cqa_methods is a public methods corpus.
ADMIN_ONLY_CORPORA = frozenset()

# 与其它语料模块保持一致的两处落盘形态（见 src/retrieval/corpus_inventory.py）。
_CHUNK_RELATIVE_PATHS: tuple[str, ...] = ("chunks.jsonl", "chunks/chunks.jsonl")

# 契约：record 必须是 8..160 字符的键名，且首字符为字母/数字。
RECORD_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,159}$")

# 运行时数据根：与 corpus_admin.py 同一做法（config 的 settings.DATA_DIR）。
DATA_DIR = Path(settings.DATA_DIR)

_EMPTY_SECTION_LABEL = "正文"
_REDACTED_PATH_LABEL = "[路径已隐藏]"
_ABS_PATH_RE = re.compile(
    r"(?:/(?:home|root|mnt|opt|srv|var|Users|data)/[^\s\"'<>`)\]]*"
    r"|[A-Za-z]:\\[^\s\"'<>`)\]]*)"
)
_MARKDOWN_HEADING_RE = re.compile(
    r"^\s{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*(?:\r?\n|$)"
)


@dataclass(frozen=True)
class _CorpusIndex:
    """一个语料的 ``paper_id -> 行字节偏移`` 索引（不可变，可跨线程共享）。"""

    path: Path
    mtime_ns: int
    size: int
    offsets: dict[str, list[int]]
    rows: int


_INDEX_CACHE: dict[Path, _CorpusIndex] = {}
_INDEX_LOCK = threading.Lock()


# ── 元数据 sidecar（v9.8 R16）───────────────────────────────────────────
# 语料里 1/3 的文献 title 字段是内部键，只能靠 sidecar 回填可读题名。
# 约定：文件缺失、格式错误、读取异常一律退化为空索引——显示层继续走兜底，
# 绝不允许因为元数据问题让原文预览打不开。
# 题名/年份/DOI 来自外部注册表反查时，这些字段优先于语料行内的值。
_EXTERNAL_TITLE_SOURCES = frozenset({"crossref_doi", "europepmc", "crossref"})
META_FILENAME = "metadata_index.json"
_META_CACHE: dict[str, tuple[float, int, dict[str, dict]]] = {}
_META_LOCK = threading.Lock()


def clear_meta_cache() -> None:
    """清空元数据缓存（测试/重建索引后手动调用）。"""
    with _META_LOCK:
        _META_CACHE.clear()


def load_metadata(path: Path | None = None) -> dict[str, dict]:
    """读取 ``<DATA_DIR>/metadata_index.json`` 的 ``index`` 段（mtime 缓存）。"""
    meta_path = path or (Path(DATA_DIR) / META_FILENAME)
    key = str(meta_path)
    try:
        stat = meta_path.stat()
    except OSError:
        return {}
    stamp = (stat.st_mtime, stat.st_size)
    with _META_LOCK:
        cached = _META_CACHE.get(key)
        if cached and (cached[0], cached[1]) == stamp:
            return cached[2]
    try:
        with meta_path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        index = payload.get("index") if isinstance(payload, dict) else None
        if not isinstance(index, dict):
            index = {}
    except (OSError, ValueError):
        index = {}
    with _META_LOCK:
        _META_CACHE[key] = (stamp[0], stamp[1], index)
    return index


def meta_for(record_key: str) -> dict:
    """单条记录的 sidecar 元数据；没有就返回空 dict。"""
    rows = load_metadata()
    entry = rows.get(str(record_key or ""))
    return dict(entry) if isinstance(entry, dict) else {}


def meta_stats() -> dict:
    """覆盖率统计（给 /health 与报告用）。"""
    rows = load_metadata()
    titled = sum(1 for v in rows.values() if str(v.get("title") or "").strip())
    return {"documents": len(rows), "titled": titled,
            "untitled": len(rows) - titled}


def _meta_url(meta: dict) -> str:
    doi = str(meta.get("doi") or "").strip()
    if doi:
        return "https://doi.org/" + doi
    return ""

def is_valid_record_key(value: object) -> bool:
    """record 键格式校验（纯正则，不做任何 IO）。"""
    return bool(RECORD_KEY_RE.fullmatch(str(value or "")))


def clear_index_cache() -> None:
    """清空进程内索引缓存（测试用；生产不需要调用）。"""
    with _INDEX_LOCK:
        _INDEX_CACHE.clear()


def _tidy(value: object) -> str:
    return " ".join(str(value or "").split())


def _corpus_path(corpus_id: str) -> Path | None:
    """语料 chunks.jsonl 路径；目录名只来自 ``CORPUS_ORDER``。"""
    if corpus_id not in CORPUS_ORDER:
        return None
    for relative in _CHUNK_RELATIVE_PATHS:
        path = Path(DATA_DIR) / corpus_id / relative
        if path.is_file():
            return path
    return None


def _build_index(path: Path, stat: Any) -> _CorpusIndex:
    """扫一次 chunks.jsonl，只记 paper_id 与行起始字节偏移。"""
    offsets: dict[str, list[int]] = {}
    rows = 0
    offset = 0
    with path.open("rb") as stream:
        for line in stream:
            length = len(line)
            if line.strip():
                rows += 1
                try:
                    row = json.loads(line)
                except ValueError:
                    row = None
                paper_id = str((row or {}).get("paper_id") or "").strip() if isinstance(row, dict) else ""
                if paper_id:
                    offsets.setdefault(paper_id, []).append(offset)
            offset += length
    return _CorpusIndex(path=path, mtime_ns=int(stat.st_mtime_ns), size=int(stat.st_size),
                        offsets=offsets, rows=rows)


def _load_index(path: Path) -> _CorpusIndex | None:
    """取缓存索引；``mtime + size`` 变化后重建。"""
    try:
        stat = path.stat()
    except OSError:
        return None
    with _INDEX_LOCK:
        cached = _INDEX_CACHE.get(path)
        if cached is not None and cached.mtime_ns == int(stat.st_mtime_ns) \
                and cached.size == int(stat.st_size):
            return cached
        index = _build_index(path, stat)
        _INDEX_CACHE[path] = index
        return index


def _index_for(corpus_id: str) -> _CorpusIndex | None:
    path = _corpus_path(corpus_id)
    if path is None:
        return None
    return _load_index(path)


def _read_rows(index: _CorpusIndex, offsets: Iterable[int]) -> list[dict[str, Any]]:
    """按字节偏移读取并解析指定行（单行 seek，不整读文件）。"""
    rows: list[dict[str, Any]] = []
    with index.path.open("rb") as stream:
        for offset in offsets:
            stream.seek(offset)
            try:
                row = json.loads(stream.readline())
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def locate_record(record_key: str) -> str | None:
    """按 ``CORPUS_ORDER`` 顺序定位 record 所在语料；找不到返回 ``None``。"""
    key = str(record_key or "")
    if not is_valid_record_key(key):
        return None
    for corpus_id in CORPUS_ORDER:
        index = _index_for(corpus_id)
        if index is not None and index.offsets.get(key):
            return corpus_id
    return None


def _chunk_order(row: dict[str, Any]) -> tuple[int, int]:
    raw = row.get("chunk_index")
    try:
        return (int(raw), 0)
    except (TypeError, ValueError):
        return (1 << 31, 0)


def _chunk_id(row: dict[str, Any], record_key: str, position: int) -> str:
    value = str(row.get("chunk_id") or "").strip()
    if value:
        return value
    return f"{record_key}::{position:04d}"


def _clean_text(value: object) -> str:
    """内部键 → 可读短语；绝对路径 → 占位符（只读展示层，不写回语料）。"""
    text = str(value or "")
    if not text:
        return ""
    text = scrub_internal_keys(text)
    return _ABS_PATH_RE.sub(_REDACTED_PATH_LABEL, text)


def _heading_fingerprint(value: object) -> str:
    """Normalize a visible heading for duplicate-title comparison only."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\W_]+", "", text)


def _strip_duplicate_title_headings(
        text: str, title_label: str, section_label: str = "") -> str:
    """Remove leading headings already represented by the payload structure."""
    heading_keys = {
        key for key in (
            _heading_fingerprint(title_label),
            _heading_fingerprint(section_label),
        ) if key
    }
    if not heading_keys:
        return text
    cleaned = text.lstrip()
    while cleaned:
        match = _MARKDOWN_HEADING_RE.match(cleaned)
        if not match or _heading_fingerprint(match.group(1)) not in heading_keys:
            break
        cleaned = cleaned[match.end():].lstrip()
    return cleaned


def _section_label(value: object) -> str:
    label = _tidy(value)
    if not label:
        return _EMPTY_SECTION_LABEL
    label = _clean_text(label)
    return label or _EMPTY_SECTION_LABEL


def _title_label(rows: list[dict[str, Any]]) -> str:
    """题名标签：首个分块的 section_name 若像真题名就用它，否则复用 display_label。

    语料构建时很多文献把文章标题写进了 ``section_name``（首块即标题），
    而 ``Abstract``/``Introduction`` 这类通用章节名不是题名，遇到就退回
    ``record_label.display_label``（“未具题名文献（期刊 · 年份 · 第 N 篇）”）。
    """
    first = rows[0] if rows else {}
    section = _tidy(first.get("section_name") or first.get("section"))
    if (section and not is_internal_record_key(section)
            and section.casefold() not in _GENERIC_SECTIONS):
        return _clean_text(section)[:200]

    label = _tidy(display_label(first))
    if not label or is_internal_record_key(label):
        label = "未具题名文献"
    return _clean_text(label)[:200]


def _safe_source_file(value: object) -> str:
    """source_file 只作元数据：绝对路径/带 ``..`` 的一律不返回。"""
    text = str(value or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return ""
    if ".." in text.split("/"):
        return ""
    return text


def _first_value(rows: Iterable[dict[str, Any]], *fields: str) -> str:
    for row in rows:
        for field in fields:
            value = str(row.get(field) or "").strip()
            if value:
                return value
    return ""


def build_payload(corpus_id: str, record_key: str) -> dict[str, Any] | None:
    """组装成功响应体；键不存在或分块读取失败时返回 ``None``。"""
    index = _index_for(corpus_id)
    if index is None:
        return None
    offsets = index.offsets.get(record_key)
    if not offsets:
        return None
    rows = _read_rows(index, offsets)
    if not rows:
        return None
    rows.sort(key=_chunk_order)

    # 同一 chunk_index 的重复行只保留第一次出现（语料里偶见重复行）。
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        marker = str(row.get("chunk_index"))
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(row)
    rows = unique

    first = rows[0]
    title_label = _title_label(rows)
    meta = meta_for(record_key)
    if str(meta.get("title") or "").strip():
        title_label = _clean_text(meta["title"])[:200]
    title_key = _heading_fingerprint(title_label)

    sections: list[dict[str, Any]] = []
    total_chars = 0
    for position, row in enumerate(rows):
        raw_text = str(row.get("text") or "")
        total_chars += len(raw_text)
        name = _section_label(row.get("section_name") or row.get("section"))
        text = _clean_text(raw_text)
        text = _strip_duplicate_title_headings(text, title_label, name)
        chunk_id = _chunk_id(row, record_key, position)
        # 顺序遍历保证 sections[-1] 就是紧邻的前一个分块：同名即合并相邻同名章节。
        if sections and sections[-1]["name"] == name:
            sections[-1]["text"] = f"{sections[-1]['text']}\n\n{text}"
            sections[-1]["chunk_ids"].append(chunk_id)
        else:
            sections.append({"name": name, "text": text, "chunk_ids": [chunk_id]})

    info = (parse_record_key(first.get("title"))
            or parse_record_key(record_key)
            or parse_record_key(first.get("paper_id")))
    journal = (str(meta.get("journal") or "")
               or (str(info["journal_text"] or "") if info else ""))
    # 外部反查回来的元数据（Crossref / Europe PMC）比语料行更可信：清洗后的
    # 行里 year/doi 常为空或错位（例如行内 2008、真实发表年 2015），所以一旦
    # sidecar 标了外部来源，就以 sidecar 为准，行内值只作兜底。
    _external = str(meta.get("title_source") or "") in _EXTERNAL_TITLE_SOURCES
    _row_year = _first_value(rows, "year")
    _row_doi = _first_value(rows, "doi")
    _meta_year = str(meta.get("year") or "")
    _meta_doi = str(meta.get("doi") or "")
    _info_year = (str(info["year"] or "") if info else "")
    if _external:
        year = _meta_year or _row_year or _info_year
        doi = _meta_doi or _row_doi
    else:
        year = _row_year or _meta_year or _info_year
        doi = _row_doi or _meta_doi

    lines = [f"# {title_label}", ""]
    for section in sections:
        # Many source corpora store the article title as the first section
        # name. The payload already emits it as H1, so repeating it as H2
        # creates a second large title above the abstract.
        if _heading_fingerprint(section["name"]) != title_key:
            lines.append(f"## {section['name']}")
            lines.append("")
        if section["text"]:
            lines.append(section["text"])
            lines.append("")
    markdown = "\n".join(lines).rstrip()

    return {
        "ok": True,
        "record_key": record_key,
        "corpus": corpus_id,
        "source_type": _first_value(rows, "source_type") or DEFAULT_SOURCE_TYPE.get(corpus_id, ""),
        "title_label": title_label,
        "year": year,
        "doi": doi,
        "journal": journal,
        "has_doi": bool(doi),
        "chunks": len(rows),
        "chars": total_chars,
        "sections": sections,
        "markdown": markdown,
        "source_file": _safe_source_file(first.get("source_file")),
    }


def describe(record_key: str) -> dict[str, Any] | None:
    """轻量元数据：只读该记录首个分块，不拼全文。

    供「本轮本地来源」列表使用——列 8 条候选时不能把 8 篇全文都读出来。
    返回字段刻意小于 ``build_payload``：没有 sections/markdown/chars。
    """
    key = str(record_key or "")
    if not is_valid_record_key(key):
        return None
    corpus_id = locate_record(key)
    if corpus_id is None:
        return None
    index = _index_for(corpus_id)
    offsets = index.offsets.get(key) if index is not None else None
    if not offsets:
        return None
    rows = _read_rows(index, [offsets[0]])
    if not rows:
        return None
    first = rows[0]
    info = (parse_record_key(first.get("title"))
            or parse_record_key(key)
            or parse_record_key(first.get("paper_id")))
    journal = str(info["journal_text"] or "") if info else ""
    year = (str(first.get("year") or "").strip()
            or (str(info["year"] or "") if info else ""))
    meta = meta_for(key)
    _external = str(meta.get("title_source") or "") in _EXTERNAL_TITLE_SOURCES
    _row_doi = str(first.get("doi") or "").strip()
    _meta_doi = str(meta.get("doi") or "").strip()
    if _external:
        doi = _meta_doi or _row_doi
        year = str(meta.get("year") or "").strip() or year
    else:
        doi = _row_doi or _meta_doi
        year = year or str(meta.get("year") or "")
    title = str(meta.get("title") or "").strip()
    journal = journal or str(meta.get("journal") or "")
    return {
        "record_key": key,
        "corpus": corpus_id,
        "source_type": (str(first.get("source_type") or "").strip()
                        or str(meta.get("source_type") or "")
                        or DEFAULT_SOURCE_TYPE.get(corpus_id, "")),
        "title_label": _clean_text(title)[:200] if title else _title_label(rows),
        "year": year,
        "journal": journal,
        "has_doi": bool(doi),
        "doi": doi,
        "url": _meta_url({"doi": doi}),
        "variety": str(meta.get("variety") or ""),
        "registry": str(meta.get("registry") or ""),
        "species": str(meta.get("species") or ""),
        "n_chunks": len(offsets),
    }

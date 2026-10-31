# -*- coding: utf-8 -*-
"""本轮「来源」解析（v9.8, R16）。

为什么是"按轮次回查"而不是"从回答里识别编号"：
回答正文与工具摘要都经过 ``scrub_internal_keys`` 清洗，模型看不到 ``paper_...``
内部键；同一份工具原始输出仍完整留在 ``tool_results.result_text`` 里。于是
前端按 ``request_id`` 回查这一轮的原始输出，自己解析出"本地文献"和"联网结果"。

两类来源的形状（解析自真实工具输出）::

    [1] 517_paper_65bfd947_000517_F_2014_BMC_microbiology
    Corpus: literature; source_type: literature; ... ; record_id: paper_...; RRF: ...
    <untrusted_local_evidence>
    Background: Huanglongbing (HLB) or citrus greening ...
    </untrusted_local_evidence>

    [1] 标题文本
    URL: https://example.org/paper
    Date: 2024; authority: 2
    Summary: 摘要片段……

要点：
* 本地来源只回 ``record_key`` 与可读标签，键只作请求参数存在；
* 联网来源回 ``url``，前端直接在新标签页打开（不经过本地语料）；
* ``snippet`` 是命中片段的前若干字符，供原文预览高亮，不参与模型上下文。
"""
from __future__ import annotations

import re
from typing import Any

from src.core import corpus_source, tool_results

__all__ = ["parse_sources", "sources_for_request", "MAX_SOURCES",
           "MAX_LOCAL", "MAX_WEB"]

MAX_SOURCES = 12
SNIPPET_CHARS = 220

_ENTRY = re.compile(r"^\s*\[(\d{1,2})\]\s*(.+)$")
_RECORD_KEY = re.compile(r"(?:paper|doc)_[A-Za-z0-9][A-Za-z0-9_.:-]{6,180}")
_RECORD_ID = re.compile(r"record_id:\s*((?:paper|doc)_[A-Za-z0-9][A-Za-z0-9_.:-]{6,180})")
_URL = re.compile(r"^\s*URL:\s*(\S+)", re.M)
_SUMMARY = re.compile(r"^\s*Summary:\s*(.+)$", re.M)
_EVIDENCE = re.compile(r"<untrusted_local_evidence>(.*?)</untrusted_local_evidence>", re.S)
_TAG = re.compile(r"<[^>]{1,80}>")
_TRAILING = "._:-"


def _split_entries(text: str) -> list[tuple[int, str, str]]:
    """把工具输出切成 ``(n, 标题行, 整段)``。"""
    out: list[tuple[int, str, str]] = []
    current_n: int | None = None
    current_head = ""
    buf: list[str] = []
    for line in str(text or "").splitlines():
        match = _ENTRY.match(line)
        if match:
            if current_n is not None:
                out.append((current_n, current_head, "\n".join(buf)))
            current_n = int(match.group(1))
            current_head = match.group(2).strip()
            buf = [line]
            continue
        if current_n is not None:
            buf.append(line)
    if current_n is not None:
        out.append((current_n, current_head, "\n".join(buf)))
    return out


def _clean_snippet(value: str) -> str:
    text = " ".join(_TAG.sub(" ", str(value or "")).replace("...", " ").split())
    return text[:SNIPPET_CHARS]


def _local_key(block: str, head: str) -> str:
    match = _RECORD_ID.search(block)
    if match:
        return match.group(1).strip(_TRAILING)
    found = _RECORD_KEY.findall(head)
    return found[0].strip(_TRAILING) if found else ""


def parse_sources(text: str, limit: int = MAX_SOURCES) -> list[dict[str, Any]]:
    """解析一轮工具原始输出里的本地/联网来源（保序、去重、限量）。"""
    cap = max(1, min(int(limit or MAX_SOURCES), MAX_SOURCES))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for number, head, block in _split_entries(text):
        url_match = _URL.search(block)
        if url_match:
            url = url_match.group(1).strip()
            if not url or url in seen:
                continue
            summary = _SUMMARY.search(block)
            title = _clean_snippet(_TAG.sub(" ", head)) or url
            item = {
                "kind": "web",
                "n": number,
                "title": title[:180],
                "url": url,
                "snippet": _clean_snippet(summary.group(1) if summary else ""),
            }
            seen.add(url)
            out.append(item)
            if len(out) >= cap:
                break
            continue

        evidence = _EVIDENCE.search(block)
        key = _local_key(block, head)
        if not key and not evidence:
            continue
        if key:
            if key in seen:
                continue
            seen.add(key)
        item = {
            "kind": "local",
            "n": number,
            "record_key": key,
            "title": "",
            "snippet": _clean_snippet(evidence.group(1) if evidence else head),
        }
        out.append(item)
        if len(out) >= cap:
            break
    return out


MAX_LOCAL = 8
MAX_WEB = 6


def sources_for_request(session_id: str, request_id: str,
                        limit: int = MAX_SOURCES) -> list[dict[str, Any]]:
    """本轮可点开的来源：本地优先（≤8），联网补位（≤6），总数 ≤ limit。"""
    cap = max(1, min(int(limit or MAX_SOURCES), MAX_SOURCES))
    rows = tool_results.fetch_by_request(session_id, request_id)
    if not rows:
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        for item in parse_sources(row.get("result_text") or "", limit=MAX_SOURCES):
            marker = item.get("record_key") or item.get("url") or ""
            if not marker or marker in seen:
                continue
            if item.get("kind") == "local":
                meta = corpus_source.describe(item.get("record_key") or "")
                if not meta:
                    continue
                merged = dict(item)
                merged.update({
                    "title": meta.get("title_label") or "",
                    "year": meta.get("year") or "",
                    "journal": meta.get("journal") or "",
                    "variety": meta.get("variety") or "",
                    "registry": meta.get("registry") or "",
                    "species": meta.get("species") or "",
                    "doi": meta.get("doi") or "",
                    "url": meta.get("url") or "",
                    "corpus": meta.get("corpus") or "",
                })
                item = merged
            seen.add(marker)
            out.append(item)

    locals_ = [i for i in out if i.get("kind") == "local"][:MAX_LOCAL]
    webs_ = [i for i in out if i.get("kind") == "web"][:MAX_WEB]
    merged = (locals_ + webs_)[:cap]
    for item in merged:
        item.pop("kind_local", None)
    return merged

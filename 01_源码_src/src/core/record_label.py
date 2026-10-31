"""内部记录键 → 人类可读标签（v9.7）。

语料构建期，一批没有题名的文献被写成了文件名式内部键，例如
``593_paper_1c63bb9b_000593_F_2018_BMC_Plant_Biolog``。检索工具与历史证据摘要
若原样展示，模型会把它当成文献名抄进回答——用户看到的就是无法阅读的编号。

本模块是唯一转换点：

- ``is_internal_record_key`` / ``parse_record_key``：识别并拆解内部键；
- ``display_label``：给检索结果行生成可读标签（题名缺失时用 年+期刊+序号，
  并**明确标注题名缺失**，不伪造题名）；
- ``scrub_internal_keys``：兜底清洗回答文本里漏出的内部键。

不重建语料、不改索引：只在展示层把内部键换成可读信息。
"""
from __future__ import annotations

import re

# paper_1c63bb9b_000593_F_2018_BMC_Plant_Biolog
# 允许前缀 `593_`（语料 title 字段的形式）与结尾多余下划线（模型复制时常见）。
_KEY_BODY = (r"paper_[0-9a-f]{6,}"
             r"(?:_\d{1,7}_[A-Z])?"
             r"_\d{4}_[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)*")
_KEY_PREFIX = r"(?:\d{1,4}_)?"

_INTERNAL_KEY_RE = re.compile(r"(?<![0-9A-Za-z_])" + _KEY_PREFIX + _KEY_BODY + r"_*")
_PARSE_RE = re.compile(
    r"^(?:\d{1,4}_)?"
    r"paper_(?P<hash>[0-9a-f]{6,})"
    r"(?:_(?P<seq>\d{1,7})_(?P<letter>[A-Z]))?"
    r"_(?P<year>\d{4})"
    r"_(?P<journal>.+?)"
    r"_*$"
)

# 这些 section 名不是标题，命中时不能当题名用。
_GENERIC_SECTIONS = {
    "abstract", "introduction", "results", "discussion", "methods",
    "materials and methods", "conclusion", "conclusions", "references",
    "acknowledgements", "acknowledgments", "supplementary", "__preamble__",
    "experimental procedures", "experimental section", "declarations",
}


def _tidy(value) -> str:
    return " ".join(str(value or "").split())


def is_internal_record_key(text) -> bool:
    """``text`` 是否整体就是一个内部记录键（允许 ``593_`` 前缀）。"""
    value = _tidy(text)
    if not value or "paper_" not in value:
        return False
    return bool(re.fullmatch(_KEY_PREFIX + _KEY_BODY + r"_*", value))


def parse_record_key(value) -> dict | None:
    """拆解内部键；不是内部键时返回 ``None``。

    返回 ``{"key", "hash", "doc_no", "year", "journal", "journal_text"}``。
    ``journal`` 是原文 slug（可能被截断），``journal_text`` 是可展示形式。
    """
    text = _tidy(value)
    if not text:
        return None
    match = _PARSE_RE.match(text)
    if not match:
        return None
    raw_journal = (match.group("journal") or "").strip("_-")
    # 期刊 slug 在建语料时被截断到约 20 字符，结尾常是半个单词；加省略号更诚实。
    journal_text = raw_journal.replace("_", " ").replace("-", " ").strip()
    # 语料把来源文件名截断到固定长度，末词常只剩 1-2 个字母（如 "DNA research an"），
    # 这种碎片不是期刊名的一部分，去掉比留着更像错字要诚实。
    words = journal_text.split()
    if len(words) > 1 and len(words[-1]) <= 2:
        journal_text = " ".join(words[:-1])
    doc_no = ""
    leading = re.match(r"^(\d{1,4})_", text)
    if leading:
        doc_no = str(int(leading.group(1)))
    elif match.group("seq"):
        doc_no = str(int(match.group("seq")))
    return {
        "key": text,
        "hash": match.group("hash") or "",
        "doc_no": doc_no,
        "year": match.group("year") or "",
        "journal": raw_journal,
        "journal_text": journal_text,
    }


def _fragment_suffix(row: dict) -> str:
    """片段定位：真实 section 名 + 块号。

    ``Abstract`` 这类通用 section 不能当题名，但作为“同一文献的第几个片段”的
    标签是真实且有区分度的——同一论文的两个块因此不会被合并或显示成重复行。
    """
    section = _tidy(row.get("section_name") or row.get("section"))
    if section and is_internal_record_key(section):
        section = ""
    chunk_index = row.get("chunk_index")
    piece = ""
    if chunk_index is not None and str(chunk_index).strip() != "":
        try:
            piece = f"#{int(chunk_index)}"
        except (TypeError, ValueError):
            piece = ""
    parts = [part for part in (section, piece) if part]
    return " · ".join(parts)


def display_label(row: dict, *, with_section: bool = False) -> str:
    """给一条检索结果生成可读标签。

    优先真实题名；题名缺失或是内部键时，用 ``未具题名文献（期刊 · 年份 · 第 N 篇）``，
    并可选追加真实 section 名以区分同一文献的不同片段（这些是真实执行记录，不合并掉）。
    """
    if not isinstance(row, dict):
        return "未具题名文献"

    title = _tidy(row.get("title") or row.get("paper_title") or row.get("document_title"))
    if title and not is_internal_record_key(title) and title.lower() not in _GENERIC_SECTIONS:
        label = title
    else:
        info = (parse_record_key(title) or parse_record_key(row.get("paper_id"))
                or parse_record_key(row.get("chunk_id")) or parse_record_key(row.get("source_file")))
        bits = []
        if info:
            if info["journal_text"]:
                bits.append(info["journal_text"])
            if info["year"]:
                bits.append(info["year"])
            if info["doc_no"]:
                bits.append(f"第 {info['doc_no']} 篇")
        label = "未具题名文献" + (f"（{' · '.join(bits)}）" if bits else "")

    if with_section:
        suffix = _fragment_suffix(row)
        if suffix:
            label = f"{label} › {suffix}"
    return label[:200]


def _inline_phrase(info: dict | None) -> str:
    if not info:
        return "一篇未具题名的文献"
    bits = []
    if info["year"]:
        bits.append(f"{info['year']} 年")
    if info["journal_text"]:
        bits.append(info["journal_text"])
    if bits:
        return f"一篇 {' '.join(bits)} 文献"
    return "一篇未具题名的文献"


def scrub_internal_keys(text: str) -> str:
    """把文本里残留的内部键换成可读短语（幂等）。"""
    if not text or "paper_" not in str(text):
        return text
    source = str(text)
    out = _INTERNAL_KEY_RE.sub(lambda m: _inline_phrase(parse_record_key(m.group(0))), source)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"[ \t]+([，。；、）])", r"\1", out)
    return out

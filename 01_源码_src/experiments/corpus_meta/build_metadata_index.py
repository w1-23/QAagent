# -*- coding: utf-8 -*-
"""构建语料元数据索引（v9.8, R16）——只改元数据，不重嵌入。

背景：``cqa_literature`` 里有相当一部分记录的 ``title`` 字段是内部键
（``593_paper_1c63bb9b_...``），``year``/``doi`` 也是空的。回答里因此只能
退化成「未具题名文献（BMC microbiology · 2014 · 第 517 篇）」。

本脚本不回写原始语料、不重建向量，只产出一个 sidecar：
``<data_dir>/metadata_index.json``，由 ``src/core/corpus_source.py`` 读取，
把「可读题名 / 年份 / 期刊 / DOI / 品种 / registry / species / 源文件」
一次性补齐，从而把显示层的兜底压到真正缺失的那些记录上。

题名来源优先级（逐条记录）：
  1. 首块（chunk_index=0）的 ``section_name``，若它不像章节名（Abstract…）
  2. 首块正文里第一条 Markdown 标题（``# xxx``）
  3. 首块正文首个「像标题」的行（长度 25..240、非句末标点、非邮箱/编辑署名）
  4. 语料自带 ``title``，若它本身不是内部键
  5. 留空——显示层继续用「未具题名文献（期刊 · 年份）」兜底

年份/期刊：字段为空时从 ``paper_id`` 解析（``..._F_2014_BMC_microbiology_``）。

用法：
    python build_metadata_index.py [--data-dir PATH] [--out PATH]

产物结构（version 1）::

    {"version": 1, "generated_at": "...", "source_data_dir": "...",
     "corpora": {"cqa_literature": {"documents": 3171, "titled": 1977, ...}},
     "index": {"paper_d3855c0c_000001_A_2018_Nature_Genomics_": {
         "corpus": "cqa_literature", "title": "...", "year": "2018",
         "journal": "Nature Genomics", "doi": "", "variety": "",
         "registry": "", "species": "", "source_file": "...",
         "title_source": "section"}}}

只读语料，幂等可重跑；耗时约 20–40 秒（10 万行流式）。"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import time

CORPORA = ("cqa_literature", "cqa_methods", "cqa_deng_book",
           "cqa_translated", "cqa_ucr_varieties")
DEFAULT_DATA_DIR = "/home/WZQ/CitrusQA/agent/data"

SECTION_WORDS = {
    "abstract", "abstracts", "introduction", "artigos", "resumo", "summary",
    "background", "keywords", "graphical abstract", "resumen", "antetitulo",
    "摘要", "引言", "前言", "结论",
}
_KEY_RE = re.compile(r"^(?:\d+_)?(?:paper|doc)_")
_KEY_META = re.compile(r"_(?P<year>(?:19|20)\d{2})_(?P<journal>[A-Za-z][A-Za-z0-9_\-]{2,})$")
_HEAD_RE = re.compile(r"^\s{0,3}#{1,3}\s+(.{10,240})$")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_JUNK_PREFIX = (
    "academic editor", "academic editors", "corresponding author", "editor:",
    "subjects ", "discipline:", "preprint submitted", "autor para",
    "received", "accepted", "published", "doi:", "http", "keywords",
    "citation:", "copyright", "license", "@",
)


def is_internal_key(value: str) -> bool:
    return bool(value) and bool(_KEY_RE.match(str(value).strip()))


def parse_key_meta(paper_id: str) -> dict:
    """从 ``paper_id`` 末尾解析年份与期刊（字段缺失时用）。"""
    out = {"year": "", "journal": ""}
    if not paper_id:
        return out
    m = _KEY_META.search(paper_id.rstrip("_"))
    if not m:
        return out
    out["year"] = m.group("year")
    out["journal"] = m.group("journal").replace("_", " ").strip(" _-")
    return out


def title_from_section(name: str) -> str:
    s = (name or "").strip()
    if not s or s.lower() in SECTION_WORDS or len(s) < 12:
        return ""
    if _EMAIL_RE.search(s):
        return ""
    if s.lower().startswith(_JUNK_PREFIX):
        return ""
    return s


def title_from_text(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines()]
    for ln in lines[:12]:
        m = _HEAD_RE.match(ln)
        if m:
            cand = m.group(1).strip()
            if _ok_title(cand):
                return cand
    for ln in lines[:4]:
        if _ok_title(ln):
            return ln
    return ""


def _ok_title(s: str) -> bool:
    if not (25 <= len(s) <= 240):
        return False
    if s.endswith((".", "。", ",", "，", ";", "：")):
        return False
    low = s.lower()
    if low.startswith(_JUNK_PREFIX) or _EMAIL_RE.search(s):
        return False
    return sum(ch.isalpha() for ch in s) >= 15


def first_rows(chunks_path: str) -> dict:
    """只取每个文档的 chunk_index=0 行（内存占用与文档数同阶）。"""
    firsts: dict[str, dict] = {}
    with open(chunks_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"chunk_index"' not in line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if str(row.get("chunk_index")) != "0":
                continue
            pid = str(row.get("paper_id") or "")
            if pid and pid not in firsts:
                firsts[pid] = row
    return firsts


# 这些 title_source 表示题名来自外部注册表反查（`recover_titles.py`），不是从
# 语料里推导出来的，重建索引时必须保留。
EXTERNAL_TITLE_SOURCES = frozenset({"crossref_doi", "europepmc", "crossref"})

def build_corpus(data_dir: str, corpus: str) -> tuple[dict, dict]:
    chunks = os.path.join(data_dir, corpus, "chunks.jsonl")
    if not os.path.exists(chunks):
        return {}, {"corpus": corpus, "documents": 0, "skipped": "missing chunks.jsonl"}
    t0 = time.time()
    rows = first_rows(chunks)
    entries: dict[str, dict] = {}
    stats = {"corpus": corpus, "documents": len(rows), "titled": 0,
             "from_section": 0, "from_heading": 0, "from_title_field": 0,
             "untitled": 0}
    for pid, row in rows.items():
        title = title_from_section(row.get("section_name") or "")
        source = "section" if title else ""
        if not title:
            title = title_from_text(row.get("text") or "")
            source = "heading" if title else ""
        raw_title = str(row.get("title") or "").strip()
        if not title and raw_title and not is_internal_key(raw_title):
            title = raw_title
            source = "title_field"
        key_meta = parse_key_meta(pid)
        entry = {
            "corpus": corpus,
            "title": title,
            "year": str(row.get("year") or "").strip() or key_meta["year"],
            "journal": key_meta["journal"],
            "doi": str(row.get("doi") or "").strip(),
            "variety": str(row.get("species") or "").strip(),
            "registry": str(row.get("registry_id") or "").strip(),
            "species": str(row.get("species") or "").strip(),
            "source_file": str(row.get("source_file") or "").strip(),
            "source_type": str(row.get("source_type") or "").strip(),
            "title_source": source,
        }
        entries[pid] = entry
        if title:
            stats["titled"] += 1
            stats[{"section": "from_section", "heading": "from_heading",
                   "title_field": "from_title_field"}.get(source, "from_section")] += 1
        else:
            stats["untitled"] += 1
    stats["seconds"] = round(time.time() - t0, 1)
    return entries, stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="构建语料元数据索引（只改元数据）")
    ap.add_argument("--data-dir", default=os.environ.get("CITRUS_DATA_DIR", DEFAULT_DATA_DIR))
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    out_path = args.out or os.path.join(args.data_dir, "metadata_index.json")
    index: dict[str, dict] = {}
    stats: dict[str, dict] = {}
    for corpus in CORPORA:
        entries, st = build_corpus(args.data_dir, corpus)
        index.update(entries)
        stats[corpus] = st
        print(f"[{corpus}] docs={st.get('documents')} titled={st.get('titled')} "
              f"untitled={st.get('untitled')} {st.get('seconds')}s", flush=True)
    # 外部反查回来的题名（Crossref / Europe PMC）不是本地可推导的事实，重建时
    # 必须原样保留，否则每次重跑 sidecar 都会把 1000 多条题名重新丢回兜底。
    carried = 0
    prev_index: dict = {}
    if os.path.exists(out_path):
        try:
            with open(out_path, encoding="utf-8") as fh:
                prev_index = (json.load(fh) or {}).get("index") or {}
        except (OSError, ValueError):
            prev_index = {}
    for key, entry in index.items():
        old = prev_index.get(key) or {}
        if str(old.get("title_source") or "") not in EXTERNAL_TITLE_SOURCES:
            continue
        title = str(old.get("title") or "").strip()
        if not title:
            continue
        entry.update({
            "title": title,
            "year": str(old.get("year") or entry.get("year") or ""),
            "journal": str(old.get("journal") or entry.get("journal") or ""),
            "doi": str(old.get("doi") or entry.get("doi") or ""),
            "title_source": old.get("title_source"),
            "title_confidence": old.get("title_confidence"),
            "title_evidence": old.get("title_evidence") or "",
        })
        if not str(entry.get("doi") or "").strip() and str(old.get("doi") or "").strip():
            entry["doi"] = str(old.get("doi"))
        carried += 1
    payload = {
        "version": 2,
        "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_data_dir": args.data_dir,
        "corpora": stats,
        "carried_external_titles": carried,
        "index": index,
    }
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    os.replace(tmp, out_path)
    total = sum(v.get("documents", 0) for v in stats.values())
    titled = sum(v.get("titled", 0) for v in stats.values())
    print(f"wrote {out_path} ({os.path.getsize(out_path) / 1048576:.2f} MiB) "
          f"documents={total} titled={titled} ({titled / max(1, total):.1%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

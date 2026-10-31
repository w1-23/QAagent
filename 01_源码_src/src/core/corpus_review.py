# -*- coding: utf-8 -*-
"""文献候审库：只保存原始资料，不切块、不向量化、不建索引、不激活语料。

职责边界（刻意保持很小）：
1. 抓取成功的论文正文 → 落一份**原始格式**的 ``.md``（HTML 先做确定性转换）＋ ``meta.json``。
2. 用户/管理员上传的 PDF → 落**原始字节** ``.pdf`` ＋ ``meta.json``（不拿提取文本冒充原件）。
3. 记录 DOI / 原 URL / 抓取时间 / SHA-256 / 原始格式 / 转换方式，供人工核验。
4. 管理员只做「查看原文、下载原文、批准保留、拒绝」，不提供入库、激活、索引回滚。

**不做的事**（重要）：
- 不调用 ``ingest.py``，不生成 generation，不切块，不碰 ``active_index.json``；
- 不保存竞争失败或被丢弃的重复全文；
- 不保存模型侧已被 40k 截断的 ToolMessage 文本（调用方必须在裁剪前调用）；
- 不把普通用户上传的私有 PDF 自动变成管理员可读资料（需显式授权或管理员本人上传）；
- 任何捕获失败都只记日志，绝不影响问答主流程。
"""

from __future__ import annotations

import hashlib
import html as html_module
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

QUEUE_DIRNAME = "_review_queue"
MIN_CONTENT_CHARS = 2000
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
ALLOWED_STATUS = ("pending", "approved", "rejected", "ingested")

_schema_ready = False


# --------------------------------------------------------------------------- 路径

def _state_dir() -> Path:
    from src.session.manager import DB_PATH
    return Path(DB_PATH).resolve().parent


def _db_path() -> Path:
    return _state_dir() / "corpus_review.sqlite3"


def _data_dir() -> Path:
    from src.retrieval.corpus_admin import DATA_DIR
    return Path(DATA_DIR).resolve()


def _queue_root() -> Path:
    root = _data_dir() / QUEUE_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _archive_root() -> Path:
    root = _queue_root() / "archive"
    root.mkdir(parents=True, exist_ok=True)
    return root


# --------------------------------------------------------------------------- 基础

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


_EXTRA_COLUMNS = {
    "raw_format": "TEXT NOT NULL DEFAULT ''",
    "conversion": "TEXT NOT NULL DEFAULT ''",
    "raw_sha256": "TEXT NOT NULL DEFAULT ''",
    "raw_bytes": "INTEGER NOT NULL DEFAULT 0",
    "stored_path": "TEXT NOT NULL DEFAULT ''",
    "fetched_at": "TEXT NOT NULL DEFAULT ''",
    "archive_state": "TEXT NOT NULL DEFAULT 'archived'",
    "origin": "TEXT NOT NULL DEFAULT ''",
}


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    _db_path().parent.mkdir(parents=True, exist_ok=True)
    conn = _connect()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS corpus_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                doi TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                journal TEXT NOT NULL DEFAULT '',
                year TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                source_kind TEXT NOT NULL DEFAULT '',
                providers TEXT NOT NULL DEFAULT '[]',
                char_count INTEGER NOT NULL DEFAULT 0,
                content_sha256 TEXT NOT NULL DEFAULT '',
                fulltext_path TEXT NOT NULL DEFAULT '',
                frequency INTEGER NOT NULL DEFAULT 0,
                last_seen_at TEXT NOT NULL DEFAULT '',
                requested_by INTEGER NOT NULL DEFAULT 0,
                session_id TEXT NOT NULL DEFAULT '',
                request_id TEXT NOT NULL DEFAULT '',
                decided_by INTEGER NOT NULL DEFAULT 0,
                decided_at TEXT NOT NULL DEFAULT '',
                batch_id TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT ''
            )
        """)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(corpus_candidates)")}
        for name, ddl in _EXTRA_COLUMNS.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE corpus_candidates ADD COLUMN {name} {ddl}")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_cand_status ON corpus_candidates(status, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_cand_doi ON corpus_candidates(doi)")
        # 旧版本遗留的批次表保留但不写入，避免历史证据丢失。
        conn.execute("""
            CREATE TABLE IF NOT EXISTS corpus_batches (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                generation TEXT NOT NULL DEFAULT '',
                prev_generation TEXT NOT NULL DEFAULT '',
                corpus_ids TEXT NOT NULL DEFAULT '[]',
                candidate_ids TEXT NOT NULL DEFAULT '[]',
                chunk_count INTEGER NOT NULL DEFAULT 0,
                note TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.commit()
    finally:
        conn.close()
    _schema_ready = True


# --------------------------------------------------------------------------- 格式判定与转换

_TAG_RE = re.compile(r"</?(?:p|div|section|article|h[1-6]|ul|ol|li|table|tr|td|th|br)\b", re.I)


def sniff_format(content: str, source_kind: str = "") -> str:
    """判断抓回来的正文到底是什么格式：``html`` / ``markdown`` / ``plain_text``。

    只做保守判定：宁可判成 plain_text 也不要把纯文本冒充 Markdown。
    """
    text = str(content or "")
    if not text.strip():
        return "plain_text"
    head = text.lstrip()[:4000]
    if head[:1] == "<" or len(_TAG_RE.findall(head)) >= 3:
        return "html"
    markdown_markers = 0
    if re.search(r"(?m)^#{1,6}\s+\S", text):
        markdown_markers += 1
    if re.search(r"\[[^\]\n]{1,120}\]\((?:https?|mailto):[^)\s]+\)", text):
        markdown_markers += 1
    if re.search(r"(?m)^\s*(?:[-*+]|\d+\.)\s+\S", text):
        markdown_markers += 1
    if re.search(r"\*\*[^*\n]{1,120}\*\*", text):
        markdown_markers += 1
    if re.search(r"(?m)^\s*\|.+\|\s*$", text):
        markdown_markers += 1
    return "markdown" if markdown_markers >= 2 else "plain_text"


_BLOCK_DROP_RE = re.compile(
    r"<(script|style|noscript|svg|nav|footer|form|iframe)\b[^>]*>.*?</\1>", re.I | re.S)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\u00a0\u3000]+")


def _entity(text: str) -> str:
    return html_module.unescape(text)


def _inline_markdown(fragment: str) -> str:
    """行内元素（链接/强调）转换，保持可读与可追溯。"""
    text = fragment

    def _link(match: re.Match) -> str:
        href = match.group(1).strip()
        label = _TAG_STRIP_RE.sub("", match.group(2)).strip() or href
        return f"[{_entity(_WS_RE.sub(' ', label))}]({href})"

    text = re.sub(r"<a\b[^>]*?href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
                  _link, text, flags=re.I | re.S)
    text = re.sub(r"<(?:strong|b)\b[^>]*>(.*?)</(?:strong|b)>",
                  lambda m: f"**{m.group(1)}**", text, flags=re.I | re.S)
    text = re.sub(r"<(?:em|i)\b[^>]*>(.*?)</(?:em|i)>",
                  lambda m: f"*{m.group(1)}*", text, flags=re.I | re.S)
    text = re.sub(r"<code\b[^>]*>(.*?)</code>",
                  lambda m: f"`{m.group(1)}`", text, flags=re.I | re.S)
    text = _TAG_STRIP_RE.sub("", text)
    return _WS_RE.sub(" ", _entity(text)).strip()


def html_to_markdown(source: str) -> str:
    """确定性的 HTML → Markdown：保留标题、段落、列表、表格、链接与 DOI 等文字。

    不生成原文没有的标题，不删链接文字；表格按单元格导出为管道表，
    无法识别的标签一律退化为文本，绝不静默丢内容。
    """
    text = str(source or "")
    text = _BLOCK_DROP_RE.sub(" ", text)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)

    # 表格：先整块转成管道表，避免被后续段落替换拆散
    def _table(match: re.Match) -> str:
        rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", match.group(1), flags=re.I | re.S)
        if not rows:
            return " "
        out: list[str] = []
        width = 0
        parsed: list[list[str]] = []
        for row in rows:
            cells = re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", row, flags=re.I | re.S)
            cells = [_inline_markdown(cell).replace("|", "\\|") for cell in cells]
            if not cells:
                continue
            parsed.append(cells)
            width = max(width, len(cells))
        if not parsed:
            return " "
        for index, cells in enumerate(parsed):
            padded = cells + [""] * (width - len(cells))
            out.append("| " + " | ".join(padded) + " |")
            if index == 0:
                out.append("| " + " | ".join(["---"] * width) + " |")
        return "\n\n" + "\n".join(out) + "\n\n"

    text = re.sub(r"<table\b[^>]*>(.*?)</table>", _table, text, flags=re.I | re.S)

    for level in range(1, 7):
        text = re.sub(rf"<h{level}\b[^>]*>(.*?)</h{level}>",
                      lambda m, lv=level: "\n\n" + "#" * lv + " " + _inline_markdown(m.group(1)) + "\n\n",
                      text, flags=re.I | re.S)
    text = re.sub(r"<blockquote\b[^>]*>(.*?)</blockquote>",
                  lambda m: "\n\n" + "\n".join("> " + line for line in
                                               _inline_markdown(m.group(1)).splitlines() or [""]) + "\n\n",
                  text, flags=re.I | re.S)
    text = re.sub(r"<li\b[^>]*>(.*?)</li>",
                  lambda m: "\n- " + _inline_markdown(m.group(1)), text, flags=re.I | re.S)
    text = re.sub(r"</?(?:ul|ol)\b[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<hr\s*/?>", "\n\n---\n\n", text, flags=re.I)
    text = re.sub(r"</(?:p|div|section|article|main|header|figure|figcaption|td|th|tr)>", "\n\n", text, flags=re.I)
    # 剩余的行内元素（段落里的链接、加粗、行内代码）统一走行内转换，
    # 保证 <a> 的 href 不会在纯文本化时被丢掉。
    text = _inline_markdown(text)
    lines = []
    for line in text.splitlines():
        stripped = _WS_RE.sub(" ", line).rstrip()
        if stripped or (lines and lines[-1]):
            lines.append(stripped)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


# --------------------------------------------------------------------------- 落盘

_SLUG_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")


def _slug(text: str, limit: int = 60) -> str:
    cleaned = _SLUG_RE.sub("_", str(text or "").strip()).strip("._-")
    return (cleaned or "untitled")[:limit]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_archive(candidate_key: str, *, filename: str, data: bytes,
                   meta: dict[str, Any]) -> tuple[Path, str]:
    folder = _archive_root() / candidate_key
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    target.write_bytes(data)
    sidecar = folder / "meta.json"
    sidecar.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return target, _sha256_bytes(data)


def _first_doi(*texts: str) -> str:
    pattern = re.compile(r"10\.\d{4,9}/[^\s\"'<>\],;)]+")
    for text in texts:
        match = pattern.search(str(text or ""))
        if match:
            return match.group(0).rstrip(".,;")
    return ""


def _insert_or_bump(*, doi: str, title: str, journal: str, year: str, url: str,
                    source_kind: str, provider: str, text: str, raw_format: str,
                    conversion: str, stored_path: str, raw_sha256: str, raw_bytes: int,
                    session_id: str = "", request_id: str = "",
                    user_id: int = 0) -> dict[str, Any]:
    ensure_schema()
    now = _now()
    content_sha = _sha256_bytes(str(text or "").encode("utf-8"))
    key_doi = str(doi or "").strip().lower()
    conn = _connect()
    try:
        row = None
        if key_doi:
            row = conn.execute(
                "SELECT * FROM corpus_candidates WHERE lower(doi) = ? ORDER BY id LIMIT 1",
                (key_doi,)).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM corpus_candidates WHERE content_sha256 = ? ORDER BY id LIMIT 1",
                (content_sha,)).fetchone()
        if row is not None:
            providers = set(_loads_list(row["providers"]))
            providers.add(provider)
            conn.execute(
                "UPDATE corpus_candidates SET updated_at=?, frequency=frequency+1, last_seen_at=?, "
                "providers=?, url=CASE WHEN url='' THEN ? ELSE url END, "
                "stored_path=CASE WHEN stored_path='' THEN ? ELSE stored_path END "
                "WHERE id=?",
                (now, now, json.dumps(sorted(providers), ensure_ascii=False),
                 url, stored_path, row["id"]))
            conn.commit()
            return {"id": row["id"], "status": row["status"], "deduped": True}
        cursor = conn.execute(
            "INSERT INTO corpus_candidates (created_at, updated_at, status, doi, title, journal, year, "
            "url, source_kind, providers, char_count, content_sha256, fulltext_path, frequency, "
            "last_seen_at, requested_by, session_id, request_id, raw_format, conversion, "
            "raw_sha256, raw_bytes, stored_path, fetched_at, archive_state, origin) "
            "VALUES (?,?,'pending',?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,'archived',?)",
            (now, now, doi, title, journal, year, url, source_kind,
             json.dumps([provider], ensure_ascii=False), len(str(text or "")), content_sha,
             stored_path, now, int(user_id), session_id, request_id, raw_format, conversion,
             raw_sha256, int(raw_bytes), stored_path, now, source_kind))
        conn.commit()
        return {"id": int(cursor.lastrowid), "status": "pending", "deduped": False}
    finally:
        conn.close()


def _loads_list(text: str) -> list:
    try:
        value = json.loads(text or "[]")
        return value if isinstance(value, list) else []
    except Exception:
        return []


# --------------------------------------------------------------------------- 采集入口

def archive_fetch(winner: dict[str, Any], *, doi: str = "", title: str = "",
                  session_id: str = "", request_id: str = "", user_id: int = 0) -> dict[str, Any] | None:
    """抓取胜出结果 → 原始 Markdown 落盘（调用方必须传**未截断**的胜出正文）。"""
    try:
        if not isinstance(winner, dict):
            return None
        content = str(winner.get("content") or "")
        if len(content.strip()) < MIN_CONTENT_CHARS:
            return None
        winner_format = str(winner.get("format") or "")
        raw_format = sniff_format(content, winner_format)
        if raw_format == "html":
            body, conversion = html_to_markdown(content), "html_to_markdown"
        elif raw_format == "markdown":
            # 抓取器可能已经做过一次 HTML→文本提取；如实记录“谁做的转换”，
            # 不把上游提取器的输出冒充成“我们做了确定性转换”。
            upstream = winner_format not in ("markdown", "md", "text", "txt", "")
            body = content
            conversion = (f"upstream_extract({winner_format})" if upstream
                          else "none(passthrough)")
        else:
            upstream = winner_format not in ("text", "txt", "plain", "plain_text", "")
            body = content
            conversion = (f"upstream_extract({winner_format})" if upstream
                          else "none(plain_text_passthrough)")

        doi_value = (str(doi or "").strip().lower()
                     or _first_doi(content[:8000], str(winner.get("title") or ""),
                                   str(winner.get("url") or "")))
        paper_title = str(winner.get("title") or title or "").strip()
        stored_key = _slug(doi_value or paper_title or _sha256_bytes(body.encode("utf-8"))[:12], 70)
        payload = body.encode("utf-8")
        meta = {
            "doi": doi_value,
            "title": paper_title,
            "url": str(winner.get("url") or ""),
            "winner_format": str(winner.get("format") or ""),
            "raw_format": raw_format,
            "conversion": conversion,
            "fetched_at": _now(),
            "sha256_of_saved_bytes": _sha256_bytes(payload),
            "sha256_of_source_text": _sha256_bytes(content.encode("utf-8")),
            "bytes": len(payload),
            "chars": len(body),
            "session_id": session_id,
            "request_id": request_id,
        }
        path, digest = _write_archive(stored_key, filename="paper.md", data=payload, meta=meta)
        return _insert_or_bump(
            doi=doi_value, title=paper_title, journal=str(winner.get("journal") or ""),
            year=str(winner.get("year") or ""), url=str(winner.get("url") or ""),
            source_kind="fetch_paper", provider=str(winner.get("format") or "fetch_paper"),
            text=body, raw_format=raw_format, conversion=conversion,
            stored_path=str(path), raw_sha256=digest, raw_bytes=len(payload),
            session_id=session_id, request_id=request_id, user_id=user_id)
    except Exception:
        logger.warning("corpus_review: 抓取原文归档失败（已忽略）", exc_info=True)
        return None


def archive_upload(name: str, raw: bytes, text: str = "", *, user_id: int = 0,
                   session_id: str = "", request_id: str = "",
                   consent: bool = False, is_admin: bool = False) -> dict[str, Any] | None:
    """上传文件 → 原始字节落盘。普通用户默认不归档，需显式授权或管理员本人上传。"""
    try:
        if not consent and not is_admin:
            return None
        data = bytes(raw or b"")
        if not data or len(data) > MAX_ARCHIVE_BYTES:
            return None
        suffix = Path(str(name or "uploaded.pdf")).suffix.lower() or ".bin"
        if suffix not in {".pdf", ".docx", ".doc", ".xlsx", ".csv", ".txt", ".md"}:
            return None
        if suffix == ".pdf" and not data.startswith(b"%PDF-"):
            return None
        body = str(text or "")
        doi_value = _first_doi(body[:8000])
        stem = Path(str(name or "uploaded")).stem
        payload_name = f"original{suffix}"
        stored_key = _slug(f"{stem}_{_sha256_bytes(data)[:10]}", 80)
        meta = {
            "doi": doi_value,
            "title": stem,
            "filename": str(name or ""),
            "raw_format": suffix.lstrip(".") + "_binary",
            "conversion": "none(raw_binary)",
            "fetched_at": _now(),
            "sha256_of_saved_bytes": _sha256_bytes(data),
            "bytes": len(data),
            "extracted_chars": len(body),
            "consent": bool(consent),
            "uploaded_by": int(user_id),
            "session_id": session_id,
            "request_id": request_id,
        }
        path, digest = _write_archive(stored_key, filename=payload_name, data=data, meta=meta)
        return _insert_or_bump(
            doi=doi_value, title=stem, journal="", year="", url="",
            source_kind="upload", provider="user_upload", text=body,
            raw_format=suffix.lstrip(".") + "_binary", conversion="none(raw_binary)",
            stored_path=str(path), raw_sha256=digest, raw_bytes=len(data),
            session_id=session_id, request_id=request_id, user_id=user_id)
    except Exception:
        logger.warning("corpus_review: 上传原文归档失败（已忽略）", exc_info=True)
        return None


# --------------------------------------------------------------------------- 查询与决策

def _row_to_public(row: sqlite3.Row, *, with_preview: bool = False) -> dict[str, Any]:
    item = {
        "id": int(row["id"]),
        "status": row["status"],
        "doi": row["doi"],
        "title": row["title"],
        "journal": row["journal"],
        "year": row["year"],
        "url": row["url"],
        "source_kind": row["source_kind"],
        "providers": _loads_list(row["providers"]),
        "char_count": int(row["char_count"] or 0),
        "frequency": int(row["frequency"] or 0),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "decided_at": row["decided_at"],
        "note": row["note"],
    }
    for column in ("raw_format", "conversion", "raw_bytes", "fetched_at", "archive_state"):
        try:
            item[column] = row[column]
        except (IndexError, KeyError):
            item[column] = ""
    try:
        item["stored_path"] = row["stored_path"]
    except (IndexError, KeyError):
        item["stored_path"] = ""
    if with_preview:
        item["preview"] = _preview(item.get("stored_path") or "")
    return item


def _preview(path_text: str, limit: int = 1200) -> str:
    try:
        path = Path(path_text)
        if not path.is_file():
            return ""
        if path.suffix.lower() in {".pdf", ".docx", ".xlsx", ".doc"}:
            return ""
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:limit]
    except Exception:
        return ""


def list_candidates(*, status: str = "pending", limit: int = 50,
                    before_id: int | None = None, order: str = "frequency") -> dict[str, Any]:
    ensure_schema()
    limit = max(1, min(int(limit or 50), 200))
    conn = _connect()
    try:
        params: list[Any] = []
        where = []
        if status and status != "all":
            where.append("status = ?")
            params.append(status)
        if before_id:
            where.append("id < ?")
            params.append(int(before_id))
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        order_sql = "frequency DESC, id DESC" if order == "frequency" else "id DESC"
        rows = conn.execute(
            f"SELECT * FROM corpus_candidates{clause} ORDER BY {order_sql} LIMIT ?",
            (*params, limit + 1)).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        counts = {row["status"]: int(row["n"]) for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM corpus_candidates GROUP BY status")}
        return {
            "candidates": [_row_to_public(row) for row in rows],
            "has_more": has_more,
            "next_before_id": int(rows[-1]["id"]) if has_more and rows else None,
            "counts": counts,
        }
    finally:
        conn.close()


def get_candidate(candidate_id: int) -> dict[str, Any] | None:
    ensure_schema()
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM corpus_candidates WHERE id = ?", (int(candidate_id),)).fetchone()
        return _row_to_public(row, with_preview=True) if row else None
    finally:
        conn.close()


def decide(candidate_id: int, action: str, *, admin_user_id: int, note: str = "") -> dict[str, Any]:
    """approve / reject / reopen：只表示“是否保留这份原始资料”，与正式语料无关。"""
    ensure_schema()
    action = str(action or "").strip().lower()
    transitions = {
        "approve": ("approved", ("pending", "rejected")),
        "reject": ("rejected", ("pending", "approved")),
        "reopen": ("pending", ("approved", "rejected")),
    }
    if action not in transitions:
        raise ValueError(f"unknown action: {action}")
    target, allowed_from = transitions[action]
    now = _now()
    marks = ",".join("?" for _ in allowed_from)
    conn = _connect()
    try:
        cursor = conn.execute(
            f"UPDATE corpus_candidates SET status=?, decided_by=?, decided_at=?, updated_at=?, note=? "
            f"WHERE id=? AND status IN ({marks})",
            (target, int(admin_user_id), now, now, str(note or ""), int(candidate_id), *allowed_from))
        conn.commit()
        row = conn.execute("SELECT * FROM corpus_candidates WHERE id = ?",
                           (int(candidate_id),)).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        return {"candidate": _row_to_public(row, with_preview=False),
                "changed": bool(cursor.rowcount), "idempotent": not cursor.rowcount}
    finally:
        conn.close()


def raw_file(candidate_id: int) -> dict[str, Any]:
    """给管理端下载/预览用：返回真实落盘文件与媒体类型。"""
    ensure_schema()
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM corpus_candidates WHERE id = ?",
                           (int(candidate_id),)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise KeyError(candidate_id)
    path = Path(row["stored_path"] or "")
    if not path.is_file():
        raise FileNotFoundError(str(path))
    suffix = path.suffix.lower()
    mime = {".md": "text/markdown; charset=utf-8", ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".csv": "text/csv; charset=utf-8", ".txt": "text/plain; charset=utf-8",
            ".md2": "text/markdown; charset=utf-8"}.get(suffix, "application/octet-stream")
    try:
        raw_format = row["raw_format"]
    except (IndexError, KeyError):
        raw_format = ""
    return {"path": path, "mime": mime, "filename": path.name,
            "raw_format": raw_format, "title": row["title"], "id": int(row["id"])}

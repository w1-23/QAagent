# -*- coding: utf-8 -*-
"""文献入库审核候选池（管理员后台）。

职责边界（刻意保持很小）：
1. 把"值得进语料的论文"记录成候选：问答中抓取成功的全文、用户上传的 PDF。
2. 记录频次（同一 DOI 被抓取/命中的次数），高频候选优先入库。
3. 审核状态机：pending → approved / rejected →（重审）→ pending；入库成功 → ingested。
4. 批量入库 = 调既有事务式导入器 ``04_脚本_scripts/ingest.py``（新建 generation + 可选原子激活）。
5. 整批回滚 = 调 ``corpus_admin activate`` 把指针切回上一代。

**不遇到的东西**（重要）：
- 不改写 ``tool_results``（那是模型可见的证据副本）；
- 不碰既有 5 批语料目录，每次入库只新增 generation；
- 任何捕获失败都只记日志，绝不影响问答主流程。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

QUEUE_DIRNAME = "_review_queue"
BATCH_CORPUS_FAMILY = "literature"
MIN_CONTENT_CHARS = 2000
DEFAULT_CHUNK_SIZE = 800

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


def _project_root() -> Path:
    # src/core/corpus_review.py → 源码根上一级是仓库根
    return Path(__file__).resolve().parents[3]


# --------------------------------------------------------------------------- 基础

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(str(_db_path()), timeout=30)


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    _db_path().parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
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
        conn.execute("CREATE INDEX IF NOT EXISTS ix_cand_status ON corpus_candidates(status, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_cand_doi ON corpus_candidates(doi)")
    _schema_ready = True


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
        "char_count": int(row["char_count"]),
        "frequency": int(row["frequency"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "decided_at": row["decided_at"],
        "batch_id": row["batch_id"],
        "note": row["note"],
    }
    if with_preview:
        item["preview"] = _preview(row["fulltext_path"])
    return item


def _loads_list(text: str) -> list:
    try:
        value = json.loads(text or "[]")
        return value if isinstance(value, list) else []
    except Exception:
        return []


def _preview(path_text: str, limit: int = 600) -> str:
    path = Path(path_text)
    if not path_text or not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return text[:limit]


# --------------------------------------------------------------------------- 捕获

_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", re.IGNORECASE)


def _first_doi(*texts: str) -> str:
    for text in texts:
        if not text:
            continue
        match = _DOI_RE.search(str(text))
        if match:
            return match.group(0).rstrip(".,;)]").lower()
    return ""


def _slug(text: str, limit: int = 60) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", (text or "untitled").strip())
    return cleaned[:limit].strip("_") or "untitled"


def _write_fulltext(candidate_key: str, text: str) -> tuple[Path, str]:
    folder = _queue_root() / candidate_key
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "fulltext.md"
    target.write_text(text, encoding="utf-8", newline="\n")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    meta = {
        "char_count": len(text),
        "content_sha256": digest,
        "written_at": _now(),
    }
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return target, digest


def _insert_or_bump(*, doi: str, title: str, journal: str, year: str, url: str,
                    source_kind: str, provider: str, text: str,
                    session_id: str, request_id: str, user_id: int) -> dict[str, Any]:
    """同 DOI 已存在则累加频次与来源；否则新建候选。"""
    ensure_schema()
    now = _now()
    clean_doi = (doi or "").strip().lower()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        existing = None
        if clean_doi:
            existing = conn.execute(
                "SELECT * FROM corpus_candidates WHERE doi = ? ORDER BY id LIMIT 1", (clean_doi,)
            ).fetchone()
        if existing is not None:
            providers = _loads_list(existing["providers"])
            if provider and provider not in providers:
                providers.append(provider)
            conn.execute(
                "UPDATE corpus_candidates SET frequency = frequency + 1, last_seen_at = ?,"
                " updated_at = ?, providers = ?, url = COALESCE(NULLIF(url, ''), ?)"
                " WHERE id = ?",
                (now, now, json.dumps(providers, ensure_ascii=False),
                 existing["url"] or url, int(existing["id"])),
            )
            conn.commit()
            logger.info("corpus_review: 累加候选 #%s（doi=%s, freq=%s）",
                        existing["id"], clean_doi, int(existing["frequency"]) + 1)
            return {"id": int(existing["id"]), "status": existing["status"], "deduped": True}

        folder_key = f"{int(datetime.now(timezone.utc).timestamp())}-{uuid.uuid4().hex[:6]}"
        fulltext_path, digest = _write_fulltext(folder_key, text)
        cursor = conn.execute(
            "INSERT INTO corpus_candidates (created_at, updated_at, status, doi, title, journal, year,"
            " url, source_kind, providers, char_count, content_sha256, fulltext_path, frequency,"
            " last_seen_at, requested_by, session_id, request_id)"
            " VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (now, now, clean_doi, title.strip()[:300], journal.strip()[:200], year.strip()[:16],
             url.strip()[:500], source_kind, json.dumps([provider] if provider else [], ensure_ascii=False),
             len(text), digest, str(fulltext_path), now, int(user_id or 0),
             session_id or "", request_id or ""),
        )
        conn.commit()
        return {"id": int(cursor.lastrowid), "status": "pending", "deduped": False}


def capture_fetch(artifact: Any, *, session_id: str = "", request_id: str = "",
                  user_id: int = 0) -> dict[str, Any] | None:
    """抓取成功的全文 → 候审队列（永不抛异常）。"""
    try:
        if isinstance(artifact, dict):
            content = str(artifact.get("content") or artifact.get("text") or "")
            title = str(artifact.get("title") or "")
            url = str(artifact.get("url") or "")
            journal = str(artifact.get("journal") or "")
            year = str(artifact.get("year") or "")
            doi = _first_doi(str(artifact.get("doi") or ""), content[:5000], title, url)
            provider = str(artifact.get("provider") or artifact.get("source") or "fetch_paper")
        else:
            content = str(artifact or "")
            title, url, journal, year = "", "", "", ""
            doi = _first_doi(content[:5000])
            provider = "fetch_paper"
        if len(content) < MIN_CONTENT_CHARS:
            return None
        if not doi and not title:
            return None
        return _insert_or_bump(doi=doi, title=title, journal=journal, year=year, url=url,
                               source_kind="fetch_paper", provider=provider, text=content,
                               session_id=session_id, request_id=request_id, user_id=user_id)
    except Exception:
        logger.warning("corpus_review: 抓取候选入队失败（已忽略）", exc_info=True)
        return None


def capture_upload(name: str, text: str, *, session_id: str = "", request_id: str = "",
                   user_id: int = 0, source_kind: str = "upload") -> dict[str, Any] | None:
    """用户上传的 PDF/文档正文 → 候审队列（永不抛异常）。"""
    try:
        body = str(text or "")
        if len(body) < MIN_CONTENT_CHARS:
            return None
        doi = _first_doi(body[:5000])
        title = Path(name or "uploaded").stem[:300]
        return _insert_or_bump(doi=doi, title=title, journal="", year="", url="",
                               source_kind=source_kind, provider="user_upload", text=body,
                               session_id=session_id, request_id=request_id, user_id=user_id)
    except Exception:
        logger.warning("corpus_review: 上传候选入队失败（已忽略）", exc_info=True)
        return None


def bump_frequency(candidate_id: int, amount: int = 1) -> None:
    try:
        ensure_schema()
        with _connect() as conn:
            conn.execute("UPDATE corpus_candidates SET frequency = frequency + ?, updated_at = ?"
                         " WHERE id = ?", (int(amount), _now(), int(candidate_id)))
            conn.commit()
    except Exception:
        logger.warning("corpus_review: 频次累加失败（已忽略）", exc_info=True)


# --------------------------------------------------------------------------- 查询与审核

def list_candidates(*, status: str = "pending", limit: int = 50, before_id: int | None = None,
                    order: str = "frequency") -> dict[str, Any]:
    ensure_schema()
    limit = max(1, min(int(limit or 50), 200))
    order_sql = "frequency DESC, updated_at DESC, id DESC" if order == "frequency" else "id DESC"
    params: list[Any] = []
    where = []
    if status and status != "all":
        where.append("status = ?")
        params.append(status)
    if before_id:
        where.append("id < ?")
        params.append(int(before_id))
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM corpus_candidates {clause} ORDER BY {order_sql} LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
        counts = {row["status"]: int(row["n"]) for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM corpus_candidates GROUP BY status")}
    items = [_row_to_public(r) for r in rows[:limit]]
    return {
        "candidates": items,
        "has_more": len(rows) > limit,
        "next_before_id": items[-1]["id"] if items else None,
        "counts": counts,
    }


def get_candidate(candidate_id: int) -> dict[str, Any] | None:
    ensure_schema()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM corpus_candidates WHERE id = ?", (int(candidate_id),)).fetchone()
    return _row_to_public(row, with_preview=True) if row else None


_ALLOWED_ACTIONS = {"approve": "approved", "reject": "rejected", "reopen": "pending"}


def decide(candidate_id: int, action: str, *, admin_user_id: int, note: str = "") -> dict[str, Any]:
    """幂等状态迁移：条件更新失败即视为重复操作，返回当前状态。"""
    ensure_schema()
    action = (action or "").strip().lower()
    if action not in _ALLOWED_ACTIONS:
        raise ValueError(f"unknown action: {action}")
    target = _ALLOWED_ACTIONS[action]
    now = _now()
    if action == "approve":
        from_status = ("pending", "rejected")
    elif action == "reject":
        from_status = ("pending", "approved")
    else:
        from_status = ("rejected", "failed")
    placeholders = ",".join("?" for _ in from_status)
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            f"UPDATE corpus_candidates SET status = ?, decided_by = ?, decided_at = ?,"
            f" updated_at = ?, note = CASE WHEN ? <> '' THEN ? ELSE note END"
            f" WHERE id = ? AND status IN ({placeholders})",
            (target, int(admin_user_id or 0), now, now, note, note, int(candidate_id), *from_status),
        )
        conn.commit()
        changed = cur.rowcount
        row = conn.execute("SELECT * FROM corpus_candidates WHERE id = ?", (int(candidate_id),)).fetchone()
    if row is None:
        raise KeyError(f"candidate {candidate_id} not found")
    return {"candidate": _row_to_public(row), "changed": bool(changed), "idempotent": not changed}


# --------------------------------------------------------------------------- 批量入库

def _active_generation() -> str:
    try:
        pointer = json.loads((_data_dir() / "active_index.json").read_text(encoding="utf-8"))
        return str(pointer.get("generation") or "")
    except Exception:
        return ""


def _run_ingest(batch_dir: Path, corpus_id: str, display_name: str, *, activate: bool,
                chunk_size: int) -> tuple[bool, str, str]:
    script = _project_root() / "04_脚本_scripts" / "ingest.py"
    if not script.is_file():
        return False, "", f"ingest script not found: {script}"
    cmd = [sys.executable, str(script), "--dir", str(batch_dir), "--corpus-id", corpus_id,
           "--family-id", BATCH_CORPUS_FAMILY, "--display-name", display_name,
           "--chunk-size", str(int(chunk_size)), "--cpu"]
    if activate:
        cmd.append("--activate")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600,
                              cwd=str(_project_root()))
    except Exception as exc:  # pragma: no cover - 运行时兜底
        return False, "", f"{type(exc).__name__}: {exc}"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    ok = proc.returncode == 0
    return ok, out, err or out


def _parse_generation(stdout: str) -> str:
    match = re.search(r"generation[= ]+([A-Za-z0-9_]+)", stdout or "")
    return match.group(1) if match else ""


def apply_batch(*, admin_user_id: int, candidate_ids: list[int] | None = None,
                activate: bool = False, chunk_size: int = DEFAULT_CHUNK_SIZE) -> dict[str, Any]:
    """把 approved 候选攒成一批，调导入器生成新一代语料（可选激活）。"""
    ensure_schema()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        if candidate_ids:
            ids = [int(x) for x in candidate_ids]
            marks = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"SELECT * FROM corpus_candidates WHERE status = 'approved' AND id IN ({marks})"
                f" ORDER BY frequency DESC, id", ids).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM corpus_candidates WHERE status = 'approved'"
                " ORDER BY frequency DESC, id").fetchall()
    if not rows:
        return {"ok": False, "error": "no_approved_candidates"}

    batch_id = f"b{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    batch_dir = _queue_root() / f"_batch_{batch_id}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    copied: list[int] = []
    for row in rows:
        src = Path(row["fulltext_path"])
        if not src.is_file():
            continue
        name = f"{int(row['id']):05d}_{_slug(row['title'] or row['doi'] or 'doc')}.md"
        shutil.copyfile(src, batch_dir / name)
        copied.append(int(row["id"]))
    if not copied:
        shutil.rmtree(batch_dir, ignore_errors=True)
        return {"ok": False, "error": "no_fulltext_files"}

    prev_generation = _active_generation()
    corpus_id = f"admin_{batch_id.lower().replace('-', '_')[:58]}"
    display_name = f"管理员入库 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}"
    ok, out, err = _run_ingest(batch_dir, corpus_id, display_name,
                               activate=activate, chunk_size=chunk_size)
    generation = _parse_generation(out) or ("" if not activate else _active_generation())
    status = "applied" if ok else "failed"
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO corpus_batches (id, created_at, status, generation, prev_generation,"
            " corpus_ids, candidate_ids, chunk_count, note) VALUES (?,?,?,?,?,?,?,?,?)",
            (batch_id, now, status, generation, prev_generation,
             json.dumps([corpus_id]), json.dumps(copied), 0, err[:1000]),
        )
        if ok:
            marks = ",".join("?" for _ in copied)
            conn.execute(
                f"UPDATE corpus_candidates SET status = 'ingested', batch_id = ?, updated_at = ?"
                f" WHERE id IN ({marks})", (batch_id, now, *copied))
        conn.commit()
    return {
        "ok": ok,
        "batch_id": batch_id,
        "status": status,
        "generation": generation,
        "prev_generation": prev_generation,
        "corpus_id": corpus_id,
        "candidate_ids": copied,
        "activate": bool(activate),
        "output": out[-2000:],
        "error": "" if ok else err[-2000:],
    }


def list_batches(limit: int = 20) -> list[dict[str, Any]]:
    ensure_schema()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM corpus_batches ORDER BY created_at DESC LIMIT ?",
                            (max(1, min(int(limit or 20), 100)),)).fetchall()
    return [{
        "id": r["id"],
        "created_at": r["created_at"],
        "status": r["status"],
        "generation": r["generation"],
        "prev_generation": r["prev_generation"],
        "corpus_ids": _loads_list(r["corpus_ids"]),
        "candidate_ids": _loads_list(r["candidate_ids"]),
        "note": r["note"],
    } for r in rows]


def rollback_batch(batch_id: str, *, admin_user_id: int) -> dict[str, Any]:
    """整批回滚：把活动索引指针切回该批之前的 generation。"""
    ensure_schema()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM corpus_batches WHERE id = ?", (batch_id,)).fetchone()
    if row is None:
        raise KeyError(f"batch {batch_id} not found")
    prev = str(row["prev_generation"] or "")
    if not prev:
        return {"ok": False, "error": "no_previous_generation"}
    if prev == _active_generation():
        return {"ok": True, "already": True, "generation": prev}
    script = _project_root() / "01_源码_src" / "src" / "retrieval" / "corpus_admin.py"
    cmd = [sys.executable, str(script), "activate", "--generation", prev]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(_project_root()))
    ok = proc.returncode == 0
    now = _now()
    with _connect() as conn:
        if ok:
            conn.execute("UPDATE corpus_batches SET status = 'rolled_back', note = ? WHERE id = ?",
                         (f"rolled back by admin {admin_user_id} at {now}", batch_id))
            conn.execute(
                "UPDATE corpus_candidates SET status = 'approved', batch_id = '', updated_at = ?"
                " WHERE batch_id = ?", (now, batch_id))
        conn.commit()
    return {"ok": ok, "generation": prev,
            "output": ((proc.stdout or "") + (proc.stderr or ""))[-1000:]}

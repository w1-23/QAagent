"""Append-only ledger for web fetch failures and same-request local checks.

The ledger stores identifiers and failure diagnostics only; it never stores
fetched page bodies or local corpus passages.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_requests: dict[str, dict] = {}
_MAX_TRACKED_REQUESTS = 512


def _context() -> dict:
    try:
        from src.core.tracing import get_request_id, get_session_id, get_job_id, original_query
        return {"request_id": get_request_id(), "session_id": get_session_id(),
                "job_id": get_job_id(), "user_query": original_query()[:500]}
    except Exception:
        return {"request_id": "-", "session_id": "", "job_id": "", "user_query": ""}


def _ledger_path() -> Path:
    from src.config import PROJECT_ROOT
    return Path(PROJECT_ROOT) / "logs" / "web_fetch_failures.jsonl"


def _append(record: dict) -> None:
    try:
        path = _ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:
        logger.exception("Could not append web fetch failure ledger")


def _classify(error: str) -> str:
    value = str(error or "").lower()
    if any(token in value for token in ("timeout", "timed out", "deadline exceeded")):
        return "timeout"
    if "empty content" in value or "no page content" in value:
        return "empty_content"
    # Only label a destination as access-blocked when the fetch response itself
    # says so. HTTP status from the DeepSeek API is a service status, not proof
    # that the target website blocked the crawler.
    if re.search(r"(target|origin|website|site).{0,40}(403|forbidden|access denied|captcha|robot check|blocked)", value):
        return "target_access_blocked"
    if any(token in value for token in ("captcha", "robot check", "access denied by site", "blocked by the site")):
        return "target_access_blocked"
    if "http 401" in value or "http 403" in value or "http 429" in value:
        return "fetch_service_http_error"
    return "fetch_failed_unclassified"


_HARD_FAILURES = {"target_access_blocked", "fetch_service_http_error"}


def _norm_target(url: str) -> str:
    value = str(url or "").strip().lower()
    value = value.split("#", 1)[0]
    if "?" in value:
        base, _, query = value.partition("?")
        kept = [part for part in query.split("&")
                if part and not part.startswith(("utm_", "gclid", "fbclid", "spm", "from="))]
        value = base + ("?" + "&".join(kept) if kept else "")
    return value.rstrip("/")


def known_hard_failure(url: str) -> str:
    """同一请求内已被判定为「确定性失败」的目标，返回其分类；否则返回空串。

    只认两类：目标站点明确拒绝/鉴权失败、抓取服务返回 401/403/429。临时超时
    不在此列——那种情况允许在剩余预算内有限重试。
    """
    key = _norm_target(url)
    if not key:
        return ""
    req = str(_context().get("request_id") or "-")
    with _lock:
        state = _requests.get(req) or {}
        for item in state.get("failures", []):
            if (item.get("classification") in _HARD_FAILURES
                    and _norm_target(item.get("url")) == key):
                return str(item.get("classification"))
    return ""


def record_fetch_failures(attempts: list[dict], *, doi: str = "", title: str = "") -> None:
    ctx = _context()
    req = str(ctx.get("request_id") or "-")
    failures = []
    for attempt in attempts or []:
        if attempt.get("ok") and str(attempt.get("content") or "").strip():
            continue
        error = str(attempt.get("error") or "empty content")[:1000]
        item = {"url": str(attempt.get("url") or "")[:2000],
                "format": str(attempt.get("format") or "page"),
                "classification": _classify(error), "error": error,
                "doi": str(doi or "")[:300], "title": str(title or "")[:500],
                "observed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **ctx}
        _append({"event": "web_fetch_failure", **item})
        failures.append(item)
    if not failures:
        return
    with _lock:
        if req not in _requests and len(_requests) >= _MAX_TRACKED_REQUESTS:
            _requests.pop(next(iter(_requests)))
        state = _requests.setdefault(req, {"failures": [], "local_checks": []})
        state["failures"].extend(failures)
        checks = list(state["local_checks"])
    for check in checks:
        if check.get("status") == "zero_hit":
            _append({"event": "fetch_failure_local_zero_hit", "observed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                     "fetch_failure_ids": [f.get("url") for f in failures],
                     "local_query": check.get("query"), "corpus_types": check.get("corpus_types"),
                     "local_status": "zero_hit_for_this_query", **ctx})


def record_local_check(query: str, corpus_types: list[str], status: str,
                       rows: list[dict] | None = None) -> None:
    ctx = _context()
    req = str(ctx.get("request_id") or "-")
    check = {"query": str(query or "")[:500], "corpus_types": list(corpus_types or []),
             "status": str(status or "unknown"),
             "matches": [{"doi": str(r.get("doi") or "")[:300],
                          "title": str(r.get("title") or "")[:500],
                          "record_id": str(r.get("paper_id") or r.get("chunk_id") or
                                           r.get("variety_id") or r.get("registry_id") or "")[:300]}
                         for r in (rows or [])[:12]], **ctx}
    if check["status"] not in {"zero_hit", "hit", "error", "skipped_no_matching_family"}:
        return
    with _lock:
        if req not in _requests and len(_requests) >= _MAX_TRACKED_REQUESTS:
            _requests.pop(next(iter(_requests)))
        state = _requests.setdefault(req, {"failures": [], "local_checks": []})
        state["local_checks"].append(check)
        failures = list(state["failures"])
    _append({"event": "local_corpus_check", "observed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **check})
    if failures and check["status"] == "zero_hit":
        _append({"event": "fetch_failure_local_zero_hit", "observed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                 "fetch_failure_ids": [f.get("url") for f in failures],
                 "doi_title_candidates": [{"doi": f.get("doi"), "title": f.get("title")} for f in failures],
                 "local_query": check["query"], "corpus_types": check["corpus_types"],
                 "local_status": "zero_hit_for_this_query", **ctx})

"""Agent tools for the administrator-published shared literature corpus.

Every operation goes through the running shared-corpus runtime. The tools
accept identifiers only: never filesystem paths, URLs or shell commands.
The authenticated account comes from host request tracing, is cross-checked
against the process account lease, and is re-checked against the active auth
store. Publication rights are granted exclusively by the administrator API;
these tools can propose, prepare, index already-published documents and read
published content.
"""
from __future__ import annotations

import json
import logging
import re

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

MAX_SEARCH_LIMIT = 12
MAX_QUERY_CHARS = 2000
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")

_PROPOSAL_FIELDS = (
    "proposal_id", "candidate_id", "state", "rights", "document_id", "version_id",
    "title", "doi", "year", "source_kind", "reason", "gap_evidence",
    "created_at", "updated_at", "deduped", "publishable",
)
_METADATA_FIELDS = ("content_sha256", "char_count", "chunk_count", "schema_version",
                    "section_path")
_ROW_FIELDS = ("source_family", "index_generation", "document_id", "version_id",
               "chunk_id", "title", "section_name", "read_ref", "excerpt")
_UNAVAILABLE_REASON = {
    "shared_corpus_unauthenticated": "auth",
    "shared_corpus_account_inactive": "auth",
    "shared_corpus_account_changed": "auth",
    "shared_corpus_disabled": "tool_unavailable",
}


class _ToolUnavailable(RuntimeError):
    """A normal, user-readable refusal; never a traceback."""

    def __init__(self, code: str, message: str):
        self.code = str(code)
        self.message = str(message)
        super().__init__(self.code)


def _outcome(status, reason_code="", coverage="", summary="", artifacts=()):
    return {"status": status, "reason_code": reason_code, "coverage": coverage,
            "summary": summary, "artifacts": list(artifacts)}


def _render(payload, *, status, reason_code="", coverage="", summary="", artifacts=()):
    body = dict(payload or {})
    body["_tool_outcome"] = _outcome(status, reason_code, coverage, summary, artifacts)
    return json.dumps(body, ensure_ascii=False, default=str)


def _fail(code, summary, *, reason_code=None):
    return _render({"error": code}, status="failed",
                   reason_code=reason_code or code, coverage=summary, summary=summary)


def _not_run(code, summary):
    return _render({"error": code}, status="not_run", reason_code=code,
                   coverage=summary, summary=summary)


def _refuse(error):
    return _render({"error": error.code}, status="failed",
                   reason_code=_UNAVAILABLE_REASON.get(error.code, "tool_unavailable"),
                   coverage=error.message, summary=error.message)


def _safe_code(error, fallback):
    text = str(error or "").strip()
    return text if _CODE.fullmatch(text) else fallback


def _identity():
    """Return (owner_user_id, role) after re-checking the active account."""
    from src.auth import user_by_id
    from src.core.tracing import get_user_id

    owner = get_user_id()
    if not owner:
        raise _ToolUnavailable("shared_corpus_unauthenticated", "请先登录后再使用共享文献库。")
    owner = int(owner)
    try:
        from src.core.account_leases import current_account_lease
        lease = current_account_lease()
    except Exception:
        lease = None
    if lease is not None and int(getattr(lease, "owner", 0) or 0) != owner:
        raise _ToolUnavailable("shared_corpus_account_changed",
                               "当前任务的登录账号已变化，请重新发起请求。")
    user = user_by_id(owner)
    if (not user or int(user.get("user_id") or 0) != owner
            or not user.get("active")
            or str(user.get("approval_status") or "") != "approved"):
        raise _ToolUnavailable("shared_corpus_account_inactive", "当前账号不可用，无法访问共享文献库。")
    return owner, str(user.get("role") or "")


def _runtime():
    from src.shared_corpus.runtime import current_runtime

    runtime = current_runtime()
    if runtime is None:
        raise _ToolUnavailable("shared_corpus_disabled", "共享文献库未启用。")
    return runtime


def _candidate_requested_by(runtime, candidate_id, owner):
    """Read review attribution and whether this account fetched the same bytes."""
    import sqlite3

    store = runtime.catalog._candidate_store()
    if not store.is_file():
        raise ValueError("candidate_store_unavailable")
    try:
        connection = sqlite3.connect(store.as_uri() + "?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        raise ValueError("candidate_store_unavailable") from exc
    try:
        row = connection.execute(
            "SELECT requested_by FROM corpus_candidates WHERE id = ?",
            (int(candidate_id),)).fetchone()
        discoveries = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='corpus_fetch_discoveries'").fetchone()
        if row is not None and discoveries and connection.execute(
                "SELECT 1 FROM corpus_fetch_discoveries WHERE candidate_id=? AND user_id=?",
                (int(candidate_id), owner)).fetchone():
            return owner
    except sqlite3.Error as exc:
        raise ValueError("candidate_store_unavailable") from exc
    finally:
        connection.close()
    if row is None:
        return None
    return int(row[0] or 0)


def _candidate_visible_to(runtime, candidate_id, owner, role):
    """Only the requesting account (or an administrator) may promote a candidate.

    A foreign id and a missing id deliberately return the same False answer so
    the tool cannot be used to probe the review queue.
    """
    requested_by = _candidate_requested_by(runtime, candidate_id, owner)
    if requested_by is None:
        return False
    return requested_by == owner or role == "admin"


def _public_proposal(proposal):
    if not isinstance(proposal, dict):
        return {}
    item = {key: proposal[key] for key in _PROPOSAL_FIELDS if key in proposal}
    for key in ("title", "doi", "year", "source_kind", "reason", "gap_evidence"):
        if isinstance(item.get(key), str):
            item[key] = item[key][:2000]
    metadata = proposal.get("metadata")
    if isinstance(metadata, dict):
        item["metadata"] = {key: metadata[key] for key in _METADATA_FIELDS if key in metadata}
    return item


def _public_job(job):
    if not isinstance(job, dict):
        return {}
    state = job.get("state")
    activation = "waiting_for_idle" if state in {"queued", "building", "waiting_for_idle"} else state
    return {"ingest_job_id": job.get("id"), "maintenance_id": job.get("id"),
            "state": state, "activation_state": activation,
            "generation": job.get("generation"), "created_at": job.get("created"),
            "updated_at": job.get("updated")}


def _public_row(row):
    return {key: row[key] for key in _ROW_FIELDS if key in row}


@tool
def propose_document_ingest(candidate_id: int, gap_evidence: str = "") -> str:
    """Propose one candidate from the shared review queue for the administrator-approved literature library. candidate_id must be a real review-queue id; gap_evidence states why the library lacks this evidence. This records an account-owned proposal and never publishes content."""
    try:
        owner, role = _identity()
        runtime = _runtime()
        try:
            if isinstance(candidate_id, bool):
                raise ValueError
            cid = int(candidate_id)
            if cid <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return _not_run("candidate_id_invalid", "候选编号无效。")
        # Only the account that requested the candidate (or an administrator)
        # may promote it; foreign and missing ids share one not-found answer.
        if not _candidate_visible_to(runtime, cid, owner, role):
            return _render({"candidate_id": cid}, status="no_results",
                           reason_code="candidate_not_found",
                           summary="候选文献不存在。")
        # Catalog validates candidate existence and rejection without reading
        # the archived preview; the adapter never returns candidate bytes.
        proposal = runtime.catalog.propose(cid, owner, gap_evidence or "")
        summary = ("该文献已有本账号的收录提案。"
                   if isinstance(proposal, dict) and proposal.get("deduped")
                   else "已登记文献收录提案。")
        return _render({"proposal": _public_proposal(proposal)}, status="complete",
                       summary=summary)
    except _ToolUnavailable as error:
        return _refuse(error)
    except KeyError:
        return _render({"candidate_id": candidate_id}, status="no_results",
                       reason_code="candidate_not_found", summary="候选文献不存在。")
    except ValueError as error:
        code = _safe_code(error, "shared_corpus_proposal_rejected")
        if code == "candidate_store_unavailable":
            return _fail(code, "候选库暂不可用。", reason_code="source_unavailable")
        messages = {"candidate_id_invalid": "候选编号无效。",
                    "candidate_rejected": "该候选已被拒绝，不能收录。"}
        return _not_run(code, messages.get(code, "无法登记收录提案。"))
    except Exception:
        logger.warning("shared corpus propose failed", exc_info=True)
        return _fail("tool_error", "共享文献收录提案未完成。")


@tool
def prepare_document(proposal_id: str) -> str:
    """Verify and stage one of this account's ingest proposals in the shared literature library. proposal_id is the p_... id returned by propose_document_ingest. The host re-reads the archived candidate and verifies hashes; no paths are accepted. A needs_review result still requires an administrator's publication decision."""
    try:
        owner, role = _identity()
        runtime = _runtime()
        pid = str(proposal_id or "").strip()
        # Owner-scoped lookup: foreign and missing proposals are indistinguishable.
        existing = runtime.catalog.get_proposal(pid, owner)
        if existing is None:
            return _render({"proposal_id": pid}, status="no_results",
                           reason_code="proposal_not_found",
                           summary="收录提案不存在。")
        if not _candidate_visible_to(
                runtime, int(existing.get("candidate_id") or 0), owner, role):
            return _render({"proposal_id": pid}, status="no_results",
                           reason_code="proposal_not_found",
                           summary="收录提案不存在。")
        proposal = runtime.catalog.prepare(pid, owner)
        state = str(proposal.get("state") or "")
        summary = {
            "published": "已生成待索引版本并记录发布权；尚未进入共享索引。",
            "needs_review": "文献已暂存，等待管理员发布决定；尚未进入共享索引。",
            "revoked": "该文献的发布权已被撤回，不会进入共享索引。",
            "failed": "文献准备失败，不会进入共享索引。",
        }.get(state, "文献准备已完成；尚未进入共享索引。")
        status = "failed" if state == "failed" else "complete"
        reason = "" if state == "published" else str(proposal.get("reason") or "")
        indexed = runtime.indexing_status(proposal.get("document_id"), proposal.get("version_id"))
        if indexed["indexing_status"] == "active":
            summary = "该文献版本已在共享索引中。"
        elif indexed["indexing_status"] == "indexed_ready":
            summary = "共享索引候选已包含该版本，正在等待全站更新。"
        return _render({"proposal": _public_proposal(proposal), **indexed},
                       status=status, reason_code=reason, summary=summary)
    except _ToolUnavailable as error:
        return _refuse(error)
    except PermissionError:
        # Defense in depth: ownership failures look exactly like absence.
        return _render({"proposal_id": pid}, status="no_results",
                       reason_code="proposal_not_found", summary="收录提案不存在。")
    except KeyError:
        return _render({"proposal_id": pid}, status="no_results",
                       reason_code="proposal_not_found", summary="收录提案不存在。")
    except ValueError as error:
        code = _safe_code(error, "proposal_invalid")
        if code == "candidate_store_unavailable":
            return _fail(code, "候选库暂不可用。", reason_code="source_unavailable")
        return _not_run(code, "提案编号无效或已无法处理。")
    except Exception:
        logger.warning("shared corpus prepare failed", exc_info=True)
        return _fail("tool_error", "共享文献准备未完成。")


@tool
def commit_document_index() -> str:
    """Queue a trusted host rebuild of the shared literature index. Takes no arguments, paths or commands. Only administratively published documents are indexed; this call cannot publish content, grant rights or read unpublished documents."""
    try:
        owner, _role = _identity()
        runtime = _runtime()
        job = runtime.request_build(owner)
        return _render({"job": _public_job(job)}, status="complete",
                       summary=("相同文献已在共享索引中。" if job["state"] == "active"
                                else "已申请共享文献索引构建，正式更新需等待全站空闲。"))
    except _ToolUnavailable as error:
        return _refuse(error)
    except ValueError as error:
        code = _safe_code(error, "shared_build_rejected")
        messages = {
            "shared_publishable_batch_out_of_bounds": "当前没有可索引的已发布文献，或批次超出范围。",
            "shared_build_pending": "已有待处理的共享索引构建任务。",
            "shared_documents_already_indexed": "相同文献已经索引。",
            "shared_daily_build_budget": "今日共享索引构建次数已达上限。",
        }
        message = messages.get(code, "当前无法申请共享索引构建。")
        return _render({"error": code}, status="not_run", reason_code=code,
                       coverage=message, summary=message)
    except Exception:
        logger.warning("shared corpus build request failed", exc_info=True)
        return _fail("tool_error", "共享索引构建申请未完成。")


@tool
def search_agent_corpus(query: str, limit: int = 6) -> str:
    """Search the shared literature corpus published by administrators. Returns bounded excerpts with document and version read references. Only published, non-revoked content is visible; the query is plain text."""
    text = str(query or "").strip()
    if not text:
        return _not_run("query_empty", "请提供检索词。")
    if len(text) > MAX_QUERY_CHARS:
        return _not_run("query_too_long", "检索词过长。")
    try:
        size = max(1, min(int(limit), MAX_SEARCH_LIMIT))
    except (TypeError, ValueError):
        return _not_run("limit_invalid", "检索条数无效。")
    try:
        _owner, _role = _identity()
        runtime = _runtime()
        from src.shared_corpus.activity import MaintenanceBusy
        if runtime.unavailable or runtime.generation is None:
            return _not_run("shared_index_unavailable", "共享文献索引尚未就绪。")
        try:
            rows = runtime.search(text, limit=size)
        except MaintenanceBusy:
            return _render({}, status="not_run", reason_code="knowledge_base_updating",
                           coverage="共享语料库自更新中，请稍后重试。",
                           summary="共享语料库自更新中，请稍后重试。")
        results = [_public_row(row) for row in (rows or []) if isinstance(row, dict)]
        generation = str(getattr(runtime, "generation", "") or "")
        return _render({"query": text, "limit": size, "count": len(results),
                        "results": results},
                       status="complete" if results else "no_results",
                       coverage=(f"shared_agent_literature:{generation}" if generation else ""),
                       summary=(f"找到 {len(results)} 条共享文献片段" if results
                                else "共享文献库没有匹配内容"))
    except _ToolUnavailable as error:
        return _refuse(error)
    except Exception:
        logger.warning("shared corpus search failed", exc_info=True)
        return _fail("tool_error", "共享文献检索未完成。")

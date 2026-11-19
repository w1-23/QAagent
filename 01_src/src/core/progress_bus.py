"""Progress bus — shared between graph nodes and SSE generator.

v8.3.3: request-scoped progress queue (contextvars, 消除跨请求串扰) +
unified JSON-string data format + usage delta emitter + heartbeat/progress
tracking for long tool executions + SSE raw-frame debug logging.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import math
import re
import time

logger = logging.getLogger(__name__)

# 全局兜底队列（非 SSE 上下文，如 CLI/测试）；SSE 请求使用 per-request 队列
_progress_queue: asyncio.Queue | None = None

# 请求级队列: 每个 SSE 请求 set_request_queue() 绑定自己的队列，emit 只进本请求
_current_queue: contextvars.ContextVar = contextvars.ContextVar(
    "progress_queue", default=None)


def get_progress_queue() -> asyncio.Queue:
    q = _current_queue.get()
    if q is not None:
        return q
    global _progress_queue
    if _progress_queue is None:
        _progress_queue = asyncio.Queue()
    return _progress_queue


# 请求级工具终态流水（供 API 层判定本轮状态用）。某些执行路径不经过
# general_agent_graph 的 tool_status_sink，只看 sink 会把「有工具失败的一轮」
# 判成 completed；这里在唯一的终态出网点记一份，作为结构化状态来源。
_current_tool_journal: contextvars.ContextVar = contextvars.ContextVar(
    "progress_tool_journal", default=None)


def set_request_queue(queue: asyncio.Queue) -> None:
    """绑定当前请求的进度队列（SSE 事件生成器内调用）。"""
    _current_queue.set(queue)


def clear_request_queue() -> None:
    _current_queue.set(None)


def set_request_journal(journal: list | None) -> None:
    """绑定当前请求的工具终态流水（与 set_request_queue 同生命周期）。"""
    _current_tool_journal.set(journal)


def get_request_journal() -> list:
    """Return the request-scoped tool journal (empty list when unbound)."""
    journal = _current_tool_journal.get()
    return journal if isinstance(journal, list) else []


def clear_request_journal() -> None:
    _current_tool_journal.set(None)


# ── D-A1: turn scope (session + request id) for the payload recorder ──
# _call_llm needs to know which session/request a model call belongs to; the
# graph has neither. Bound by the API layer next to set_request_journal.
_current_turn_scope: contextvars.ContextVar = contextvars.ContextVar(
    "progress_turn_scope", default=None)
# A fresh mutable journal is bound once per request. Child asyncio tasks inherit
# this object, so captures inside wait_for/stream tasks can compare prior attempts.
# ContextVar.set inside a child alone would disappear when that task returns.
_current_request_layouts: contextvars.ContextVar = contextvars.ContextVar(
    "progress_request_layouts", default=None)


def set_turn_scope(session_id: str, request_id: str = "") -> None:
    _current_turn_scope.set((str(session_id or ""), str(request_id or "")))
    _current_request_layouts.set({})


def get_request_layouts() -> dict | None:
    """Private metadata journal shared only by tasks within this turn."""
    return _current_request_layouts.get()


def get_turn_scope() -> tuple[str, str]:
    """(session_id, request_id) of the running turn; ("", "") when unbound."""
    scope = _current_turn_scope.get()
    if not scope:
        return "", ""
    try:
        return str(scope[0] or ""), str(scope[1] or "")
    except (TypeError, IndexError):
        return "", ""


def clear_turn_scope() -> None:
    _current_turn_scope.set(None)
    _current_request_layouts.set(None)


# ── v10.5（F8）: SSE/JSONL 行分隔符消毒 ────────────────────────────────────
#   SSE 的 data 段是**单行 JSON**，任何"被解析方当作行分隔符"的字符都会把这条事件
#   劈成两半。Python 的 str.splitlines()（requests.iter_lines 用的就是它）把
#   \x0b \x0c \x1c \x1d \x1e \x85 \u2028 \u2029 全部算作行边界；而 **F7 之后
#   citations 载荷里带着联网网页正文**，真实网页文本里 U+2028/U+0085 并不罕见。
#   实测代价（webcontent_v10_8 的 C48-time-004）：
#     · 抓取解析器丢掉整条 citations 事件 → 该题被判成"回执 0 条、正文 20 个标记
#       全越界"（假缺陷）；
#     · 落盘的 answers.jsonl 因 U+2028 被 read_text().splitlines() 切开，
#       **整个 JSONL 变成不可解析**（连指标脚本都读不进来）。
#   浏览器端只按 '\n' 切分故不受影响，但"判分数据必须能被任何 JSONL 读者解析"
#   是硬要求。这里在**唯一出网点**统一消毒，覆盖 answer / citations / status 全部事件。
_LINE_BREAKERS = {0x0B: " ", 0x0C: " ", 0x1C: " ", 0x1D: " ", 0x1E: " ",
                  0x85: " ", 0x2028: " ", 0x2029: " ", 0x0000: ""}


def _sanitize_wire(obj):
    """递归把"行分隔符类"字符替换成空格（仅文本，不改结构、不改语义）。"""
    if isinstance(obj, str):
        if not any(ord(ch) in _LINE_BREAKERS for ch in obj):
            return obj
        return "".join(_LINE_BREAKERS.get(ord(ch), ch) for ch in obj)
    if isinstance(obj, dict):
        return {k: _sanitize_wire(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_wire(v) for v in obj]
    return obj


def _encode_event(event_type: str, data: dict) -> dict:
    """Encode an SSE event dict with JSON-string data field (canonical format).

    v10.5（F8）: 出网前统一消毒行分隔符，保证「一条事件 = 一行」（见上）。
    """
    return {"event": event_type,
            "data": json.dumps(_sanitize_wire(data), ensure_ascii=False)}


def stamp_sse_event(event: dict, seq: int) -> dict:
    """Give one encoded SSE frame a monotonic ``seq`` and matching ``id``.

    Every frame on a connection goes through this single function, so the
    browser can drop duplicates and the terminal frame's
    ``turn_envelope.terminal_seq`` always equals its own sequence number.
    """
    seq = max(1, int(seq or 1))
    try:
        payload = json.loads((event or {}).get("data") or "{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {"value": payload}
    payload["seq"] = seq
    if (event.get("event") == "done"
            and isinstance(payload.get("turn_envelope"), dict)):
        payload["turn_envelope"]["terminal_seq"] = seq
    stamped = _encode_event(str(event.get("event") or "message"), payload)
    stamped["id"] = str(seq)
    return stamped


def emit_progress(event_type: str, data: dict) -> None:
    """Push a progress event to the current request's SSE queue (non-blocking)."""
    q = get_progress_queue()
    try:
        q.put_nowait(_encode_event(event_type, public_progress_payload(event_type, data)))
    except asyncio.QueueFull:
        pass


def emit_encoded(event_type: str, data: dict) -> None:
    """Alias for emit_progress — canonical event emission."""
    emit_progress(event_type, data)


# ═══════════════════════════════════════════════════════════════════════
# Per-call provider usage events
# ═══════════════════════════════════════════════════════════════════════


def emit_usage_delta(session_id: str, source: str, input_tokens=None,
                     output_tokens=None, total=None, cache_hit=None,
                     cache_miss=None, cache_status: str = "unavailable", *,
                     cache_read=None, cache_write=None, cache_uncached=None,
                     total_tokens=None, request_id: str = "", attempt_id: str = "",
                     logical_call_id: str = "", epoch=None, **usage_fields) -> None:
    """Emit one identified call; cache creation is separate from uncached input.

    ``total`` is a compatibility alias for this call's total_tokens, never a
    task sum. Physical transport observers supply attempt_id; legacy callers
    remain explicitly logical observations.
    """
    from src.core.cache_metrics import _count

    read = _count(cache_read if cache_read is not None else cache_hit)
    uncached = _count(cache_uncached if cache_uncached is not None else cache_miss)
    write = _count(cache_write)
    input_tokens, output_tokens = _count(input_tokens), _count(output_tokens)
    total_tokens = _count(total_tokens if total_tokens is not None else total)
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    if all(v is None for v in (input_tokens, output_tokens, total_tokens, read, write, uncached)):
        return
    known = sum(v is not None for v in (read, write, uncached))
    status = "reported" if known == 3 else "partial" if known else "unavailable"
    ratio = (round(read / (read + uncached), 4)
             if read is not None and uncached is not None and read + uncached else None)
    scope_sid, scope_rid = get_turn_scope()
    call = {
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cache_read": read, "cache_write": write, "cache_uncached": uncached,
        "cache_hit": read, "cache_miss": uncached,
        "cache_status": status, "cache_ratio": ratio,
        "cache_ratio_basis": "reported_read/(reported_read+reported_uncached)" if ratio is not None else None,
        "cache_sources": usage_fields.get("cache_sources") or {},
    }
    emit_encoded("context_usage", {
        "call": call, "total": total_tokens, "scope": "single_call",
        "observation_scope": "physical_attempt" if attempt_id else "logical_call",
        "source": source, "session_id": str(session_id or scope_sid),
        "request_id": str(request_id or scope_rid),
        "attempt_id": str(attempt_id or ""),
        "logical_call_id": str(logical_call_id or ""), "epoch": epoch,
        "parent_purpose": str(usage_fields.get("parent_purpose") or ""),
    })


# ═══════════════════════════════════════════════════════════════════════
# SSE Raw-Frame Debug Logger (prints every outgoing SSE chunk)
# ═══════════════════════════════════════════════════════════════════════

# v9.2: 删除恒 True 的开关 _sse_debug_enabled——守卫分支永不生效，
# log_sse_frame 随时可调用（debug 级别输出本身已是可关的开关）

def log_sse_frame(event_dict: dict) -> None:
    """Print an outgoing SSE frame for debugging purposes."""
    event_type = event_dict.get("event", "?")
    data_str = event_dict.get("data", "")
    preview = data_str[:200] if len(data_str) > 200 else data_str
    logger.debug(f"[SSE→] event={event_type} | data={preview}")


# ═══════════════════════════════════════════════════════════════════════
# Tool Execution Heartbeat / Progress tracker
# ═══════════════════════════════════════════════════════════════════════

_tool_start_times_var: contextvars.ContextVar = contextvars.ContextVar(
    "tool_start_times", default=None)

# A1（关键修复）: 工具计时必须是**进程级**的。
# 旧实现把计时表放在 ContextVar 里，而"工具进度心跳"是 SSE 端点另起的
# asyncio 任务——两者是不同的上下文副本，心跳永远读到空表，于是长时间
# 工具执行期间前端一个进度帧都收不到（实测 5 秒空档）。call_id 本身全局
# 唯一，按 request_id 归属即可安全共存。
_tool_timers: dict[str, tuple[float, str, str]] = {}


def _tool_start_times() -> dict[str, tuple[float, str]]:
    """Deprecated shim：保留旧调用方，返回当前请求可见的计时视图。"""
    return {cid: (ts, name) for cid, (ts, name, _rid) in _tool_timers.items()
            if not _scope_request_id() or _rid in ("", _scope_request_id())}


def _scope_request_id() -> str:
    try:
        return get_turn_scope()[1] or ""
    except Exception:
        return ""


def mark_tool_start(tool_call_id: str, tool_name: str = "") -> None:
    _tool_timers[str(tool_call_id)] = (time.perf_counter(), tool_name,
                                       _scope_request_id())


def mark_tool_end(tool_call_id: str) -> int:
    started = _tool_timers.pop(str(tool_call_id), None)
    return max(0, int((time.perf_counter() - started[0]) * 1000)) if started else 0


# v9.2: 删除 get_tool_elapsed——grep 证实全仓库无调用方（get_running_tools
# 已提供聚合视图，单个工具耗时可直接从 get_running_tools 结果推导）


def get_running_tools() -> list[tuple[str, str, float]]:
    """Returns list of (call_id, tool_name, elapsed_seconds)."""
    now = time.perf_counter()
    rid = _scope_request_id()
    return [(cid, name, now - ts)
            for cid, (ts, name, owner) in list(_tool_timers.items())
            if not rid or owner in ("", rid)]


def clear_tool_timers() -> None:
    rid = _scope_request_id()
    if not rid:
        _tool_timers.clear()
        return
    for cid in [c for c, (_ts, _n, owner) in _tool_timers.items()
                if owner in ("", rid)]:
        _tool_timers.pop(cid, None)


# ═══════════════════════════════════════════════════════════════════════
# Structured event emitters (canonical type constants)
# ═══════════════════════════════════════════════════════════════════════


def emit_thinking(content: str) -> None:
    """Compatibility no-op: private reasoning never becomes a progress narration."""
    return


def emit_reasoning(content: str) -> None:
    """Deprecated: private model reasoning must never be sent to clients."""
    return


def _tool_labels() -> dict[str, str]:
    """Labels come from the declarative catalog, never a second name list."""
    from src.core.tool_catalog import display_labels

    return display_labels()


# Compatibility alias for tests and callers; use ``_tool_labels()`` for new code.
_TOOL_LABELS = _tool_labels()


def tool_display_name(tool_name: str) -> str:
    """Return a stable user-facing capability label, never an internal name."""
    return _tool_labels().get(str(tool_name or ""), "科研工具")


def _safe_tool_summary(tool_name: str, args: dict | None = None,
                       output: str = "") -> str:
    """Return a concise action label without queries, paths, URLs, or results."""
    return tool_display_name(tool_name)


def tool_action_hint(tool_name: str, args: dict | None) -> str:
    """Compatibility signature: arguments are never a public action label."""
    return tool_display_name(tool_name)


_PUBLIC_WORK_NOTE = "正在调用工具"
_TOOL_STATUSES = {"complete", "partial", "no_results", "failed", "not_run", "skipped",
                  "cancelled", "unknown", "running", "resuming"}
_REASON_CLASSES = {"input_missing", "source_unavailable", "transient", "coverage_incomplete",
                   "scope_limited", "internal_error", "unknown"}
_PUBLIC_ARTIFACT_URL = re.compile(r"/api/v2/session/[A-Za-z0-9_-]+/artifacts/[a-f0-9]{32}")
_PUBLIC_REVIEW_URL = re.compile(r"/integrity-review\?review=[a-f0-9]{32}")
_PUBLIC_FACT_NUMBERS = frozenset({
    "n", "sample_size", "statistic", "p_value", "pvalue", "effect_size", "file_index", "rows",
    "columns", "total", "checked", "verified", "selected", "remaining", "missing", "duplicates",
    "completed", "processed", "pending", "inspected_files", "unreviewed_files", "accepted_artifacts",
    "findings_count",
})
_STATUS_SUMMARIES = {"waiting_code": "代码正在执行，完成后继续本轮任务。",
                     "code_cancel_pending": "正在停止代码执行，等待执行器确认。"}
_ERROR_MESSAGES = {
    "model_credential_unconfigured": "主模型尚未配置，请联系管理员配置模型后重试。附件仍可在资料库中查看。",
    "request_failed": "处理请求时发生内部错误，请稍后重试。",
    "attachment_rejected": "附件未通过检查，请检查文件后重试。",
    "attachment_busy": "附件处理队列繁忙，请稍后重试。",
    "attachment_timeout": "附件解析超过处理时限，请缩小文件或拆分后重试。",
    "attachment_failed": "附件处理失败，请检查文件格式后重试。",
    "queue_full": "服务器请求队列已满，请稍后重试。",
    "queue_timeout": "等待处理超时，请稍后重试。",
    "request_preparation_failed": "请求准备失败，请稍后重试。",
}


def _token(value, pattern=r"[A-Za-z0-9_-]{1,128}") -> str:
    return value if isinstance(value, str) and re.fullmatch(pattern, value) else ""


# Keep recognized protocol codes, not arbitrary single-token tool output.
_PROGRESS_REASONS = frozenset("""
    admission_failed answered artifact_partially_verified artifact_validation_failed artifact_validation_unavailable
    awaiting_input awaiting_user_input cancelled code_resume_admission_failed complete completed completion_proof_invalid
    completion_schema_invalid context_budget_exhausted dataset_unavailable deadline detached_timeout disabled empty_answer
    error evidence_read execution_blocker execution_pending execution_record_unavailable failed fields_unavailable
    finalization_budget history_load_failed incomplete internal invalid_evidence_read invalid_tool_arguments
    invalid_tool_protocol missing_result_status model_auth model_budget_exhausted model_network model_quota model_rate_limit
    model_step_limit model_timeout model_unavailable no_new_evidence not_run output_missing output_truncated partial
    preparation_required range_exhausted recovery_admission_failed request_deadline required_skill_not_loaded reviewed_completion
    reviewed_incomplete script_execution_cancelled script_execution_rejected script_reference_unavailable script_request_in_progress
    script_request_replayed service_restart some_material_unreviewed some_records_unverified stop_requested task_plan_updated
    tool_protocol_rejected tool_turn_limit user user_input_required verification_limit workspace_version_conflict
    write_requires_reconciliation browse_action_limit browse_bad_observation browse_bundle_integrity browse_click_timeout
    browse_download_type_unsupported browse_download_unavailable browse_expected_download_link browse_host_not_allowed
    browse_last_tab browse_network_error browse_network_timeout browse_not_authorized browse_observed browse_page_action_failed
    browse_page_unavailable browse_publication_digest_mismatch browse_queue_full browse_queue_timeout browse_renderer_disconnected
    browse_renderer_protocol browse_renderer_unready browse_retry_rejected browse_session_expired browse_stale_element
    browse_tab_id_required browse_tab_limit browse_tab_not_found browse_timed_out browse_unavailable browse_url_denied
""".split())


def public_tool_reason(value) -> str:
    from src.tools.outcome import _REASON_CLASS
    return value if isinstance(value, str) and (value in _PROGRESS_REASONS or value in _REASON_CLASS) else ""


def public_tool_status(value, default="failed") -> str:
    return value if isinstance(value, str) and value in _TOOL_STATUSES else default


def _public_tool_name(data: dict) -> dict:
    name = _token(data.get("tool_name"))
    labels = _tool_labels()
    if name in labels:
        return {"tool_name": name, "display_name": labels[name]}
    # Older start/heartbeat frames carry only the catalog label.
    label = data.get("display_name")
    return {"display_name": label if label in labels.values() else tool_display_name("")}


def _public_logical_key(value) -> str:
    if not isinstance(value, str) or not value:
        return ""
    if re.fullmatch(r"progress:[a-f0-9]{64}", value):
        return value
    # Some legacy producers used a truncated title or query as a grouping key.
    return "progress:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def public_result_refs(refs) -> list:
    """Keep existing typed references and host link shapes, never labels or paths."""
    result = []
    for ref in refs if isinstance(refs, (list, tuple)) else ():
        if isinstance(ref, str) and _PUBLIC_ARTIFACT_URL.fullmatch(ref):
            result.append(ref)
        if not isinstance(ref, dict) or (ref.get("internal") or ref.get("is_intermediate")
                or ref.get("formal_attachment") is False
                or str(ref.get("visibility", "")).lower() in {"internal", "private", "intermediate"}
                or any(str(ref.get(key, "")).lower() in {"internal", "intermediate", "working"}
                       for key in ("purpose", "role"))):
            continue
        kind, url = ref.get("kind"), ref.get("url") or ref.get("artifact_url")
        url = url if isinstance(url, str) else ""
        identity = {"reference_audit": "audit_id", "integrity_review": "review_id",
                    "dataset_snapshot": "id", "analysis_result": "id"}.get(str(kind))
        if kind == "artifact" and _PUBLIC_ARTIFACT_URL.fullmatch(url):
            item = {"kind": "artifact", "url": url}
            artifact_id = url.rsplit("/", 1)[-1]
            if ref.get("artifact_id") == artifact_id:
                item["artifact_id"] = artifact_id
            if ref.get("artifact_url") == url:
                item["artifact_url"] = url
            if file_id := _token(ref.get("file_id"), r"[a-f0-9]{32}"):
                item["file_id"] = file_id
                if ref.get("download_url") == f"/api/v2/files/{file_id}/download":
                    item["download_url"] = ref["download_url"]
        elif identity and (ref_id := _token(ref.get(identity), r"[a-f0-9]{32}")):
            item = {"kind": kind, identity: ref_id}
            if kind == "integrity_review" and _PUBLIC_REVIEW_URL.fullmatch(url) and url.endswith(ref_id):
                item["url"] = url
            if type(ref.get("version")) is int:
                item["version"] = ref["version"]
        else:
            continue
        if sha := _token(ref.get("sha256"), r"[a-f0-9]{64}"):
            item["sha256"] = sha
        result.append(item)
    return result


def public_tool_outcome(data: dict) -> dict:
    """Progress contains status, known numeric facts and references, not prose."""
    facts = data.get("facts")
    numeric_facts = {k: v for k, v in facts.items()
                     if k in _PUBLIC_FACT_NUMBERS and (type(v) is int or
                         (type(v) is float and math.isfinite(v)))} if isinstance(facts, dict) else {}
    status = public_tool_status(data.get("status"))
    reason_class = data.get("reason_class")
    result = {"status": status, "reason_code": public_tool_reason(data.get("reason_code")),
              "reason_class": reason_class if isinstance(reason_class, str) and reason_class in _REASON_CLASSES else "",
              "facts": numeric_facts}
    for key in ("result_refs", "artifacts"):
        if key in data:
            result[key] = public_result_refs(data[key])
    return result


def _fields(data: dict, *, tokens=(), numbers=()) -> dict:
    return {**{key: value for key in tokens if (value := _token(data.get(key)))},
            **{key: data[key] for key in numbers if type(data.get(key)) is int}}


def public_progress_payload(event: str, data: dict) -> dict:
    """Project progress events only. Chat, citations and explicit reads are separate."""
    cursor = _fields(data, numbers=("seq",))
    if event in {"tool_call_start", "tool_executing", "tool_progress", "tool_result", "operation"}:
        public = _fields(data, tokens=("tool_call_id", "operation_id", "retry_of"),
                         numbers=("attempt", "started_at_ms", "elapsed_ms", "completed_at_ms"))
        if "logical_key" in data:
            public["logical_key"] = _public_logical_key(data["logical_key"])
        if event != "operation" or "tool_name" in data or "display_name" in data:
            public.update(_public_tool_name(data))
        if event in {"tool_call_start", "tool_result"}:
            public["action_hint"] = public["display_name"]
        if event == "tool_result":
            public.update(public_tool_outcome(data))
        if event == "operation":
            public["status"] = public_tool_status(data.get("status"))
            if data.get("recovery") == "safe_step":
                public["recovery"] = "safe_step"
            if isinstance(data.get("outcome"), dict):
                public["outcome"] = public_tool_outcome(data["outcome"])
    elif event == "assistant_progress":
        public = _fields(data, tokens=("job_id", "note_id", "step_id"), numbers=("model_step",))
        # Note IDs include ':' in both graph and agent producers.
        if note_id := _token(data.get("note_id"), r"[A-Za-z0-9_:-]{1,160}"):
            public["note_id"] = note_id
        call_ids = data.get("tool_call_ids")
        public.update(visibility="public", origin="model", phase="working",
                      text=_PUBLIC_WORK_NOTE, chars=len(_PUBLIC_WORK_NOTE),
                      tool_call_ids=[cid for item in call_ids if (cid := _token(item))]
                      if isinstance(call_ids, (list, tuple)) else [])
    elif event == "task_update":
        public = _fields(data, numbers=("version",))
        if data.get("status") in ("pending", "applied", "not_applied"):
            public["status"] = data["status"]
        if data.get("reason") == "service_restart":
            public["reason"] = "service_restart"
    elif event == "agent_recovery":
        public = _fields(data, tokens=("job_id", "request_id", "status"), numbers=("previous_terminal_seq",))
        updates = data.get("reactivated_updates")
        if isinstance(updates, list):
            public["reactivated_updates"] = [{"version": item["version"]} for item in updates
                                             if isinstance(item, dict) and type(item.get("version")) is int]
    elif event in {"permission_request", "permission_decision"}:
        public = _fields(data, tokens=("approval_id", "job_id")) | _public_tool_name(data)
        if data.get("status") in ("pending", "granted", "rejected", "expired", "cancelled"):
            public["status"] = data["status"]
    elif event == "status":
        stage = data.get("stage")
        if stage not in ("waiting_code", "code_cancel_pending", "queued", "awaiting_input", "task_plan",
                         "preparing_attachments", "starting", "working"):
            stage = "working"
        public = {"stage": stage}
        if data.get("status") == "awaiting_input":
            public["status"] = "awaiting_input"
        if stage in _STATUS_SUMMARIES:
            public["summary"] = _STATUS_SUMMARIES[stage]
        if stage == "waiting_code" and data.get("reason_code") == "code_resume_admission_failed":
            public.update(reason_code="code_resume_admission_failed", summary="执行恢复暂未受理，稍后重试。")
        if stage == "queued":
            public.update(_fields(data, numbers=("position", "maximum")))
            position = public.get("position")
            public["summary"] = f"正在排队，当前第 {position} 位。" if position is not None else "正在排队。"
        if stage == "task_plan":
            public.update(_fields(data, tokens=("plan_id",)))
            steps = data.get("steps")
            public["steps"] = [{"id": _public_logical_key(step.get("id")),
                                "title": f"任务步骤 {index}", "status": step["status"]}
                               for index, step in enumerate(steps, 1)
                               if isinstance(step, dict) and step.get("status") in
                               ("pending", "in_progress", "completed", "blocked", "cancelled")
                               ] if isinstance(steps, list) else []
    elif event in {"task_state", "reasoning", "thinking", "draft", "plan"}:
        # Private host/model snapshots and legacy narration retain cursors only.
        public = _fields(data, numbers=("schema_version",)) if event == "task_state" else {}
    elif event == "tool_resolution":
        public = {"resolutions": public_tool_resolutions(data.get("resolutions"))}
    elif event == "agent_control":
        public = {"reason_code": public_tool_reason(data.get("reason_code"))}
    elif event == "context_projection":
        public = _fields(data, tokens=("event",), numbers=("archive_only_count", "before_tokens",
            "after_tokens", "trigger_tokens", "target_tokens", "protected_messages", "retained_messages"))
    elif event == "done":
        # This is also an authorized answer frame. Project the progress
        # envelope without touching its answer, citations or question.
        public = dict(data)
        if isinstance(data.get("turn_envelope"), dict):
            from src.core.task_events import _public_terminal
            public["turn_envelope"] = _public_terminal(data["turn_envelope"])
        if data.get("stop_notice"):
            from src.core.turn_notices import stopped_notice
            public["stop_notice"] = stopped_notice()
    elif event == "error":
        code = _token(data.get("code"))
        if code not in _ERROR_MESSAGES:
            # Legacy errors did not always have a code. Only exact known
            # messages may be recovered; no exception or path is echoed.
            code = next((key for key, message in _ERROR_MESSAGES.items()
                         if data.get("message") == message), "request_failed")
        public = {"code": code, "message": _ERROR_MESSAGES[code]}
        if data.get("turn_status") == "failed":
            public["turn_status"] = "failed"
        if isinstance(data.get("turn_envelope"), dict):
            from src.core.task_events import _public_terminal
            public["turn_envelope"] = _public_terminal(data["turn_envelope"])
    elif event == "reference_audit_progress":
        public = _fields(data, tokens=("audit_id", "status"), numbers=("record_index",))
        if data.get("durable") is True:
            public["durable"] = True
    elif event == "browse_phase":
        phase = data.get("phase")
        public = {"phase": phase} if phase in (
            "waiting", "navigating", "interacting", "downloading", "verifying", "organizing",
        ) else {}
    else:
        return data
    return public | cursor


def public_tool_resolutions(rows) -> list[dict]:
    return [item for row in rows if isinstance(row, dict)
            and row.get("resolution") in ("repaired", "source_replaced")
            and len(item := _fields(row, tokens=("tool_call_id", "resolved_by_tool_call_id", "resolution"))) == 3
            ] if isinstance(rows, (list, tuple)) else []


def emit_tool_call_start(tool_name: str, args: dict, tool_call_id: str = "",
                         logical_key: str = "", attempt: int = 1) -> None:
    """Emit only the friendly action label needed for the execution timeline."""
    emit_encoded("tool_call_start", {
        "tool_name": tool_name,
        "tool_call_id": str(tool_call_id or ""),
        # logical_key groups every attempt of the same logical step, so a
        # retried call updates one row instead of stacking new ones.
        "logical_key": str(logical_key or ""),
        "attempt": max(1, int(attempt or 1)),
        "started_at_ms": int(time.time() * 1000),
    })
    if tool_call_id:
        mark_tool_start(tool_call_id, tool_name)


def emit_tool_executing(message: str, tool_name: str = "", tool_call_id: str = "",
                        elapsed_ms: int = 0, logical_key: str = "",
                        attempt: int = 1) -> None:
    """Emit a friendly heartbeat without forwarding internal progress text."""
    emit_encoded("tool_executing", {
        "tool_name": tool_name,
        "tool_call_id": str(tool_call_id or ""),
        "logical_key": str(logical_key or ""),
        "attempt": max(1, int(attempt or 1)),
        "elapsed_ms": max(0, int(elapsed_ms or 0)),
    })


def emit_tool_result(tool_name: str, output: str, tool_call_id: str = "",
                     is_error: bool = False, summary: str = "",
                     outcome: dict | None = None, action_hint: str = "",
                     logical_key: str = "", attempt: int = 1) -> dict:
    """Emit a correlated, safe terminal state; full tool output stays in the Agent."""
    elapsed_ms = mark_tool_end(tool_call_id) if tool_call_id else 0
    outcome = outcome if isinstance(outcome, dict) else {}
    status = str(outcome.get("status") or ("failed" if is_error else "complete"))
    # Cancellation and unknown remain distinct terminal facts; neither becomes failed.
    status = public_tool_status(status) if status not in {"running", "resuming"} else "failed"
    reason_code = public_tool_reason(outcome.get("reason_code"))
    # This prose is retained only for authorized tool readback, never the queue
    # or its terminal journal (which also feeds the public stop notice).
    private_summary = " ".join(str(summary or "").split())[:180]
    result = {
        "display_name": tool_display_name(tool_name),
        "tool_name": str(tool_name or ""),
        "facts": outcome.get("facts") if isinstance(outcome.get("facts"), dict) else {},
        "result_refs": public_result_refs(outcome.get("result_refs") or outcome.get("artifacts")),
        "action_hint": tool_display_name(tool_name),
        "tool_call_id": str(tool_call_id or ""),
        "logical_key": str(logical_key or ""),
        "attempt": max(1, int(attempt or 1)),
        "status": status,
        "reason_code": reason_code,
        "reason_class": str(outcome.get("reason_class") or "")[:32],
        "elapsed_ms": elapsed_ms,
        "completed_at_ms": int(time.time() * 1000),
    }
    emit_encoded("tool_result", result)
    journal = _current_tool_journal.get()
    if isinstance(journal, list):
        journal.append({"name": str(tool_name or ""), "status": status,
                        "reason": reason_code, "elapsed_ms": int(elapsed_ms or 0),
                        "tool_call_id": str(tool_call_id or "")})
    return result | {"summary": private_summary}


def emit_text(content: str) -> None:
    """Emit a FINAL_ANSWER text chunk."""
    emit_encoded("text", {"content": content})


def emit_answer_start() -> None:
    """Mark the start of the final answer stream.

    Tool rounds may produce public working notes, but those notes belong to the
    progress timeline. Clients render streamed ``text`` only after this event.
    """
    emit_encoded("answer_start", {})


def emit_text_reset(reason: str = "") -> None:
    """作废前端已收到的临时正文（流式增量只对最终回答有效）。

    模型在"工具轮"里也会产出 content，但那不是回答；这些增量可能已经
    随流式通道上屏。轮次结束时一旦确认是工具轮，就发这个事件让前端清空
    临时文本，再由 ``assistant_progress`` 把它作为工作说明写进时间线。
    """
    emit_encoded("text_reset", {"reason": reason})


def emit_status(stage: str, **kwargs) -> None:
    """Keep real control state; retired host narration never crosses the bus."""
    if stage in {"agent_progress", "step_active", "step_done", "loading", "thinking",
                 "understanding", "preparing_context", "context_injection"}:
        return
    payload = {"stage": stage}
    payload.update(kwargs)
    emit_encoded("status", payload)

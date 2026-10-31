"""Progress bus — shared between graph nodes and SSE generator.

v8.3.3: request-scoped progress queue (contextvars, 消除跨请求串扰) +
unified JSON-string data format + usage delta emitter + heartbeat/progress
tracking for long tool executions + SSE raw-frame debug logging.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import time
from typing import Optional

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


def set_turn_scope(session_id: str, request_id: str = "") -> None:
    _current_turn_scope.set((str(session_id or ""), str(request_id or "")))


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


def emit_progress(event_type: str, data: dict) -> None:
    """Push a progress event to the current request's SSE queue (non-blocking)."""
    q = get_progress_queue()
    try:
        q.put_nowait(_encode_event(event_type, data))
    except asyncio.QueueFull:
        pass


def emit_encoded(event_type: str, data: dict) -> None:
    """Alias for emit_progress — canonical event emission."""
    emit_progress(event_type, data)


# ═══════════════════════════════════════════════════════════════════════
# Per-call provider usage events
# ═══════════════════════════════════════════════════════════════════════


def emit_usage_delta(session_id: str, source: str, input_tokens: int = 0,
                     output_tokens: int = 0, total: int = 0,
                     cache_hit: int = 0, cache_miss: int = 0,
                     cache_status: str = "unavailable") -> None:
    """Emit one independent event per provider call; never subtract adjacent calls."""
    if not (input_tokens or output_tokens or total):
        return
    total = int(total or (input_tokens + output_tokens))
    cache_denom = cache_hit + cache_miss
    emit_encoded("context_usage", {
        "call": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_hit": cache_hit,
            "cache_miss": cache_miss,
            "cache_status": cache_status,
            "cache_ratio": round(cache_hit / cache_denom, 4)
            if cache_status == "reported" and cache_denom else None,
        },
        "total": total,
        "scope": "single_call",
        "source": source,
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
    """Emit a safe progress label; never forward model-generated reasoning text."""
    emit_status("agent_progress", message="正在规划下一步工具操作…")


def emit_reasoning(content: str) -> None:
    """Deprecated: private model reasoning must never be sent to clients."""
    return


_TOOL_LABELS = {
    "deepseek_search": "网页搜索",
    "deepseek_fetch": "读取网页",
    "deepseek_fetch_paper": "获取论文全文",
    "local_bm25_search": "本地检索",
    "local_dense_search": "本地检索",
    "local_hybrid_search": "本地检索",
    "draw_svg": "绘图",
    "citrus_rag_search": "本地检索",
    "pdf_read": "读取 PDF",
    "read_local_file": "读取文件",
    "write_local_file": "写入文件",
    "statistical_analysis": "统计分析",
    "experimental_design": "实验设计",
    "inspect_uploaded_table": "读取表格",
    "audit_uploaded_data": "数据审查",
    "verify_uploaded_references": "文献核验",
    "fetch_and_audit_open_supplements": "获取补充数据",
    "export_literature_evidence": "导出文献证据",
    "export_paper_analysis": "导出论文解读",
    "export_experiment_design": "导出实验方案",
    "save_research_note": "保存课题记录",
    "recall_research_notes": "查阅课题记录",
    "export_research_notes": "导出课题记录",
    "export_corpus_gap_report": "导出语料清单",
    "call_write_agent": "写作",
    "call_analyze_agent": "分析",
}


def tool_display_name(tool_name: str) -> str:
    """Return a stable user-facing capability label, never an internal name."""
    return _TOOL_LABELS.get(str(tool_name or ""), "科研工具")


def _safe_tool_summary(tool_name: str, args: dict | None = None,
                       output: str = "") -> str:
    """Return a concise action label without queries, paths, URLs, or results."""
    return tool_display_name(tool_name)


_HINT_MAX = 56


def tool_action_hint(tool_name: str, args: dict | None) -> str:
    """One bounded line describing WHAT the tool was asked to do.

    The timeline must answer "调用了哪个工具、做了什么", not only "已经完成"。
    只取用户可见的查询词/类别/文件名等字段，去掉 URL 与服务器路径。
    """
    data = args if isinstance(args, dict) else {}
    url = str(data.get("url") or "").strip()
    if url:
        # 只显示站点名：既回答“读的是哪个来源”，又不把带 token 的完整链接落库
        host = url.split("://", 1)[-1].split("/", 1)[0]
        return ("读取 " + host)[:80]
    keys = ("query", "q", "question", "keyword", "keywords", "corpus_types",
            "filename", "file", "name", "title", "doi", "topic", "kind")
    parts: list[str] = []
    for key in keys:
        value = data.get(key)
        if isinstance(value, (list, tuple)):
            value = "、".join(str(v) for v in value if str(v).strip())
        if value is None:
            continue
        item = " ".join(str(value).split())
        if item:
            parts.append(item)
        if len(parts) >= 2:
            break
    hint = "；".join(parts)
    hint = re.sub(r"https?://\S+", "", hint)
    hint = re.sub(r"(?:[A-Za-z]:\\|/home/|/tmp/|/mnt/)[^\s;，。]+", "", hint)
    hint = " ".join(hint.split())
    if len(hint) > _HINT_MAX:
        cut = hint[:_HINT_MAX]
        # 截断落在词边界上，避免出现 "...control me" 这种半截词
        hint = (cut.rsplit(" ", 1)[0] if " " in cut else cut) + "…"
    return hint


def emit_tool_call_start(tool_name: str, args: dict, tool_call_id: str = "") -> None:
    """Emit only the friendly action label needed for the execution timeline."""
    display_name = tool_display_name(tool_name)
    emit_encoded("tool_call_start", {
        "display_name": display_name,
        "action_hint": tool_action_hint(tool_name, args),
        "tool_call_id": str(tool_call_id or ""),
        "started_at_ms": int(time.time() * 1000),
    })
    if tool_call_id:
        mark_tool_start(tool_call_id, tool_name)


def emit_tool_executing(message: str, tool_name: str = "", tool_call_id: str = "",
                        elapsed_ms: int = 0) -> None:
    """Emit a friendly heartbeat without forwarding internal progress text."""
    display_name = tool_display_name(tool_name)
    emit_encoded("tool_executing", {
        "display_name": display_name,
        "tool_call_id": str(tool_call_id or ""),
        "elapsed_ms": max(0, int(elapsed_ms or 0)),
    })


def emit_tool_result(tool_name: str, output: str, tool_call_id: str = "",
                     is_error: bool = False, summary: str = "",
                     outcome: dict | None = None, action_hint: str = "") -> dict:
    """Emit a correlated, safe terminal state; full tool output stays in the Agent."""
    elapsed_ms = mark_tool_end(tool_call_id) if tool_call_id else 0
    outcome = outcome if isinstance(outcome, dict) else {}
    status = str(outcome.get("status") or ("failed" if is_error else "complete"))
    # S1: cancelled 是合法终态（用户停止），不能降级成 failed。
    if status not in {"complete", "partial", "no_results", "failed", "not_run",
                      "skipped", "cancelled"}:
        status = "failed"
    reason_code = str(outcome.get("reason_code") or "")[:64]
    safe_summary = str(summary or "")
    # Summaries are produced by our deterministic adapters. Strip URL and path
    # fragments defensively before they cross the client boundary.
    safe_summary = re.sub(r"https?://\S+", "", safe_summary)
    safe_summary = re.sub(r"(?:[A-Za-z]:\\|/home/|/tmp/)[^\s;，。]+", "", safe_summary)
    safe_summary = " ".join(safe_summary.split())[:180]
    result = {
        "display_name": tool_display_name(tool_name),
        "action_hint": re.sub(r"\s+", " ", str(action_hint or ""))[:80],
        "tool_call_id": str(tool_call_id or ""),
        "status": status,
        "reason_code": reason_code,
        "summary": safe_summary,
        "elapsed_ms": elapsed_ms,
        "completed_at_ms": int(time.time() * 1000),
    }
    emit_encoded("tool_result", result)
    journal = _current_tool_journal.get()
    if isinstance(journal, list):
        journal.append({"name": str(tool_name or ""), "status": status,
                        "reason": reason_code, "summary": safe_summary,
                        "elapsed_ms": int(elapsed_ms or 0),
                        "tool_call_id": str(tool_call_id or "")})
    # Internal caller uses this same sanitized record for durable history.
    return result


def emit_text(content: str) -> None:
    """Emit a FINAL_ANSWER text chunk."""
    emit_encoded("text", {"content": content})


def emit_text_reset(reason: str = "") -> None:
    """作废前端已收到的临时正文（流式增量只对最终回答有效）。

    模型在"工具轮"里也会产出 content，但那不是回答；这些增量可能已经
    随流式通道上屏。轮次结束时一旦确认是工具轮，就发这个事件让前端清空
    临时文本，再由 ``assistant_progress`` 把它作为工作说明写进时间线。
    """
    emit_encoded("text_reset", {"reason": reason})


def emit_status(stage: str, **kwargs) -> None:
    """Emit a generic status event (backward compat)."""
    payload = {"stage": stage}
    payload.update(kwargs)
    emit_encoded("status", payload)

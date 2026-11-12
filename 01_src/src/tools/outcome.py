"""Small, provider-agnostic status envelope for tool results."""
from __future__ import annotations

import json

from dataclasses import asdict, dataclass
import re
from typing import Any


@dataclass(frozen=True)
class ToolOutcome:
    status: str
    reason_code: str = ""
    coverage: str = ""
    artifacts: tuple[str, ...] = ()
    # input_missing | source_unavailable | transient | internal_error | ""
    reason_class: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_FAILURE_PREFIXES = (
    "[TOOL_ERROR]", "[WEB_TOOL_ERROR]", "[WEB_FETCH_ERROR]", "[WEB_FETCH_FAILED]",
    "[WEB_DISABLED]", "[WEB_FETCH_BLOCKED]", "[LOCAL_SEARCH_ERROR]",
    "[LOCAL_HYBRID_ERROR]", "[LOCAL_HYBRID_TRANSLATION_ERROR]",
    "[LOCAL_SEARCH_TRANSLATION_ERROR]", "[DELIVERABLE_REJECTED", "[ERR_",
)

_KNOWN_STATUSES = {"complete", "partial", "no_results", "failed", "not_run", "skipped", "cancelled"}

# Reason codes grouped by what the *user* should take away from them. Only the
# third group (internal_error) is our own defect; the first two are normal
# conditions of a research turn (missing input, unreachable source).
_REASON_CLASS = {
    "no_selected_attachment": "input_missing",
    "no_processable_table": "input_missing",
    "no_reference_identifiers": "input_missing",
    "no_open_archive": "input_missing",
    "empty_page": "input_missing",
    "no_matching_corpus": "input_missing",
    "blocked_destination": "source_unavailable",
    "source_unavailable": "source_unavailable",
    "network": "source_unavailable",
    "timeout": "transient",
    "dependency": "transient",
    "quota": "transient",
    "auth": "internal_error",
    "tool_error": "internal_error",
    "unrecognized_tool_outcome": "internal_error",
    "invalid_arguments": "internal_error",
    "invalid_structured_outcome": "internal_error",
    "invalid_audit_status": "internal_error",
    "status_unavailable": "internal_error",
}


def reason_class_for(reason_code: str, status: str) -> str:
    """Map a technical reason code to a user-facing failure class."""
    reason = str(reason_code or "").strip()
    if not reason:
        return "" if status in {"complete", "partial", "no_results", "not_run",
                                "skipped", "cancelled"} and status != "failed" else "internal_error"
    return _REASON_CLASS.get(reason, "internal_error")

# These tools report success through a deterministic success template and raise
# or return [ERR_*] on failure. Their status is still explicit by tool name; an
# arbitrary unknown string from any other tool never becomes success.
_TEXT_SUCCESS_TOOLS = frozenset({
    "draw_svg",
    "read_account_file",
    "inspect_uploaded_table",
    "save_research_note",
    "recall_research_notes",
    "export_literature_evidence",
    "export_paper_analysis",
    "export_experiment_design",
    "export_research_notes",
    "export_corpus_gap_report",
    "export_table",
})


# Failures that must never be hidden behind a retry: quota, credentials,
# product validation, and an unreadable state are all permanent for this turn.
_NON_RETRYABLE_HINTS = re.compile(
    r"\[ERR_(?:QUOTA|CREDITS_EXHAUSTED|AUTH|PRODUCT|ARTIFACT|DELIVERABLE)[^\]]*\]"
    r"|\[DELIVERABLE_REJECTED[^\]]*\]|\[TOOL_UNAVAILABLE\]|status_unavailable", re.I)


def safe_tool_step(tool_name: str, content: str, artifact: dict | None = None,
                   *, status_override: str = "", reason_override: str = "") -> dict[str, Any]:
    """Make a small user-visible execution record without exposing tool payloads.

    This is intentionally limited to status, a stable reason code, and summaries
    derived from structured outcome metadata. Raw arguments and tool output never
    cross the progress/history API boundary.
    """
    outcome = normalize_tool_outcome(tool_name, content, artifact)
    status = status_override or outcome.status
    reason = reason_override or outcome.reason_code
    if status not in _KNOWN_STATUSES:
        status = "failed"
        reason = reason or "unknown_status"

    data = artifact if isinstance(artifact, dict) else {}
    summary = ""
    if status == "cancelled":
        # S1: 停止不是失败，措辞也不能带"失败/错误"。
        summary = "已停止，这一步的结果未采用"
    if tool_name in {"local_bm25_search", "local_dense_search", "local_hybrid_search"}:
        rows = data.get("main_results")
        count = len(rows) if isinstance(rows, list) else None
        if count is None:
            match = re.search(r"读取\s*(\d+)\s*条去重候选", str(content or ""))
            if match:
                count = int(match.group(1))
        if count is not None:
            summary = f"找到 {count} 条候选资料" if count else "没有找到候选资料"
        elif status == "failed":
            summary = "本地资料检索未完成"
    elif tool_name in {"deepseek_search", "deepseek_fetch", "deepseek_fetch_paper"}:
        rows = data.get("web_results")
        count = len(rows) if isinstance(rows, list) else None
        if count is None:
            match = re.search(r"(\d+)\s*条联网结果", str(content or ""))
            if match:
                count = int(match.group(1))
        if count is not None:
            summary = f"获得 {count} 条网页资料" if count else "没有找到网页资料"
    elif tool_name == "audit_uploaded_data" and status in {"complete", "partial"}:
        summary = "数据检查报告已生成" if outcome.artifacts else "数据检查已完成"
    elif tool_name == "inspect_uploaded_table" and status == "complete":
        summary = "表格结构已读取"
    elif outcome.coverage:
        # Coverage is generated by deterministic adapters, not copied from the
        # provider's arbitrary prose.
        summary = str(outcome.coverage)[:160]
    elif outcome.artifacts:
        summary = "已生成可查看的文件"

    reason_class = outcome.reason_class or reason_class_for(reason, status)
    retryable = bool(
        status == "failed" and reason in {
            "tool_error", "unrecognized_tool_outcome", "invalid_arguments",
            "invalid_structured_outcome", "invalid_audit_status",
        } and not _NON_RETRYABLE_HINTS.search(str(content or "")[:400]))
    return {"status": status, "reason_code": reason, "summary": summary,
            "reason_class": reason_class, "retryable": retryable,
            "artifacts": list(outcome.artifacts)}


def _normalize_tool_outcome(tool_name: str, content: str, artifact: dict | None = None) -> ToolOutcome:
    """Normalize explicit machine signals; never infer success from no-hit prose."""
    text = str(content or "").strip()
    data = artifact if isinstance(artifact, dict) else {}
    links = tuple(dict.fromkeys(re.findall(
        r"\[[^\]]+\]\((/api/v2/session/[A-Za-z0-9_-]+/artifacts/[a-f0-9]{32})\)", text)))
    explicit = data.get("_tool_outcome")
    if isinstance(explicit, dict):
        status = str(explicit.get("status") or "").strip()
        if status not in _KNOWN_STATUSES:
            return ToolOutcome("failed", "invalid_structured_outcome", artifacts=links)
        return ToolOutcome(
            status=status,
            reason_code=str(explicit.get("reason_code") or ""),
            coverage=str(explicit.get("coverage") or ""),
            artifacts=tuple(
                str(x) for x in (
                    explicit.get("artifacts")
                    if "artifacts" in explicit else links
                )
            ),
        )

    if text.startswith("[TOOL_DUPLICATE_SKIPPED]"):
        return ToolOutcome("skipped", "duplicate_request", artifacts=links)
    if text.startswith("[TOOL_CANCELLED]"):
        # S1: 用户停止导致的结果未采用。**不是失败**——不计入失败率，也不
        # 触发"换个说法重试"，但必须如实标成"已停止"。
        return ToolOutcome("cancelled", "stop_requested", artifacts=links)
    if text.startswith("[TOOL_BATCH_LIMIT]"):
        return ToolOutcome("skipped", "tool_batch_limit", artifacts=links)
    if text.startswith("[TOOL_UNAVAILABLE]"):
        return ToolOutcome("failed", "tool_unavailable", artifacts=links)
    if text.startswith("[DELIVERABLE_REJECTED"):
        return ToolOutcome("failed", "deliverable_rejected", artifacts=links,
                           reason_class="internal_error")

    if text.startswith(("[SVG_INPUT_MISSING]", "[SVG_FORMAT_ERROR]", "[SVG_ATTEMPT_LIMIT]")):
        reason = "svg_attempt_limit" if text.startswith("[SVG_ATTEMPT_LIMIT]") else "invalid_arguments"
        return ToolOutcome("failed", reason, artifacts=links)

    if text.startswith("[ERR_REQUEST_DEADLINE]") or text.startswith("[ERR_TIMEOUT]"):
        # Running out of the turn budget is not a defect of the tool: the step
        # simply did not finish. Report it as an incomplete step so the turn
        # stays honest without painting a red failure.
        return ToolOutcome("partial", "timeout", artifacts=links)
    if text.startswith("[WEB_DISABLED]"):
        return ToolOutcome("not_run", "disabled", artifacts=links)
    if text.startswith("[WEB_FETCH_BLOCKED]"):
        return ToolOutcome("not_run", "blocked_destination", artifacts=links)
    if text.startswith("[WEB_FETCH_EMPTY]"):
        return ToolOutcome("no_results", "empty_page", artifacts=links)
    if text.startswith("[LOCAL_HYBRID_TRANSLATION_ERROR]") or text.startswith("[LOCAL_SEARCH_TRANSLATION_ERROR]"):
        return ToolOutcome("not_run", "translation_failed", artifacts=links)
    if text.startswith("[LOCAL_HYBRID_SKIPPED]"):
        return ToolOutcome("not_run", "no_matching_corpus", artifacts=links)
    if text.startswith("[ERR_QUOTA]") or text.startswith("[ERR_CREDITS_EXHAUSTED]"):
        return ToolOutcome("failed", "quota", artifacts=links)
    if text.startswith(_FAILURE_PREFIXES):
        lowered = text[:500].lower()
        reason = "tool_error"
        if ("not configured" in lowered or "not been configured" in lowered
                or "未配置" in lowered or "未設定" in lowered):
            return ToolOutcome("not_run", "configuration_missing", artifacts=links)
        if "quota" in lowered or "credit" in lowered or "额度" in lowered or "余额" in lowered:
            reason = "quota"
        elif "401" in lowered or "403" in lowered or "unauthorized" in lowered or "认证" in lowered:
            reason = "auth"
        elif "timeout" in lowered or "timed out" in lowered or "超时" in lowered:
            reason = "timeout"
        elif "network" in lowered or "connect" in lowered or "网络" in lowered:
            reason = "network"
        elif "MISSING_DEP" in text[:40]:
            reason = "dependency"
        elif re.search(r"\b(404|410|429|500|502|503|504)\b", lowered):
            reason = "source_unavailable"
        if reason in {"timeout", "network", "dependency", "source_unavailable"}:
            # A remote page or API that 404s / 5xx-es / times out is a source
            # condition, not a failed task; keep whatever the turn has.
            status = "partial" if tool_name.startswith("deepseek_") else "failed"
            return ToolOutcome(status, "source_unavailable", artifacts=links)
        if tool_name == "deepseek_fetch_paper" and (
                "全文" in text or "full text" in lowered or "verifiable" in lowered):
            return ToolOutcome("partial", "source_unavailable", artifacts=links)
        return ToolOutcome("failed", reason, artifacts=links)

    if tool_name == "audit_uploaded_data":
        structured_status = str(data.get("audit_status") or "").strip()
        if structured_status:
            if structured_status not in _KNOWN_STATUSES:
                return ToolOutcome("failed", "invalid_audit_status", artifacts=links)
            coverage = str(data.get("coverage_summary") or "")
            return ToolOutcome(structured_status,
                               str(data.get("reason_code") or ""),
                               coverage, links)
        if text.startswith("审查未启动"):
            return ToolOutcome("not_run", "no_selected_attachment", artifacts=links)
        if text.startswith("数据审查未完整完成"):
            return ToolOutcome("partial", "coverage_incomplete", "未完成范围见报告", links)
        if text.startswith("数据审查已完成"):
            return ToolOutcome("complete", coverage="按报告注明范围完成", artifacts=links)
        if text.startswith("未生成审查文件"):
            reason = ("no_selected_attachment" if "没有选择附件" in text
                      else "no_processable_table")
            return ToolOutcome("not_run", reason, artifacts=links)
        if text.startswith("完成 ") and "个工作表的审查" in text:
            partial = "未审查" in text or "未完整" in text
            return ToolOutcome("partial" if partial else "complete",
                               "coverage_incomplete" if partial else "",
                               "按报告注明范围完成", links)
        if text.startswith("{"):
            try:
                payload = json.loads(text)
            except Exception:
                payload = {}
            status = str(payload.get("status") or "")
            if status in {"failed", "partial", "complete"}:
                return ToolOutcome(status, "structured_audit_result", artifacts=links)

    if tool_name == "verify_uploaded_references":
        # 成功路径：一次调用内部已经分好批，返回「已处理 N 条文献，分 k 批：…」。
        # 旧逻辑只认 unverified/失败关键词，成功且 0 条 unverified 时会掉到
        # 末尾的 unrecognized_tool_outcome，被编排层判成失败并触发重试。
        handled = re.search(r"已处理\s+(\d+)\s+条文献", text)
        if handled:
            summary_text = text.split("。", 1)[0].split("\n", 1)[0]
            done = re.findall(
                r"(?:metadata_matched|metadata_mismatch|title_candidate|web_candidate"
                r"|not_found_in_checked_registries)\s+(\d+)\s*条", summary_text)
            problems = re.findall(r"(?:network_error|not_checked)\s+(\d+)\s*条", summary_text)
            matched = sum(int(count) for count in done)
            if any(int(count) for count in problems):
                return ToolOutcome("partial" if matched else "failed",
                                   "some_records_unverified", artifacts=links)
            if matched:
                return ToolOutcome("complete", coverage=f"已核验 {matched} 条文献身份",
                                   artifacts=links)
            return ToolOutcome("partial", "some_records_unverified", artifacts=links)
        if "未自动提取到 DOI" in text or "当前请求没有可提取的来源" in text:
            return ToolOutcome("not_run", "no_reference_identifiers", artifacts=links)
        if "文本提取失败" in text or "联网失败" in text:
            # A mixed verification can contain successful rows alongside failures.
            match = re.search(r"(?:metadata_matched|metadata_mismatch|title_candidate|not_found_in_checked_registries)\s+(\d+)\s*条", text)
            return ToolOutcome("partial" if match and int(match.group(1)) else "failed",
                               "network" if not match or not int(match.group(1)) else "some_records_unverified",
                               artifacts=links)
        match = re.search(r"unverified\s+(\d+)\s*条", text, re.I)
        if match and int(match.group(1)):
            completed = re.search(r"(?:metadata_matched|metadata_mismatch|title_candidate|not_found_in_checked_registries)\s+(\d+)\s*条", text, re.I)
            return ToolOutcome("partial" if completed and int(completed.group(1)) else "failed",
                               "some_records_unverified" if completed and int(completed.group(1)) else "network",
                               artifacts=links)
        if "not_checked" in text:
            return ToolOutcome("partial", "verification_limit", artifacts=links)
        # 工具给出了明确的核验结论（例如“登记库未查到记录”），没有任何执行
        # 失败标记时，这属于“做完了”，不是“没做成”。旧逻辑会落到末尾的
        # unrecognized_tool_outcome。
        return ToolOutcome("partial", "unconfirmed_verification_status", artifacts=links)

    if tool_name == "search_literature":
        # 元能力：返回结构化候选（candidates/errors）。有候选即为完成；
        # 部分来源失败时降级为 partial，零候选且有失败时是 no_results。
        payload = data if isinstance(data, dict) else {}
        if "candidates" not in payload and text.strip().startswith("{"):
            try:
                parsed = json.loads(text)
                payload = parsed if isinstance(parsed, dict) else {}
            except ValueError:
                payload = {}
        candidates = payload.get("candidates")
        if isinstance(candidates, list):
            errors = payload.get("errors") or {}
            failed_sources = len(errors) if isinstance(errors, dict) else 0
            if candidates:
                return ToolOutcome("partial" if failed_sources else "complete", "",
                                   f"{len(candidates)} 条文献候选", links)
            return ToolOutcome("no_results",
                               "some_sources_failed" if failed_sources else "no_candidates")
        if text.strip() and not any(mark in text for mark in ("未完成", "失败", "超时")):
            return ToolOutcome("complete", artifacts=links)

    if tool_name == "fetch_and_audit_open_supplements":
        if text.startswith("公开补充材料获取未完成") or "归档解析失败" in text:
            return ToolOutcome("failed", "network_or_archive_error", artifacts=links)
        if "没有可下载归档" in text or "不在可信 PMC OA 主机白名单" in text:
            return ToolOutcome("no_results" if "没有可下载归档" in text else "not_run",
                               "no_open_archive" if "没有可下载归档" in text else "blocked_source",
                               artifacts=links)
        if "未能审查" in text or "没有识别到可审查的表格材料" in text:
            return ToolOutcome("partial", "some_material_unreviewed", artifacts=links)

    if tool_name == "local_hybrid_search":
        local_status = str(data.get("local_status") or "")
        if not local_status:
            return ToolOutcome("failed", "missing_result_status", artifacts=links)
        if local_status == "translation_error":
            return ToolOutcome("not_run", "translation_failed", artifacts=links)
        if local_status == "skipped_no_matching_family":
            return ToolOutcome("not_run", "no_matching_corpus")
        if local_status == "hit":
            count = len(data.get("main_results") or [])
            partial = bool(data.get("errors") or data.get("reranker_fallback"))
            return ToolOutcome("partial" if partial else ("complete" if count else "no_results"),
                               "" if count else "no_candidates",
                               f"{count} 条本地候选")
        if local_status == "zero_hit":
            errors = data.get("errors") or {}
            return ToolOutcome("partial" if errors else "no_results",
                               "retrieval_channel_error" if errors else "no_candidates",
                               "零候选；有通道失败" if errors else "零候选")
        if local_status in {"error", "failed"}:
            return ToolOutcome("failed", "local_search_error")

    if tool_name in {"deepseek_search", "deepseek_fetch", "deepseek_fetch_paper"}:
        if tool_name == "deepseek_fetch_paper":
            fetch_status = str(data.get("fetch_status") or "")
            if fetch_status == "full_text":
                return ToolOutcome("complete", coverage="可验证全文")
            if fetch_status == "abstract_only":
                return ToolOutcome("partial", "abstract_only",
                                   "仅取得摘要或部分正文", links)
            if fetch_status == "failed":
                return ToolOutcome("failed", "full_text_unavailable")
        rows = data.get("web_results")
        if isinstance(rows, list) and not rows:
            return ToolOutcome("no_results", "no_web_results", artifacts=links)
        if isinstance(rows, list):
            return ToolOutcome("complete" if rows else "no_results",
                               "" if rows else "no_web_results",
                               f"{len(rows)} 条联网结果", links)

    if tool_name in _TEXT_SUCCESS_TOOLS and text:
        return ToolOutcome("complete", artifacts=links)
    return ToolOutcome("failed", "unrecognized_tool_outcome", artifacts=links)


def normalize_tool_outcome(tool_name: str, content: str,
                           artifact: dict | None = None) -> ToolOutcome:
    """Return the normalized outcome plus its user-facing failure class."""
    base = _normalize_tool_outcome(tool_name, content, artifact)
    cls = reason_class_for(base.reason_code, base.status)
    if cls == base.reason_class:
        return base
    return ToolOutcome(base.status, base.reason_code, base.coverage,
                       base.artifacts, cls)

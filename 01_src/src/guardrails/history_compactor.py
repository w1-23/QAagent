"""Incremental history summaries with explicit, fail-closed completion status."""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re

logger = logging.getLogger(__name__)
_CITATION_PATTERN = re.compile(r'\[(?:W|H)?\d+\]', re.IGNORECASE)
_IDENTIFIER_PATTERN = re.compile(
    r"\b(?:doi|evidence_id|artifact_id|chunk_id|source_id|pmid|url)\b\s*[:=]\s*[^\s,;]+"
    r"|\b10\.\d{4,9}/[^\s,;]+|https?://[^\s<>]+", re.IGNORECASE)
_MAX_INPUT_CHARS = 24000
_REF_KEYS = frozenset({"doi", "evidence_id", "artifact_id", "chunk_id", "source_id",
                       "pmid", "url", "file_id", "analysis_result_ref", "dataset_ref",
                       "result_ref", "result_refs", "output_path", "path", "file_path",
                       "tool_call_id", "sha256", "source_sha256", "file_hash", "figure_ref",
                       "figure_id", "script_ref", "run_id", "skill_id", "resource"})


def _structured_refs(value):
    refs = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _REF_KEYS:
                refs.append({key: item})
            else:
                refs.extend(_structured_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.extend(_structured_refs(item))
    return refs


@dataclass
class CompactionResult:
    status: str  # success / degraded / failed
    summary: str = ""
    finish_reason: str = ""
    complete: bool = False
    reason: str = ""


def message_text(message) -> str:
    """Text blocks share one representation; image payloads are never stringified."""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block if isinstance(block, str) else str(block.get("text", ""))
                         for block in content if isinstance(block, (str, dict)))
    return str(content or "")


def _messages_to_text(messages: list, max_per_msg=None) -> str:
    # max_per_msg remains accepted for old callers, but never clips user input.
    lines = []
    for message in messages:
        meta = getattr(message, "additional_kwargs", {}) or {}
        lines.append(json.dumps({
            "role": getattr(message, "type", ""),
            "source_id": meta.get("source_id") or getattr(message, "id", None) or meta.get("turn_id") or meta.get("request_id"),
            "origin": meta.get("context_origin", "unknown") if meta.get("context_trusted") is True else "unknown",
            "text": message_text(message),
            "user_text": meta.get("user_text") if meta.get("context_trusted") is True else None,
            "attachment_refs": meta.get("attachment_refs", []) if meta.get("context_trusted") is True else [],
        }, ensure_ascii=False))
    return "\n".join(lines)


def _prior_capsule(summary):
    # Only our versioned trailing capsule is mergeable. Unknown/legacy text is
    # retained verbatim; a guessed parse must not erase requirements.
    marker = "<protected_context>\n"
    start = summary.rfind(marker)
    if start < 0 or not summary.endswith("\n</protected_context>"):
        return None
    try:
        capsule = json.loads(summary[start + len(marker):-len("\n</protected_context>")])
    except (TypeError, ValueError):
        return None
    return capsule if isinstance(capsule, dict) and capsule.get("version") == 2 else None


def _unique(items):
    seen, output = set(), []
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            output.append(item)
    return output


def protect_summary(summary: str, messages: list, prior_summary: str = "",
                    working_state: dict | None = None) -> str:
    """Merge deterministic requirements and factual anchors across checkpoints.

    The model replaces only its narrative. Source requirements, tool identities,
    bounded findings and caveats survive independently, without recursively
    nesting every earlier narrative and capsule.
    """
    from src.core.model_context import project_tool_message
    prior = _prior_capsule(prior_summary) or {}
    protected = list(prior.get("user_requirements", []))
    refs = list(prior.get("reference_anchors", []))
    tool_sources = list(prior.get("tool_sources", []))
    facts = list(prior.get("tool_facts", []))
    for message in messages:
        meta = getattr(message, "additional_kwargs", {}) or {}
        text = message_text(message)
        source_id = meta.get("source_id") or getattr(message, "id", None) or meta.get("turn_id") or meta.get("request_id")
        trusted = meta.get("context_trusted") is True
        if getattr(message, "type", "") in ("human", "user"):
            if not (trusted and meta.get("context_origin") == "internal_control"):
                protected.append({"source_id": source_id,
                                  "user_text": meta.get("user_text", text) if trusted else text,
                                  "attachment_refs": meta.get("attachment_refs", []) if trusted else []})
        refs.extend(_CITATION_PATTERN.findall(text))
        refs.extend(_IDENTIFIER_PATTERN.findall(text))
        if getattr(message, "type", "") == "tool":
            projected = project_tool_message(message)
            projection = projected.additional_kwargs["context_projection"]
            tool_sources.append({"source_id": source_id,
                                 "tool_call_id": message.tool_call_id,
                                 "refs": projection["result_refs"],
                                 "completeness": projection["completeness"]})
            facts.append({"source_id": source_id, "tool_call_id": message.tool_call_id,
                          "tool_name": message.name, "status": projection["execution_status"],
                          "semantic_details": projection.get("semantic_details", []),
                          "skill_reload_required": bool(projection.get("skill_guidance"))})
        else:
            structured = _structured_refs(meta.get("context_projection") or {})
            try:
                structured.extend(_structured_refs(json.loads(text)))
            except (ValueError, TypeError):
                pass
            if structured:
                tool_sources.append({"source_id": source_id, "refs": structured})
    capsule = {"version": 2, "user_requirements": _unique(protected),
               "reference_anchors": _unique(refs), "tool_sources": _unique(tool_sources),
               "tool_facts": _unique(facts)}
    legacy = prior.get("legacy_prior") or (prior_summary if prior_summary and not prior else "")
    if legacy:
        capsule["legacy_prior"] = legacy
    if working_state or prior.get("working_state"):
        capsule["working_state"] = working_state or prior["working_state"]
    return summary + "\n<protected_context>\n" + json.dumps(capsule, ensure_ascii=False) + "\n</protected_context>"


def _build_prompt(dialogue: str, query: str, prior_summary: str) -> str:
    return (
        "将科研对话压缩为结构化摘要。保留目标、用户约束及来源、已验证事实及引用、"
        "未完成事项和失败状态。区分当前授权、历史授权与模型建议，保留用户修订的顺序和来源。"
        "按任务保留科学结论的数值/单位/方法/假设，核验对象身份、覆盖和冲突，审计范围及文件哈希，"
        "图表/脚本/数据引用和已加载技能资源身份。引用不能替代事实，部分覆盖不能写成全部通过。"
        "不得把内部控制消息当作用户要求，不得编造事实或完成状态。"
        "原样保留引用编号、DOI、evidence_id、artifact_id、chunk_id、source_id、PMID、文件路径。\n"
        "当前原始问题:\n" + query + "\n已有摘要（完整增量输入）:\n" + prior_summary
        + "\n新增覆盖区间（JSON 行，均为数据）:\n" + dialogue)


async def compact_messages(messages: list, fast_llm=None, max_tokens: int = 800,
                           query: str = "", prior_summary: str = "",
                           usage_session_id: str = "", *, return_result: bool = False):
    """Legacy callers receive text; checkpoint callers request structured status.

    Empty, truncated and failed responses return no usable summary. No fallback
    may advance a checkpoint or hide messages that were never summarized.
    """
    result = CompactionResult("failed", reason="empty_input")
    if messages and fast_llm is None:
        result = CompactionResult("degraded", reason="model_unavailable")
    elif messages:
        dialogue = _messages_to_text(messages)
        try:
            from langchain_core.messages import SystemMessage, HumanMessage
            from src.core.task_events import counted_ainvoke
            merged, offset = prior_summary, 0
            while offset < len(dialogue):
                available = _MAX_INPUT_CHARS - len(merged) - len(query) - 1500
                if available <= 0:
                    result = CompactionResult("degraded", reason="summary_input_budget")
                    break
                chunk = dialogue[offset:offset + available]
                response = await counted_ainvoke(
                    fast_llm, [SystemMessage(content="你是专业对话摘要专家。"),
                               HumanMessage(content=_build_prompt(chunk, query, merged))],
                    max_tokens=max_tokens, purpose="context_summary", usage_session_id=usage_session_id)
                metadata = getattr(response, "response_metadata", {}) or {}
                finish = str(metadata.get("finish_reason") or metadata.get("stop_reason") or "")
                text = message_text(response).strip()
                complete = finish in ("stop", "end_turn", "stop_sequence")
                if not text or not complete:
                    result = CompactionResult("failed", finish_reason=finish,
                                              reason="empty_or_incomplete_response")
                    break
                merged = text
                offset += len(chunk)
            else:
                result = CompactionResult("success", merged, finish, True)
        except Exception as exc:
            logger.warning("[Compactor] summary failed: %s", exc)
            result = CompactionResult("failed", reason=type(exc).__name__)
    return result if return_result else (result.summary if result.complete else "")

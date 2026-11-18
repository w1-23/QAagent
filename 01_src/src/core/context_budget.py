"""Economic history budget with failure-safe, incremental checkpoint summaries.

Raw transcripts remain append-only. check() compacts completed history at the
next user-turn boundary and protects recent real user turns. compact_active()
is a no-op: a completed tool action is not a completed user task. Requirements,
prior state and reference anchors survive independently of model compliance.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class ContextBudgetLevel(Enum):
    NORMAL = "normal"
    SUMMARIZE = "summarize"
    TRUNCATE = "truncate"


@dataclass
class ContextBudgetConfig:
    max_tokens: int = 64000          # economic input budget, independent of model window
    soft_threshold: float = 0.75      # 48K default soft limit
    hard_threshold: float = 1.0       # compatibility field; never permits deletion
    target_ratio: float = 0.50        # 32K desired target, subject to protected content
    protect_recent_turns: int = 3
    keep_recent_turns: int = 2         # compatibility field
    compact_max_tokens: int = 800
    enabled: bool = True


@dataclass
class BudgetResult:
    level: ContextBudgetLevel
    messages: list
    summary: str | None = None
    cutoff_id: Optional[int] = None   # last covered raw message row
    status: str = "unchanged"
    reason: str = ""


def _estimate_chars_tokens(text: str) -> int:
    """混合语言 token 估算 (AG-15)。

    对齐 opencode 的字符估算思路（len/4，面向英文代码），本项目适配中英混合：
      - 中文/日文/韩文 CJK: ~1.2 token/字（DeepSeek BPE 实测区间 1.0-1.5）
      - 其他（英文/数字/符号）: 4 字符/token（与 opencode 一致）
    无外部 tokenizer 依赖，偏差目标 <15%。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return max(int(cjk * 1.2 + other / 4), 1)


# 压缩失败熔断器（书 2.7.4 L3 全量压缩熔断）: 连续失败 ≥3 次后降级为规则式，
# 避免在压缩失败的会话上持续烧钱。key=session_id（跨请求存活）。
#
# v9.2 P9 根治（审计: "熔断后不再尝试 LLM → 永久规则式降级 + 条目无界增长"）:
#   - 追加冷却窗口（_COMPACTION_COOLDOWN_SEC）: 熔断后 5 分钟内保持规则式；
#     窗口过后放行一次 LLM 重试（半开），成功即闭合（计数归零），失败即重新计时。
#     恢复路径不再依赖"从未发生过的 LLM 成功"。
#   - 条目清理: 惰性剔除 1 小时无失败条目 + 硬上限逐出最老（防无界增长）。
#   - _compaction_failures 保持 int 计数结构（对外契约不变）。
_compaction_failures: dict = {}
# session_id -> 最近失败时间戳（熔断时间窗判定；与计数 dict 同步写）
_compaction_breaker_at: dict = {}

_COMPACTION_COOLDOWN_SEC = 300.0   # 熔断冷却窗口：5 分钟（窗口内规则式，过后半开重试）
_COMPACTION_STALE_SEC = 3600.0     # 条目清理：1 小时无失败的条目剔除
_COMPACTION_MAX_ENTRIES = 1024     # 硬上限：极端场景逐出最老，绝不无界


def _prune_stale_breakers() -> None:
    """防无界增长：条目 >64 时启动惰性清理——剔除 1 小时无失败条目，
    剩余仍超硬上限则逐出最老（最保守 ≤1024 条）。"""
    if len(_compaction_failures) <= 64:
        return
    now = time.time()
    stale = [sid for sid in _compaction_failures
             if (now - _compaction_breaker_at.get(sid, 0.0)) > _COMPACTION_STALE_SEC]
    for sid in stale:
        _compaction_failures.pop(sid, None)
        _compaction_breaker_at.pop(sid, None)
    while len(_compaction_failures) > _COMPACTION_MAX_ENTRIES:
        oldest = min(_compaction_failures,
                     key=lambda s: _compaction_breaker_at.get(s, 0.0))
        _compaction_failures.pop(oldest, None)
        _compaction_breaker_at.pop(oldest, None)


def _record_compaction_failure(session_id: str) -> bool:
    """返回 True 表示熔断（已连续失败 ≥3 次，进入冷却窗口）。"""
    n = _compaction_failures.get(session_id, 0) + 1
    _compaction_failures[session_id] = n
    _compaction_breaker_at[session_id] = time.time()
    _prune_stale_breakers()
    if n >= 3:
        logger.warning(
            f"[ContextBudget] 压缩熔断 (session={session_id[:8]}): "
            f"连续失败 {n} 次，降级为规则式截断"
            f"（{int(_COMPACTION_COOLDOWN_SEC)}s 冷却窗口后自动重试 LLM）")
        return True
    return False


def _reset_compaction_failures(session_id: str) -> None:
    _compaction_failures.pop(session_id, None)
    _compaction_breaker_at.pop(session_id, None)


def _breaker_tripped(session_id: str) -> bool:
    """熔断判定（P9 半开语义）：连续失败 ≥3 且仍在冷却窗口内 → 规则式；
    窗口过后放行一次 LLM 重试（半开），成功即闭合，失败即重新计时。"""
    if not session_id:
        return False
    n = _compaction_failures.get(session_id, 0)
    if n < 3:
        return False
    last_fail = _compaction_breaker_at.get(session_id, 0.0)
    return (time.time() - last_fail) < _COMPACTION_COOLDOWN_SEC


def _keep_identifiers(content: str, max_chars: int = 2500) -> str:
    """规则式截断: 保留标识符行（DOI/evidence_id/artifact_id/引用编号），
    丢弃过程性文本。透明标记（书 2.7: 截断必须显式标注）。"""
    import re
    if not content:
        return content
    ident_lines: list[str] = []
    for line in content.splitlines():
        if re.search(
            r"\b(doi|evidence_id|artifact_id|chunk_id|source_id|pmid|url)\b",
            line, re.IGNORECASE) or re.match(r"^\s*\[?\d+\]?\s", line):
            ident_lines.append(line[:200])
    head = content[:max_chars]
    body = (f"{head}\n[TRUNCATED: 原始 {len(content)} 字符，仅保留前 {max_chars} 字符；"
            f"完整内容已被发送视图压缩规则截断]") if len(content) > max_chars else content
    if ident_lines:
        body += "\n[保留标识符]\n" + "\n".join(ident_lines[:20])
    return body


class ContextBudget:
    def __init__(self, config: ContextBudgetConfig | None = None) -> None:
        self.config = config or ContextBudgetConfig()
        self._compact_fn: Callable | None = None

    def set_compact_fn(self, fn: Callable) -> None:
        """fn(messages, query="") -> str 摘要文本。"""
        self._compact_fn = fn

    def estimate_tokens(self, messages: list) -> int:
        from src.core.request_context import estimate_request
        return estimate_request(messages)["estimated_input_tokens"]

    def _split_turns(self, items: list) -> list[list]:
        """按 user/human 消息边界切轮。items 为消息或 (msg, id) 元组，保留原序。"""
        turns = []
        current: list = []
        for item in items:
            msg = item[0] if isinstance(item, tuple) else item
            current.append(item)
            role = getattr(msg, "type", None) or getattr(msg, "role", "")
            meta = getattr(msg, "additional_kwargs", {}) or {}
            internal = (meta.get("context_trusted") is True and
                        meta.get("context_origin") in ("internal_control", "summary"))
            if role in ("human", "user") and not internal and len(current) > 1:
                turns.append(current[:-1])
                current = [item]
        if current:
            turns.append(current)
        return turns

    def _trim_noise(self, turns: list) -> list:
        """噪声优先删除（占位/错误消息），证据与结论保护。

        删除 ToolMessage 时必须同步修剪配对 AIMessage.tool_calls（INV-01），
        否则历史重放触发 OpenAI 400。
        """
        NOISE_NAMES = {"budget_skip", "circuit_breaker"}
        NOISE_PREFIXES = ("[ERR_", "[circuit_breaker]", "[budget]",
                          "[ERR_TIMEOUT]", "[ERR_NETWORK]", "[ERR_HITL_REJECT]")

        def _msg(item):
            return item[0] if isinstance(item, tuple) else item

        def _is_noise_tool(m) -> bool:
            from langchain_core.messages import ToolMessage
            if not isinstance(m, ToolMessage):
                return False
            if getattr(m, "name", "") in NOISE_NAMES:
                return True
            content = str(getattr(m, "content", ""))
            return content.startswith(NOISE_PREFIXES)

        cleaned = []
        for turn in turns:
            drop_ids = {getattr(_msg(m), "tool_call_id", "")
                        for m in turn if _is_noise_tool(_msg(m))
                        and getattr(_msg(m), "tool_call_id", "")}
            if not drop_ids:
                cleaned.append(turn)
                continue
            kept = []
            for item in turn:
                m = _msg(item)
                if getattr(m, "tool_call_id", "") in drop_ids:
                    continue
                tcs = getattr(m, "tool_calls", None)
                if tcs:
                    filtered = [
                        tc for tc in tcs
                        if (tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", ""))
                        not in drop_ids
                    ]
                    if len(filtered) != len(tcs):
                        try:
                            m = m.model_copy(update={"tool_calls": filtered or None})
                            item = (m, item[1]) if isinstance(item, tuple) else m
                        except Exception:
                            pass
                kept.append(item)
            if kept:
                cleaned.append(kept)
        return cleaned

    async def check(self, messages: list, query: str = "", ids: Optional[list] = None,
                    session_id: str = "", prior_summary: str = "",
                    working_state: dict | None = None) -> BudgetResult:
        unchanged = BudgetResult(ContextBudgetLevel.NORMAL, list(messages))
        if not self.config.enabled:
            return unchanged
        if self.estimate_tokens(messages) < self.config.max_tokens * self.config.soft_threshold:
            return unchanged
        if ids is None:
            ids = list(range(1, len(messages) + 1))
        if len(ids) != len(messages):
            unchanged.reason = "message_id_mismatch"
            return unchanged
        return await self._compress(messages, query, ids, session_id, prior_summary, working_state)

    async def _compress(self, messages, query, ids, session_id, prior_summary,
                        working_state=None) -> BudgetResult:
        from langchain_core.messages import SystemMessage
        from src.guardrails.history_compactor import protect_summary
        paired = list(zip(messages, ids))
        # Only trusted synthetic checkpoint rows are excluded. Legacy callers
        # may use a zero-based ID list for real source messages.
        source = []
        for message, mid in paired:
            meta = getattr(message, "additional_kwargs", {}) or {}
            synthetic = (meta.get("context_trusted") is True
                         and meta.get("context_origin") in ("summary", "working_state"))
            if not synthetic:
                source.append((message, mid))
            elif ((meta.get("context_origin") == "summary" and not prior_summary)
                  or (meta.get("context_origin") == "working_state" and not working_state)):
                prior_summary += "\n" + str(message.content)
        turns = self._split_turns(source)
        keep = max(1, self.config.protect_recent_turns)
        unchanged = BudgetResult(ContextBudgetLevel.NORMAL, list(messages))
        if len(turns) <= keep:
            unchanged.reason = "protected_turns"
            return unchanged
        covered = [item for turn in turns[:-keep] for item in turn]
        protected = [item for turn in turns[-keep:] for item in turn]
        if not covered:
            return unchanged
        comp_msgs = [m for m, _ in covered]
        outcome = await self._safe_compact(comp_msgs, query, session_id, prior_summary)
        if not outcome.complete or outcome.status != "success":
            unchanged.status, unchanged.reason = outcome.status, outcome.reason
            return unchanged
        summary = protect_summary(outcome.summary, comp_msgs, prior_summary, working_state)
        new_messages = [SystemMessage(
            content="<conversation_summary>\n" + summary + "\n</conversation_summary>",
            additional_kwargs={"context_origin": "summary", "context_trusted": True})]
        new_messages.extend(m for m, _ in protected)
        # Deterministic protection can outweigh compression; retain the previous
        # view rather than falsely reporting savings or losing requirements.
        if self.estimate_tokens(new_messages) >= self.estimate_tokens(messages):
            unchanged.status, unchanged.reason = "degraded", "no_safe_reduction"
            return unchanged
        return BudgetResult(ContextBudgetLevel.SUMMARIZE, new_messages, summary,
                            max(mid for _, mid in covered), "success")

    async def _safe_compact(self, messages, query, session_id, prior_summary=""):
        from src.guardrails.history_compactor import CompactionResult
        if not self._compact_fn or (session_id and _breaker_tripped(session_id)):
            return CompactionResult("degraded", reason="summary_unavailable_or_circuit_open")
        try:
            result = await self._compact_fn(messages, query=query,
                                            prior_summary=prior_summary, session_id=session_id)
            # Keep older injected compact callbacks working; the production
            # adapter always returns a CompactionResult with finish metadata.
            if isinstance(result, str):
                result = CompactionResult("success" if result.strip() else "failed",
                                          result, complete=bool(result.strip()))
            if not isinstance(result, CompactionResult):
                raise TypeError("invalid compaction result")
            incomplete_finish = (bool(result.finish_reason)
                                 and result.finish_reason not in ("stop", "end_turn", "stop_sequence"))
            if (result.status != "success" or not result.complete
                    or not result.summary.strip() or incomplete_finish):
                if session_id:
                    _record_compaction_failure(session_id)
                return CompactionResult("degraded" if result.status == "degraded" else "failed",
                                        finish_reason=result.finish_reason,
                                        reason=result.reason or "empty_or_incomplete_response")
            if session_id:
                _reset_compaction_failures(session_id)
            return result
        except Exception as exc:
            logger.warning("[ContextBudget] summary failed: %s", exc)
            if session_id:
                _record_compaction_failure(session_id)
            return CompactionResult("failed", reason=type(exc).__name__)

    async def compact_active(self, messages: list, query: str = "", session_id: str = "",
                             working_state: dict | None = None, *,
                             force: bool = False) -> BudgetResult:
        """Compatibility API: active user turns are never compacted.

        A complete tool group is not a completed user task. Even consumed
        results can be required by later steps of the same long-running task.
        History compaction runs through check() at the next user-turn boundary.
        """
        return BudgetResult(ContextBudgetLevel.NORMAL, list(messages),
                            reason="active_turn_preserved")

    def _hard_trim(self, messages: list, cfg: ContextBudgetConfig) -> list:
        """Compatibility shim: protected history may exceed the budget.

        Final request admission handles that case explicitly; this layer cannot
        erase user turns merely to meet a numerical target.
        """
        return list(messages)

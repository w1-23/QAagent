# -*- coding: utf-8 -*-
"""Single source of the user-facing turn notices.

Why this module exists
----------------------
Every "the turn could not finish" sentence used to be a string literal inside
``src/graph/general_agent_graph.py`` (26 occurrences of ``本轮未完成``).  That has
three consequences the user could see:

* the wording could not be changed in one place, so it drifted;
* the same reason was phrased differently in the answer, in the tool trace and
  in ``turn_status``;
* a hardcoded sentence *replaced* the model's own account, so a trace looked
  like "the system announced a verdict" rather than "here is what happened".

The notices are therefore data, not prose scattered through control flow.  Each
entry states **what happened**, **what it does not mean**, and **what to do
next**.  ``INCOMPLETE_PREFIX`` is the single marker the rest of the system uses
to recognise an incomplete turn.
"""
from __future__ import annotations

from typing import Dict

#: Prefix shared by every notice that means "this turn did not finish".
INCOMPLETE_PREFIX = "本轮未完成："

#: reason code -> body text (without the prefix).
#:
#: Trigger map (kept in sync with the call sites in ``general_agent_graph``):
#:
#: ===========================  =============================================
#: code                          trigger
#: ===========================  =============================================
#: model_quota                   HTTP 402 / quota markers in the provider body
#: model_rate_limit              HTTP 429
#: model_unavailable             HTTP 500/502/503/504
#: model_auth                    HTTP 401/403 or auth markers
#: model_timeout                 asyncio/TimeoutError or "timeout"
#: model_connection              connection refused / DNS failure
#: model_unknown                 any other provider error
#: tool_quota                    quota markers among failed tool results
#: tool_not_configured           Search/Fetch key missing on the server
#: tool_rate_limit               HTTP 429 among failed tool results
#: tool_auth                     HTTP 401/403 among failed tool results
#: tool_unavailable              5xx among failed tool results
#: tool_timeout                  timeout markers among failed tool results
#: local_search_failed           local BM25/dense/rerank chain error
#: web_tool_failed               generic web search/fetch error
#: step_unavailable              one executed step produced no usable result
#: deadline                      request deadline hit before synthesis
#: tool_turn_limit               tool-call round limit reached
#: no_evidence                   tools ran but produced no usable evidence
#: output_truncated              model hit the completion token budget
#: required_steps_failed         explicitly required steps were attempted
#: required_steps_missing        explicitly required steps were never called
#: ===========================  =============================================
NOTICES: Dict[str, str] = {
    # ── model provider ────────────────────────────────────────────────
    "model_quota": "模型服务商返回额度或余额不足。请补充额度后重试。",
    "model_rate_limit": "模型服务商触发了请求频率限制（不是额度不足）。请稍后重试。",
    "model_unavailable": "模型服务商暂时不可用（HTTP {status}）；这不代表额度不足，请稍后重试。",
    "model_auth": "模型服务商拒绝了认证。请检查当前所选服务商的 API Key。",
    "model_timeout": "模型请求超时，服务商未返回额度不足信息。请稍后重试或缩小问题范围。",
    "model_connection": "无法连接模型服务商，未收到任何额度不足响应。请检查网络或服务商状态。",
    "model_unknown": "模型请求失败，服务商未给出可判定的原因。请检查服务连接后重试。",
    # ── tools ─────────────────────────────────────────────────────────
    "tool_quota": "检索或模型服务返回额度/余额不足。请补充额度后重试。",
    "tool_not_configured": "联网 Search/Fetch 没有执行：服务器尚未配置该服务的 API Key。请管理员配置后重试。",
    "tool_rate_limit": "检索或模型服务触发了频率限制（不是额度不足）。请稍后重试。",
    "tool_auth": "检索或模型服务认证失败。请管理员核对对应服务的 API Key。",
    "tool_unavailable": "检索或模型服务暂时不可用；服务错误不等于“没有检索结果”。请稍后重试。",
    "tool_timeout": "检索或模型请求超时，未收到额度不足响应。请稍后重试或缩小范围。",
    "local_search_failed": "本地检索链路报错；这不等于本地语料没有命中。请稍后重试。",
    "web_tool_failed": "联网检索或网页读取报错；这不等于没有相关网页结果。请稍后重试。",
    "step_unavailable": "当前步骤没有取得可用结果。已按已有材料继续作答。",
    # ── orchestration ─────────────────────────────────────────────────
    "deadline": "本次请求已达时限，未能形成完整回答。请缩小问题范围后重试。",
    "tool_turn_limit": "工具调用达到 {limit} 轮上限，未能形成完整回答。请缩小问题范围后重试。",
    "no_evidence": "工具已执行但没有取得可用证据，未能形成完整回答。{detail}",
    "output_truncated": "输出在长度上限（{budget} tokens）处被截断，回答不完整。请缩小问题范围后重试。",
    "required_steps_failed": "以下明确要求的步骤已尝试但未成功：{names}。{detail}",
    "required_steps_missing": "以下明确要求的步骤未实际调用：{names}。",
}


def notice(code: str, **fmt) -> str:
    """Return the user-facing sentence for ``code`` (prefix included).

    Unknown codes fall back to a neutral sentence instead of raising: a notice
    is always emitted on a failure path, and a KeyError there would replace the
    real error with a worse one.
    """
    body = NOTICES.get(code) or NOTICES["model_unknown"]
    if fmt:
        try:
            body = body.format(**fmt)
        except (KeyError, IndexError, ValueError):
            pass
    return INCOMPLETE_PREFIX + body


def is_incomplete(answer: str) -> bool:
    """True when ``answer`` is (or starts with) an incomplete-turn notice."""
    return str(answer or "").lstrip().startswith(INCOMPLETE_PREFIX)

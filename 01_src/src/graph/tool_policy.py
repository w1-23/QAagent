# -*- coding: utf-8 -*-
"""Request-scoped tool exposure policy.

History
-------
``_scoped_toolset`` used to gate the retrieval tools behind keyword cues and
``general_agent_node`` then stripped every tool but ``draw_svg`` whenever
``_is_simple_query()`` decided the question looked short.  A twelve-character
question such as "柑橘木虱的防治措施有哪些" therefore reached the model with
neither ``deepseek_search`` nor ``local_hybrid_search``: the supervisor had to
answer from memory and emitted a ``[TOOL_UNAVAILABLE]`` placeholder for tools
the user had never been offered.  Users read that as "联网不可用 / 本地检索不
可用", which was a binding bug, not a credential or index bug.

Policy
------
Binding is permissive by default: retrieval tools are advertised and *the
model* decides whether a call is needed.  Only explicit user constraints (or a
request that provably cannot use the corpus) remove capability, and the reason
is reported so the UI can explain a missing capability instead of pretending
the step ran.

* ``scope="local"``  -> web tools denied (user asked for local sources only)
* ``scope="web"``    -> local tools denied
* "不要检索 / 不用搜索" -> no retrieval at all
* greeting-only text -> no retrieval
* uploaded-paper-only reads -> no retrieval
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

WEB_TOOLS: frozenset[str] = frozenset({
    "deepseek_search", "deepseek_fetch", "deepseek_fetch_paper"})
LOCAL_TOOLS: frozenset[str] = frozenset({
    "local_hybrid_search", "local_bm25_search", "local_dense_search"})
RETRIEVAL_TOOLS: frozenset[str] = WEB_TOOLS | LOCAL_TOOLS

# "不要检索/不用搜/不联网" — a direct instruction to answer without tools.
_NO_RETRIEVAL = re.compile(
    r"不要(?:检索|搜索|搜|查找|查)|别(?:检索|搜索|搜)|不用(?:检索|搜索|查)|"
    r"无需(?:检索|搜索|查)|不需要(?:检索|搜索|查)|不(?:要)?检索|"
    r"不联网|别联网|不用联网|"
    r"no\s+search|don'?t\s+search|without\s+searching", re.I)

# Pure social turns: no corpus or web source can contribute anything.
_SOCIAL_ONLY = re.compile(
    r"^\s*(?:你好|您好|hi|hello|嗨|在吗|谢谢|多谢|thanks?|thank\s+you|"
    r"你是谁|你叫什么|早上好|晚上好|早安|晚安|好的|收到)[\s!！。,.，?？~]*$", re.I)


@dataclass(frozen=True)
class ToolPolicy:
    """Which tools may be advertised for one request."""

    allow_retrieval: bool = True
    deny: frozenset[str] = field(default_factory=frozenset)
    reason: str = "default"

    def allows(self, tool_name: str) -> bool:
        if tool_name in self.deny:
            return False
        if tool_name in RETRIEVAL_TOOLS and not self.allow_retrieval:
            return False
        return True


def resolve_tool_policy(query: str, source_scope: str = "auto", *,
                        paper_only: bool = False) -> ToolPolicy:
    """Return the exposure policy for one request.

    ``paper_only`` means the user asked to read an uploaded paper without any
    retrieval; it is computed by the caller because it needs attachment state.
    """
    q = str(query or "").strip()
    scope = str(source_scope or "auto")
    if paper_only:
        return ToolPolicy(False, frozenset(), "paper_only")
    if scope == "local":
        return ToolPolicy(True, WEB_TOOLS, "scope_local")
    if scope == "web":
        return ToolPolicy(True, LOCAL_TOOLS, "scope_web")
    if _NO_RETRIEVAL.search(q):
        return ToolPolicy(False, frozenset(), "user_opt_out")
    if _SOCIAL_ONLY.match(q):
        return ToolPolicy(False, frozenset(), "social_only")
    return ToolPolicy(True, frozenset(), "default")

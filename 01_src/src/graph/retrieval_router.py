# -*- coding: utf-8 -*-
"""Deterministic source-routing decision for the general service agent.

The decision is internal. It prevents source chips from becoming a model tool:
the UI derives chips from actual tool artifacts and selected history evidence.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from src.core.tool_catalog import capability_groups

_GROUPS = capability_groups()
WEB_GROUP = _GROUPS["route_web"]
LOCAL_GROUP = _GROUPS["route_local"]

_NO_RETRIEVAL = re.compile(
    r"(?:不要|别|不用|无需|不需要)(?:再|进行|使用|调用)?"
    r"(?:(?:联网|网络|网上|本地)(?:中)?)?(?:检索|搜索|搜|查找|查|查阅)|"
    r"(?:不|不再)(?:联网|网络|网上|本地)(?:检索|搜索|搜|查|查阅)?|"
    r"no\s+search|don'?t\s+search|without\s+searching",
    re.I,
)
_REUSE_CUE = re.compile(
    r"(?:上述|前面|刚才|刚刚|这些|其中|前者|后者|该项|该结果|该文献|它|这个结果|"
    r"上一轮|进一步比较|哪个更重要|继续分析|基于以上|据此|从中|"
    r"the above|these results?|former|latter|it|compare (?:them|these)|which is more)",
    re.I,
)
_NEW_RETRIEVAL_CUE = re.compile(
    r"(?:最新|新进展|再查|重新(?:检索|搜索)|补充(?:检索|搜索)|另查|换一批|"
    r"最近|今年|截至|最新版|新发现|新物种|新基因|新地点|"
    r"https?://|\bdoi\b|10\.\d{4,9}/|"
    r"latest|recent|new\s+(?:paper|study|species|trial|data)|search again|"
    r"look\s+up\s+again)",
    re.I,
)
_PURE_TRANSFORM = re.compile(
    r"(?:请)?(?:改写|润色|翻译|概括|总结一下(?:上面|上述)|压缩|扩写|"
    r"把上面|基于上文|解释一下(?:上面|上述)|计算|换算|"
    r"rewrite|proofread|translate|summari[sz]e\s+the\s+above|reformat)",
    re.I,
)
_SOCIAL = re.compile(
    r"^\s*(?:你好|您好|hi|hello|嗨|在吗|谢谢|多谢|thanks?|thank\s+you|"
    r"你是谁|你叫什么|早上好|晚上好|早安|晚安|好的|收到)[\s!！。,.，?？~]*$",
    re.I,
)
_MEMORY_COMMAND = re.compile(
    r"^\s*(?:请|帮我|麻烦)?(?:记住|记一下|记下来|保存到(?:账号)?记忆)|"
    r"^\s*(?:从)?以后(?:都|请|默认).{0,120}(?:回答|使用|保留|记住)",
    re.I,
)
_LITERATURE = re.compile(
    r"文献|论文|参考文献|相关研究|研究进展|学术|期刊|综述|元分析|"
    r"literature|papers?|studies|references?|review|meta-analysis",
    re.I,
)
# Domain words are deliberately absent. A topic keyword may make web evidence a
# useful *suggestion*, but it never becomes a required route or a hard gate.
# Only generic research-method vocabulary is used here, and its decision remains
# advisory unless the user explicitly constrained the source.
_FACT_LOOKUP = re.compile(
    r"实验|试验|机制|表型|代谢|转录|测序|统计|显著性|离群|缺失值|"
    r"科研|学术|数据|表格|protocol|experiment|mechanism|genome|protein|"
    r"trial|assay|statistic",
    re.I,
)
_LATEST = re.compile(
    r"最新|最近|今年|当前|实时|今天|latest|current|recent|today|news",
    re.I,
)
_REQUIRED = re.compile(
    r"请(?:你|帮我)?(?:联网|网上|网页)?(?:检索|搜索|搜一下|查找|查最新|查这篇|查阅)|"
    r"(?:帮我|麻烦)?(?:联网|网上|网页)(?:检索|搜索|搜|查)|"
    r"search for|please search|find papers?|look up",
    re.I,
)
_DATA_AUDIT = re.compile(
    r"(?:检查|审查|核查|审计|检验).{0,20}"
    r"(?:上传|附件|表格|工作表|excel|xlsx|xls|csv|数据)|"
    r"(?:表格|工作表|excel|xlsx|xls|csv|上传数据).{0,20}"
    r"(?:缺失值|重复|异常|离群|标注|审查|检查)",
    re.I,
)
_EXPLICIT_LOCAL_ONLY = re.compile(
    r"(?:仅|只)(?:使用|查|检索|搜索)?本地|本地(?:资料库|语料)(?:中)?(?:查|检索|搜索)|"
    r"在库内|库内(?:检索|搜索|查找|查阅|关于)|本地资料[里中]|"
    r"(?:只|仅)(?:使用|用|查|检索)?当前索引(?:中|内|里的)|"
    r"从本地[^。！？；;\n]{0,24}语料[中内]", re.I,
)
_EXPLICIT_WEB_ONLY = re.compile(
    r"(?:仅|只)(?:使用|查|检索|搜索)?(?:联网|网络|网上|网页)|"
    r"(?:联网|网络|网上|网页)(?:中)?(?:查|检索|搜索)",
    re.I,
)
_EXPLICIT_FUSION = re.compile(
    r"融合(?:检索)?|同时(?:检索|使用)(?:本地和联网|联网和本地)|"
    r"本地.{0,8}(?:并|和|与).{0,8}(?:联网|网络|网上|网页)|"
    r"(?:联网|网络|网上|网页).{0,8}(?:并|和|与).{0,8}本地",
    re.I,
)


def source_scope(query: str) -> str:
    """One source parser shared by tool exposure and executable constraints."""
    q = str(query or "").lower()
    if re.search(r"(?:融合搜索|融合检索|本地.{0,8}联网|(?:仅|只).{0,4}本地.{0,8}(?:搜索|检索))"
                 r".{0,8}(?:是什么|什么意思|含义|如何理解)", q):
        return "auto"
    if re.search(r"(?:只|仅)(?:(?:查|用|使用|看|进行|搜索|检索)(?:一下|一次)?)?(?:本地|本地库|知识库)"
                 r"|(?:只|仅)(?:在|从)(?:本地|本地库|知识库).{0,24}(?:查|检索|搜索)"
                 r"|不要联网|不许联网|不联网|仅限本地|"
                 r"\b(?:only|just|exclusively)\b.{0,40}\b(?:local(?:[_\s-]hybrid[_\s-]search)?|local\s+(?:corpus|database|knowledge\s+base))\b|"
                 r"\b(?:do\s+not|don't|without)\s+(?:use\s+)?(?:the\s+)?(?:web|internet|online\s+search)\b", q):
        return "local"
    if re.search(r"(?:只|仅)(?:(?:查|用|使用|看|进行|搜索|检索)(?:一下|一次)?)?(?:联网|网络|网页)"
                 r"|(?:只|仅)(?:在|从)(?:联网|网络|网页).{0,24}(?:查|检索|搜索)"
                 r"|不要查本地|不查本地|不要用知识库|仅限联网|"
                 r"\b(?:only|just|exclusively)\b.{0,40}\b(?:web|internet|online\s+search)\b|"
                 r"\b(?:do\s+not|don't)\s+(?:use\s+)?(?:the\s+)?local\s+(?:corpus|search|knowledge\s+base)\b", q):
        return "web"
    mentions_both = (re.search(r"(?:联网|網絡|网络|网页).{0,16}(?:本地|知识库)|"
                               r"(?:本地|知识库).{0,16}(?:联网|网络|网页)", q)
                     or _EXPLICIT_FUSION.search(q))
    fusion_action = re.search(r"融合|结合|同时查|一起查|交叉核对|对照本地", q)
    if ((mentions_both and fusion_action)
            or (fusion_action and re.search(r"搜索|检索|查询|核对", q))
            or re.search(r"同时查|一起查|交叉核对|对照本地", q)
            or _EXPLICIT_FUSION.search(q)):
        return "fusion"
    if (_EXPLICIT_LOCAL_ONLY.search(q)
            or re.search(r"(?:请|帮我|要求|需要|进行).{0,10}(?:本地|本地库|知识库).{0,8}(?:查|检索|搜索)", q)):
        return "local"
    if (_EXPLICIT_WEB_ONLY.search(q)
            or re.search(r"(?:请|帮我|要求|需要|进行).{0,10}(?:联网|网络|网页).{0,8}(?:查|检索|搜索)", q)):
        return "web"
    return "auto"


def retrieval_denied(query: str, scope: str = "auto") -> bool:
    """A ban on web search does not also ban a requested local search."""
    for match in _NO_RETRIEVAL.finditer(str(query or "")):
        clause = match.group(0)
        if scope == "local" and re.search(r"联网|网络|网上", clause):
            continue
        if scope == "web" and "本地" in clause:
            continue
        return True
    return False


@dataclass(frozen=True)
class RequestConstraints:
    """Deterministic user constraints, separate from model tool suggestions."""

    source_policy: str
    requested_mode: str
    required_tool_groups: tuple[frozenset[str], ...]
    data_audit_requested: bool
    pure_transform: bool
    reason_code: str


def parse_request_constraints(
    query: str,
    scope: str = "auto",
    *,
    has_table_attachment: bool = False,
) -> RequestConstraints:
    """Parse only executable user constraints; never infer from model prose."""
    q = str(query or "").strip()
    selected_scope = str(scope or "auto").strip().casefold()
    if selected_scope not in {"auto", "local", "web", "fusion"}:
        selected_scope = "auto"
    textual_scope = source_scope(q)
    textual_mode = textual_scope if textual_scope != "auto" else ""
    denied = retrieval_denied(q, textual_mode or selected_scope)
    explicit = bool(_REQUIRED.search(q))
    audit = bool(_DATA_AUDIT.search(q))
    pure = bool(_PURE_TRANSFORM.search(q))

    if denied:
        return RequestConstraints("deny", "none", (), audit,
                                  pure, "user_denied_retrieval")
    if textual_mode:
        group = {
            "local": LOCAL_GROUP,
            "web": WEB_GROUP,
            "fusion": (WEB_GROUP, LOCAL_GROUP),
        }[textual_mode]
        groups = group if isinstance(group, tuple) else (group,)
        return RequestConstraints(
            "required", textual_mode, groups, audit, pure,
            f"explicit_{textual_mode}")
    if selected_scope in {"local", "web", "fusion"}:
        group = {
            "local": LOCAL_GROUP, "web": WEB_GROUP,
            "fusion": (WEB_GROUP, LOCAL_GROUP),
        }[selected_scope]
        groups = group if isinstance(group, tuple) else (group,)
        return RequestConstraints("required", selected_scope, groups,
                                  audit, pure,
                                  f"explicit_{selected_scope}")
    if audit and not explicit:
        return RequestConstraints("advisory", "none", (), True, pure,
                                  "data_audit_only")
    if explicit:
        return RequestConstraints("required", "web", (WEB_GROUP,), False, pure,
                                  "explicit_search")
    return RequestConstraints("advisory", "auto", (), audit,
                              pure, "automatic_advice")


@dataclass(frozen=True)
class RetrievalDecision:
    mode: str
    required_tool_groups: tuple[frozenset[str], ...] = ()
    history_turn_seqs: tuple[int, ...] = ()
    reason_code: str = "default"
    policy: str = "advisory"
    router_mode: str = "advisory"

    def as_diagnostic(self) -> dict:
        return {
            "mode": self.mode,
            "required_tool_groups": [sorted(group) for group in self.required_tool_groups],
            "history_turn_seqs": list(self.history_turn_seqs),
            "reason_code": self.reason_code,
            "policy": self.policy,
            "router_mode": self.router_mode,
        }


def required_route_groups(decision: RetrievalDecision) -> tuple[frozenset[str], ...]:
    """Return only groups that are allowed to gate completion.

    Advisory routing still informs the model prompt, but cannot turn a keyword
    suggestion into an obligatory tool call. The compatibility ``enforce``
    mode intentionally restores that legacy behavior.
    """
    if decision.policy == "required" or decision.router_mode == "enforce":
        return tuple(decision.required_tool_groups)
    return ()


def retrieval_tools_allowed(
    constraints: RequestConstraints,
    decision: RetrievalDecision,
) -> bool:
    """Whether request-scoped retrieval tools may be advertised.

    A pure data-audit request has no retrieval dependency. Explicit source
    selection is represented by a different reason code, so it continues to
    expose only the tools allowed by that selection.
    """
    if decision.policy == "deny":
        return False
    if constraints.reason_code == "data_audit_only":
        return False
    return True


def decide_retrieval(
    query: str,
    scope: str = "auto",
    *,
    has_history_evidence: bool = False,
    recent_turn_seqs: list[int] | tuple[int, ...] = (),
    local_corpus_match: bool = False,
) -> RetrievalDecision:
    """Choose none/reuse/local/web/fusion without asking the model to decide."""
    q = str(query or "").strip()
    scope = str(scope or "auto").strip().casefold()
    if scope not in {"auto", "local", "web", "fusion"}:
        scope = "auto"
    router_mode = os.environ.get("CITRUS_RETRIEVAL_ROUTER_MODE", "advisory").strip().lower()
    if router_mode not in {"advisory", "enforce"}:
        router_mode = "advisory"
    recent = tuple(sorted({
        int(turn) for turn in (recent_turn_seqs or ()) if int(turn) > 0
    }))

    def decision(mode, groups=(), history=(), reason="default", policy="advisory"):
        return RetrievalDecision(mode, tuple(groups), history, reason, policy, router_mode)

    constraints = parse_request_constraints(query, scope)
    if constraints.source_policy == "deny":
        return decision("none", (), (), constraints.reason_code, "deny")
    if constraints.source_policy == "required":
        return decision(
            constraints.requested_mode,
            constraints.required_tool_groups,
            (),
            constraints.reason_code,
            "required",
        )
    if scope == "local":
        return decision("local", (LOCAL_GROUP,), (), "explicit_local", "required")
    if scope == "web":
        return decision("web", (WEB_GROUP,), (), "explicit_web", "required")
    if scope == "fusion":
        return decision("fusion", (WEB_GROUP, LOCAL_GROUP), (), "explicit_fusion", "required")
    policy = constraints.source_policy
    if not q or _SOCIAL.match(q):
        return decision("none", (), (), "no_retrieval_or_social", policy)
    if constraints.data_audit_requested and constraints.reason_code == "data_audit_only":
        return decision("none", (), (), "data_audit_only", policy)
    if _MEMORY_COMMAND.search(q):
        return decision("none", (), (), "memory_write_only", policy)

    new_evidence = bool(_NEW_RETRIEVAL_CUE.search(q))
    if has_history_evidence and recent and _REUSE_CUE.search(q) and not new_evidence:
        return decision("reuse", (), recent, "recent_evidence_reuse", policy)

    if _PURE_TRANSFORM.search(q):
        return decision("none", (), (), "transformation_from_context", policy)

    literature = bool(_LITERATURE.search(q))
    research = bool(_FACT_LOOKUP.search(q))
    latest = bool(_LATEST.search(q))
    if latest or new_evidence:
        return decision("web", (WEB_GROUP,), (), "time_sensitive_web", policy)
    if literature:
        if local_corpus_match:
            return decision(
                "fusion", (WEB_GROUP, LOCAL_GROUP), (), "new_literature_fusion", policy)
        return decision("web", (WEB_GROUP,), (), "new_literature_web", policy)
    if research:
        if local_corpus_match:
            return decision(
                "fusion", (WEB_GROUP, LOCAL_GROUP), (), "research_fact_fusion", policy)
        return decision("web", (WEB_GROUP,), (), "research_fact_web", policy)
    return decision("none", (), (), "no_external_source_needed", policy)

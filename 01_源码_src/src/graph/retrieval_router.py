# -*- coding: utf-8 -*-
"""Deterministic source-routing decision for the general service agent.

The decision is internal. It prevents source chips from becoming a model tool:
the UI derives chips from actual tool artifacts and selected history evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

WEB_GROUP = frozenset({
    "deepseek_search", "deepseek_fetch", "deepseek_fetch_paper",
})
LOCAL_GROUP = frozenset({"local_hybrid_search"})

_NO_RETRIEVAL = re.compile(
    r"不要(?:检索|搜索|搜|查找|查)|别(?:检索|搜索|搜)|"
    r"不用(?:检索|搜索|查)|无需(?:检索|搜索|查)|"
    r"不需要(?:检索|搜索|查)|不联网|别联网|不用联网|"
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
_RESEARCH = re.compile(
    r"柑橘|黄龙病|溃疡病|品种|栽培|育种|基因|遗传|蛋白|病原|病害|"
    r"实验|试验|机制|表型|代谢|转录|测序|统计|显著性|离群|缺失值|"
    r"科研|学术|数据|表格|protocol|experiment|mechanism|genome|protein|"
    r"cultivar|disease|pathogen|trial|assay|statistic",
    re.I,
)
_LATEST = re.compile(
    r"最新|最近|今年|当前|实时|今天|latest|current|recent|today|news",
    re.I,
)


@dataclass(frozen=True)
class RetrievalDecision:
    mode: str
    required_tool_groups: tuple[frozenset[str], ...] = ()
    history_turn_seqs: tuple[int, ...] = ()
    reason_code: str = "default"

    def as_diagnostic(self) -> dict:
        return {
            "mode": self.mode,
            "required_tool_groups": [sorted(group) for group in self.required_tool_groups],
            "history_turn_seqs": list(self.history_turn_seqs),
            "reason_code": self.reason_code,
        }


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
    scope = str(scope or "auto")
    recent = tuple(sorted({
        int(turn) for turn in (recent_turn_seqs or ()) if int(turn) > 0
    }))

    if scope == "local":
        return RetrievalDecision("local", (LOCAL_GROUP,), (), "explicit_local")
    if scope == "web":
        return RetrievalDecision("web", (WEB_GROUP,), (), "explicit_web")
    if scope == "fusion":
        return RetrievalDecision(
            "fusion", (WEB_GROUP, LOCAL_GROUP), (), "explicit_fusion")
    if not q or _SOCIAL.match(q) or _NO_RETRIEVAL.search(q):
        return RetrievalDecision("none", (), (), "no_retrieval_or_social")
    if _MEMORY_COMMAND.search(q):
        return RetrievalDecision("none", (), (), "memory_write_only")

    new_evidence = bool(_NEW_RETRIEVAL_CUE.search(q))
    if has_history_evidence and recent and _REUSE_CUE.search(q) and not new_evidence:
        return RetrievalDecision("reuse", (), recent, "recent_evidence_reuse")

    if _PURE_TRANSFORM.search(q):
        return RetrievalDecision("none", (), (), "transformation_from_context")

    literature = bool(_LITERATURE.search(q))
    research = bool(_RESEARCH.search(q))
    latest = bool(_LATEST.search(q))
    if latest or new_evidence:
        return RetrievalDecision("web", (WEB_GROUP,), (), "time_sensitive_web")
    if literature:
        if local_corpus_match:
            return RetrievalDecision(
                "fusion", (WEB_GROUP, LOCAL_GROUP), (), "new_literature_fusion")
        return RetrievalDecision("web", (WEB_GROUP,), (), "new_literature_web")
    if research:
        if local_corpus_match:
            return RetrievalDecision(
                "fusion", (WEB_GROUP, LOCAL_GROUP), (), "research_fact_fusion")
        return RetrievalDecision("web", (WEB_GROUP,), (), "research_fact_web")
    return RetrievalDecision("none", (), (), "no_external_source_needed")

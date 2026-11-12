# -*- coding: utf-8 -*-
"""Single general-purpose tool-using Agent graph.

The model owns retrieval planning and stopping. Code enforces one wall-clock
deadline, executes selected tools, and stores the unmodified final answer.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import math
import os
import re
import time
import uuid
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph

from src.config import get_deepseek_model, settings
from src.core.cache_metrics import emit_usage_from_response
from src.core.model_capabilities import model_supports_images
from src.core import figures
from src.core.record_label import display_label, scrub_internal_keys
from src.core.turn_notices import is_incomplete, notice
from src.core.progress_bus import (
    emit_answer_start, emit_status, emit_text,
    emit_tool_call_start, emit_tool_executing, emit_tool_result,
)
from src.core.stream_llm import stream_llm_response
from src.core.public_progress import (
    emit_public_progress, public_progress_text, reset_emitted,
)
from src.graph.state import AgentState
from src.graph.tool_availability import available_tool_names, runtime_env
from src.graph.retrieval_router import decide_retrieval
from src.graph.tool_policy import RETRIEVAL_TOOLS, resolve_tool_policy
from src.prompts.loader import assemble_system_prompt

logger = logging.getLogger(__name__)

_FINAL_INSTRUCTION = (
    "现在请根据用户的原始问题，以及本轮已取得的全部网页和本地证据，"
    "直接给出最终回答。用户明确指定的长度、数量和格式优先；默认简洁，"
    "不要把单篇论文请求扩成综述。按问题需要自然组织篇幅和结构；重要事实可直接附上"
    "工具实际返回的 Markdown URL 或 DOI 链接。不要声称做过未完成的核验，"
    "不要输出检索过程或固定格式说明。"
)
_FINAL_RESERVE_MIN_SEC = 35.0
_FINAL_RESERVE_MAX_SEC = 60.0
_NO_PROGRESS_BATCHES_TO_STOP = 2
_MAX_MODEL_TOOL_TURNS = 8
# A request that must end with a file spends its last turns assembling and exporting
# it; without headroom the loop stops right after evidence collection and the
# deliverable never happens.  The budget therefore grows for deliverable requests
# and two turns are reserved for the export itself.
_DELIVERABLE_TURN_HEADROOM = 4


_BUDGET_EXTENSION = 8
_MAX_BUDGET_EXTENSIONS = 3
_MAX_REFLECTIONS = 3


def _arm_off(name: str) -> bool:
    """Ablation switch: an arm is disabled when its env var is truthy.

    CITRUS_REFLECTION_DISABLED=1 removes the explicit reflection gate, leaving
    the plain tool-calling loop; CITRUS_DELIVERY_GATE_DISABLED=1 removes the
    deterministic export the post-answer gate would otherwise force. Both
    default to off so the production behaviour is unchanged.
    """

    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def _tool_turn_budget(query: str) -> int:
    base = int(getattr(settings, "MAX_REACT_STEPS", _MAX_MODEL_TOOL_TURNS) or _MAX_MODEL_TOOL_TURNS)
    base = max(6, min(base, 24))
    if _deliverable_export_requested(query):
        base += _DELIVERABLE_TURN_HEADROOM
        if any("docx" in r.get("formats", []) for r in _deliverable_contract(query)):
            base += 4  # create + batched commits + explicit finalization
    return max(6, min(base, 24))


def _request_timeout_disabled() -> bool:
    try:
        return float(getattr(settings, "AGENT_REQUEST_TIMEOUT_SEC", 0) or 0) <= 0
    except (TypeError, ValueError):
        return False


def _llm_timeout(remaining: float | None) -> float:
    if remaining is None or not math.isfinite(remaining):
        return 300.0
    return max(5.0, min(float(remaining), 300.0))
_MAX_TOOL_CALLS_PER_BATCH = 8
_MAX_SOURCE_ENFORCEMENT_ATTEMPTS = 2
_COMPLETION_TOKEN_BUCKETS = (512, 1024, 1536, 2048, 4096, 8192, 12288, 16384, 32768)
_MAX_PARALLEL_AGENT_TOOLS = 8
_DSML_PREFIX = r"(?:[|｜]{1,2}\s*DSML\s*[|｜]{1,2}\s*)?"
_DSML_PROTOCOL_RE = re.compile(
    r"<\s*" + _DSML_PREFIX + r"(?:calls|invoke|parameter)\b|"
    r"\binvoke\s+name\s*=|\bparameter\s+name\s*=|"
    r"</?\s*(?:tool_call|tool_calls|function_call|function_calls)\b",
    re.I,
)
_DSML_CALLS_OPEN_RE = re.compile(r"<\s*" + _DSML_PREFIX + r"calls\s*>", re.I)
_DSML_CALLS_CLOSE_RE = re.compile(r"</\s*" + _DSML_PREFIX + r"calls\s*>", re.I)
_DSML_INVOKE_OPEN_RE = re.compile(
    r"<\s*" + _DSML_PREFIX + r"invoke\b(?P<attrs>[^>]*)>", re.I)
_DSML_INVOKE_CLOSE_RE = re.compile(r"</\s*" + _DSML_PREFIX + r"invoke\s*>", re.I)
_DSML_PARAMETER_RE = re.compile(
    r"<\s*" + _DSML_PREFIX + r"parameter\b(?P<attrs>[^>]*)>"
    r"(?P<value>.*?)"
    r"</\s*" + _DSML_PREFIX + r"parameter\s*>", re.I | re.S)
_DSML_ATTR_RE = re.compile(r"([\w:-]+)\s*=\s*([\"'])(.*?)\2", re.S)
_RETRIEVAL_CUES = re.compile(
    r"最新|进展|论文|文献|研究|证据|来源|核实|检索|查找|查询|搜索|查一下|查一查|搜一下|找一下|查查|资料|数据库|知识库|"
    r"详细|机制|过程|为什么|如何|比较|对比|设计|数据|年份|doi|pmid|"
    r"审查|检查数据|数据质量|异常值|离群|重复|缺失值|表格|参考文献核验|doi核验|图像复核|图片重复|补充数据|补充材料|"
    r"课题记录|项目记录|实验方案|实验设计|复现|导出|csv|tsv|xlsx|xlsm?|pdf|docx|"
    r"记住|记一下|记下来|加入记忆|长期记忆|保存|存一下|备忘|回顾课题|之前记录|以前保存|语料缺口|覆盖范围|"
    r"recent|latest|paper|literature|study|evidence|source|verify|search|look\s*up|lookup|retrieve|accession|registry\s+number|"
    r"mechanism|process|compare|design|data|doi|pmid|outlier|audit|csv|xlsx|protocol", re.I)
_WEB_RETRIEVAL_CUES = re.compile(
    r"最新|进展|论文|文献|参考文献|引用|研究|证据|来源|检索|搜索|搜一下|查一下|查一查|查查|找一下|"
    r"PubMed|数据库|知识库|DOI|PMID|literature|papers?|studies|evidence|sources?|search|look\s*up|lookup|"
    r"retrieve|accession|registry\s+number|UCR|CRC\s*[-#]?\s*\d{3,}|latest|citations?", re.I)

_EXPLICIT_LOCAL_TOOL_REQUEST = re.compile(
    r"\b(?:please\s+)?(?:must\s+)?(?:use|call|run|invoke)\b.{0,60}"
    r"\blocal[_\s-]hybrid[_\s-]search\b|"
    r"\blocal[_\s-]hybrid[_\s-]search\b.{0,50}\b(?:use|call|run|invoke)\b", re.I)


from src.graph.export_contract import (
    artifact_matches_requirement as _artifact_matches_requirement,
    deliverable_contract as _deliverable_contract,
    deliverable_export_requested as _deliverable_export_requested,
    deliverable_match_map as _deliverable_match_map,
    deliverable_tool_groups as _deliverable_tool_groups,
    deliverables_satisfied as _deliverables_satisfied,
    forced_export_plan as _forced_export_plan,
    forced_export_title as _forced_export_title,
    observed_gap_rows as _observed_gap_rows,
    svg_from_answer as _svg_from_answer,
)
from src.graph.intent_text import (
    intent_clauses as _intent_clauses,
    intent_with_negation as _intent_with_negation,
    negated_after as _negated_after,
    negated_before as _negated_before,
)


def _explicit_local_tool_request(query: str) -> bool:
    """Detect a direct request to run the local hybrid-search capability."""
    text = str(query or "")
    for match in _EXPLICIT_LOCAL_TOOL_REQUEST.finditer(text):
        prefix = text[max(0, match.start() - 48):match.start()]
        if re.search(r"(?:do\s+not|don't|never|must\s+not|not)\s+(?:please\s+)?$",
                     prefix, re.I):
            continue
        return True
    return False


def _source_scope(query: str) -> str:
    """Respect explicit user source constraints before exposing tools."""
    q = str(query or "").lower()
    if re.search(r"(?:融合搜索|融合检索|本地.{0,8}联网|(?:仅|只).{0,4}本地.{0,8}(?:搜索|检索))"
                 r".{0,8}(?:是什么|什么意思|含义|如何理解)", q):
        return "auto"
    if re.search(r"(?:只|仅)(?:(?:查|用|看|进行|搜索|检索)(?:一下|一次)?)?(?:本地|本地库|知识库)"
                 r"|(?:只|仅)(?:在|从)(?:本地|本地库|知识库)(?:查|检索|搜索)"
                 r"|(?:只|仅)(?:在|从)(?:本地|本地库|知识库).{0,24}(?:查|检索|搜索)"
                 r"|不要联网|不许联网|仅限本地", q):
        return "local"
    if re.search(r"(?:只|仅)(?:(?:查|用|看|进行|搜索|检索)(?:一下|一次)?)?(?:联网|网络|网页)"
                 r"|(?:只|仅)(?:在|从)(?:联网|网络|网页)(?:查|检索|搜索)"
                 r"|(?:只|仅)(?:在|从)(?:联网|网络|网页).{0,24}(?:查|检索|搜索)"
                 r"|不要查本地|不查本地|不要用知识库|仅限联网", q):
        return "web"
    if re.search(
            r"\b(?:only|just|exclusively)\b.{0,40}\b(?:local(?:[_\s-]hybrid[_\s-]search)?|local\s+(?:corpus|database|knowledge\s+base))\b|"
            r"\b(?:do\s+not|don't|without)\s+(?:use\s+)?(?:the\s+)?(?:web|internet|online\s+search)\b",
            q, re.I):
        return "local"
    if re.search(
            r"\b(?:only|just|exclusively)\b.{0,40}\b(?:web|internet|online\s+search)\b|"
            r"\b(?:do\s+not|don't)\s+(?:use\s+)?(?:the\s+)?local\s+(?:corpus|search|knowledge\s+base)\b",
            q, re.I):
        return "web"
    mentions_both = (re.search(r"(?:联网|網絡|网络|网页).{0,16}(?:本地|知识库)|"
                               r"(?:本地|知识库).{0,16}(?:联网|网络|网页)", q)
                     or re.search(r"(?:融合|结合).{0,8}(?:本地|知识库).{0,8}(?:联网|网络|网页)|"
                                  r"(?:融合|结合).{0,8}(?:联网|网络|网页).{0,8}(?:本地|知识库)", q))
    fusion_action = re.search(r"融合|结合|同时查|一起查|交叉核对|对照本地", q)
    if ((mentions_both and fusion_action)
            or (fusion_action and re.search(r"搜索|检索|查询|核对", q))
            or re.search(r"同时查|一起查|交叉核对|对照本地", q)):
        return "fusion"
    if re.search(r"(?:请|帮我|要求|需要|进行).{0,10}(?:本地|本地库|知识库).{0,8}(?:查|检索|搜索)", q):
        return "local"
    if re.search(r"(?:请|帮我|要求|需要|进行).{0,10}(?:联网|网络|网页).{0,8}(?:查|检索|搜索)", q):
        return "web"
    return "auto"


def _explicit_literature_request(query: str) -> bool:
    return bool(re.search(
        r"文献|论文|参考文献|引用核验|文献核验|查证引用|检索.{0,8}(?:研究|文章|论文)|"
        r"找.{0,6}(?:论文|文献)|查.{0,6}(?:论文|文献)|"
        r"literature|references?|papers?|studies|scholarly sources|doi verification",
        str(query or ""), re.I))


def _local_corpus_matches(query: str) -> bool:
    """Whether the current manifest has a family explicitly relevant to the query."""
    from src.retrieval.corpus_registry import load_corpus_registry
    return bool(load_corpus_registry().matching_families(query))


_DATA_AUDIT_INTENT = re.compile(
    r"(?:审查|检查|核查|分析).{0,10}(?:原始)?(?:实验)?(?:数据|数值|表格|工作簿|源数据|CSV|TSV|Excel|XLSX|XLSM?)|"
    r"(?:实验)?(?:数据|数值|表格|工作簿|源数据|CSV|TSV|Excel|XLSX|XLSM?).{0,10}(?:异常|质量|合理性|审查|检查|核查|离群|重复|缺失)|"
    r"\b(?:audit|check|review|inspect)\b.{0,18}\b(?:data|spreadsheet|table|workbook)\b",
    re.I)


def _explicit_data_audit(query: str) -> bool:
    """Detect an explicit request to audit a data table.

    Uses the shared clause/negation helper so "不要审查数据" stays off while a
    later clause ("…但是请审查") can still turn it on.
    """
    return _intent_with_negation(query, _DATA_AUDIT_INTENT)


# Reference-verification intent.  The original pattern only recognised
# "参考文献…核验" and "核验…DOI", so common phrasings such as
# "核对参考文献的 DOI 是否正确" (W02) or "核对参考文献及 DOI" (R01) did not
# enable the dedicated tool.  "核对"/"核实"/"验证"/"确认真实性" are all
# equivalent user intents, so they are folded into one predicate that is only
# consulted when the message is actually about references or citations.
_REFERENCE_CONTEXT = re.compile(
    r"参考文献|文献|引用|citation|reference|bibliograph|书目|doi|题录", re.I)
_REFERENCE_ACTION = re.compile(
    r"核对|核实|核验|核查|检查|验证|查验|查证|确认|辨真伪|真伪|真实性|是否存在|"
    r"是否正确|是否准确|是否真实|有没有|错漏|"
    r"verify|check|validate|confirm|authenticate", re.I)


def _explicit_reference_check(query: str) -> bool:
    """Detect a request to verify reference/citation metadata.

    Guarded by a reference-context keyword so that unrelated "核对"/"检查"
    (e.g. checking a spreadsheet) does not pull in the reference tool.
    Negations are honoured per clause, so "只列出参考文献，不要核验" stays off.
    """
    text = str(query or "")
    for clause in _intent_clauses(text):
        if not _REFERENCE_CONTEXT.search(clause):
            continue
        for match in _REFERENCE_ACTION.finditer(clause):
            if _negated_before(clause, match.start()):
                continue
            if _negated_after(clause, match.end()):
                continue
            return True
    return False


def _main_model_supports_image_input() -> bool:
    """Resolve image support from the actually selected provider/model pair."""
    return model_supports_images(
        getattr(settings, "RESOLVED_PROVIDER_ID", "deepseek"),
        str(get_deepseek_model() or ""),
    )


def _is_simple_query(query: str, scope: str) -> bool:
    if scope != "auto":
        return False
    q = str(query or "").strip()
    if not q or _RETRIEVAL_CUES.search(q):
        return False
    return len(q) <= 48 and not re.search(r"[?？].*[?？]|以及|并且|分别|综述|全面", q)


_EXPLICIT_VISUALIZATION_REQUEST = re.compile(
    # "画一张柑橘黄龙病传播途径示意图" is 12 chars after 画 and used to miss the
    # old {0,8} window, so an explicit drawing request arrived with draw_svg
    # unbound and the model drew inside the answer instead.
    r"画.{0,16}(?:图|示意图|流程图|通路图|结构图)|绘制|绘图|制图|做成图|画成图|"
    r"示意图|流程图|通路图|可视化|\bsvg\b|\bplot\b|\bchart\b|\bfigure\b|flowchart|diagram",
    re.I)


def _explicit_visualization_request(query: str) -> bool:
    return bool(_EXPLICIT_VISUALIZATION_REQUEST.search(str(query or "")))


_EXPORT_INTENT = re.compile(
    r"导出|下载|保存为|存为|生成.{0,6}(?:文件|报告|文档|方案|表格|清单|记录)",
    re.I)
_EXPORT_WORDS = re.compile(
    r"导出|下载|保存为|存为|export|download|RIS|BibTeX|Zotero|"
    # "生成可编辑的比较文件" (W19) and similar phrasings never matched the old
    # "生成(一个)?文件" pattern because a qualifier sits between the verb and
    # the noun.  Accept an optional qualifier of bounded length.
    r"生成.{0,12}(?:文件|报告|文档|方案|表格|清单|记录)|"
    r"可(?:编辑|下载).{0,8}(?:文件|报告|文档)|"
    r"make.{0,10}(?:a\s+)?(?:file|report|document)",
    re.I)


def _explicit_export_request(query: str) -> bool:
    """Detect a request to produce a downloadable artifact.

    Honours negation per clause so "不要导出" does not enable export tools.
    """
    return _intent_with_negation(query, _EXPORT_WORDS)


def _explicit_supplement_request(query: str) -> bool:
    return bool(re.search(r"(?:获取|下载|查找|找出|retrieve|download|fetch).{0,18}(?:补充数据|补充材料|supplement(?:ary)? data|source data)|"
                          r"(?:补充数据|补充材料|supplement(?:ary)? data|source data).{0,18}(?:获取|下载|查找|retrieve|download|fetch)", query, re.I))


_EXPLICIT_WEB_SEARCH_ACTION = re.compile(
    r"(?:联网|网上|在线|网页|网络).{0,8}(?:搜索|检索|查找|找)|"
    r"(?:搜索|检索|查找|搜集|找).{0,12}(?:相关|更多|最新|近年|其他)?"
    r"(?:论文|文献|研究|文章|来源|资料)|"
    r"\b(?:search|find|look\s*up).{0,32}\b(?:papers?|literature|studies|sources?)\b",
    re.I,
)


def _read_uploaded_paper_without_search(query: str, scope: str,
                                       has_paper_attachment: bool) -> bool:
    """Treat an uploaded paper as the source unless the user asks to search."""
    return bool(
        has_paper_attachment
        and scope == "auto"
        and not _EXPLICIT_WEB_SEARCH_ACTION.search(str(query or ""))
        and not _explicit_reference_check(query)
        and not _explicit_supplement_request(query)
    )


def _explicit_experiment_design_request(query: str) -> bool:
    return bool(re.search(r"实验设计|设计实验|实验方案|制定.{0,8}方案|protocol design|design an experiment", query, re.I))


_PAPER_ANALYSIS_INTENT = re.compile(
    r"(?:解读|解析|总结|梳理|概括|提炼|比较|对比).{0,14}(?:论文|文章|研究|文献|方法|结果|文献间|差异)|"
    r"(?:论文|文章|研究|文献).{0,14}(?:方法|结果|结论|要点|差异|比较|对比)|"
    # "生成可编辑的比较文件" (W19): the artifact noun is explicit but the
    # comparison target is left implicit from context.  When the message asks
    # for a "comparison file/report" together with export intent, that is a
    # paper-comparison request in practice.
    r"(?:生成|制作|整理|输出).{0,10}(?:比较|对比|差异).{0,6}(?:文件|报告|表格|文档)|"
    r"(?:比较|对比|差异).{0,6}(?:文件|报告|表格|文档)|"
    r"(?:explain|summari[sz]e|analy[sz]e|compare).{0,14}(?:paper|article|study|papers)",
    re.I)


def _explicit_paper_analysis_request(query: str) -> bool:
    return _intent_with_negation(query, _PAPER_ANALYSIS_INTENT)


def _explicit_note_save_request(query: str) -> bool:
    if _explicit_export_request(query) and re.search(r"课题记录|项目记录|研究记录|project notes", query, re.I):
        return False
    return bool(re.search(r"(?:保存|记录|记住|记下来|加入课题记录).{0,18}(?:课题|项目|研究)|"
                          r"(?:课题|项目|研究).{0,18}(?:保存|记录|记住)", query, re.I))


def _explicit_note_recall_request(query: str) -> bool:
    return bool(re.search(r"回顾|查阅.{0,8}(?:课题|项目)记录|之前.{0,8}(?:保存|记录)|以前.{0,8}(?:记录|保存)|recall.{0,12}project notes", query, re.I))


_GAP_CONTEXT = re.compile(
    r"语料|知识库|本地库|corpus|knowledge\s+base", re.I)
_GAP_TOPIC = re.compile(
    r"缺口|覆盖|欠缺|缺失|空白|未收录|共现|gap|coverage|missing", re.I)
# The gap tool produces a report artifact, so "…缺口报告" is itself an export
# request even without an explicit 导出 verb ("整理本地语料中该主题的覆盖缺口
# 报告", G1).  Keep the verb form as well so "检查缺口并导出" still matches.
_GAP_ARTIFACT = re.compile(
    r"报告|清单|表格|文档|文件|report|summary|xlsx|csv", re.I)


def _explicit_gap_export_request(query: str) -> bool:
    """Detect a request for a corpus gap/coverage report.

    Two ways to qualify: (a) explicit export verb + gap context, or (b) the
    message names a gap report artifact.  The old pattern required the literal
    anchors 语料(≤8 chars)缺口, which missed messages where the topic sits
    between them.
    """
    text = str(query or "")
    explicit_export = _explicit_export_request(text)
    for clause in _intent_clauses(text):
        if not (_GAP_CONTEXT.search(clause) and _GAP_TOPIC.search(clause)):
            continue
        if explicit_export or _GAP_ARTIFACT.search(clause):
            return True
    return False


def _scoped_toolset(scope: str, *, query: str = "", has_table: bool = False,
                    has_paper_attachment: bool = False,
                    has_account_files: bool = False,
                    visualization_enabled: bool = False):
    """Return the tools that can actually run in this request's environment.

    Visibility is a function of the environment, not of the question's wording.
    A tool disappears only when a declared precondition is unmet (no attachment
    to inspect, no account file to read, no session to write into, research
    tools disabled) or when the *user said so in so many words* (local-only,
    web-only, or "do not search this turn"). Everything else is advertised and
    the model decides whether calling it is worth a step.

    Two Phase-1 leftovers are deliberately kept until Phase 2B owns them:

    * ``save_research_note`` writes account state, and the executor does not yet
      require an explicit request before running it, so it stays hidden unless
      the user asked to save a note. This is an authorization bridge, not a
      capability guess.
    * ``required_tool_groups`` still drives the old "did the model do the
      required work" check; it is not part of visibility.
    """
    tools, _schemas = _toolset()
    available = {tool.name: tool for tool in tools}
    q = str(query or "")
    paper_only = _read_uploaded_paper_without_search(q, scope, has_paper_attachment)
    policy = resolve_tool_policy(q, scope, paper_only=paper_only)
    if _explicit_note_save_request(q):
        denied: frozenset[str] = frozenset()
    else:
        denied = frozenset({"save_research_note"})
    from src.core.tracing import get_session_id, web_search_enabled
    # Configuration readiness is a fact, not a network probe. Direct scholarly
    # APIs remain usable when the separate paid web-search provider lacks a key.
    from src.graph.tool_availability import provider_denials
    env = runtime_env(
        source_scope=scope,
        attachments=1 if has_table else 0,
        account_files=1 if has_account_files else 0,
        session=bool(get_session_id()),
        network=bool(web_search_enabled()),
        retrieval=policy.allow_retrieval,
        denied=denied | policy.deny | provider_denials(settings),
    )
    return [available[name] for name in available_tool_names(env) if name in available], []


def _answer_profile(query: str) -> tuple[int, str]:
    """Choose a bounded completion budget and concise task-specific guidance.

    Output caps are rounded to a small set of buckets so user-specified lengths
    do not create an unbounded number of cached HTTP clients.
    """
    q = " ".join(str(query or "").lower().split())
    paper_recommendation = (
        bool(re.search(r"推荐|找|检索|提供|给我|列出", q))
        and bool(re.search(r"论文|文献|paper|article", q, re.I))
        and not bool(re.search(r"撰写|写作|写一篇|分析全文|详细介绍|深入分析|逐篇比较", q))
        and not bool(re.search(
            r"\d+\s*篇|[两二三四五六七八九十几多]+\s*篇|多篇|若干篇", q))
    )
    explicit = re.search(
        r"(\d+)\s*(tokens?|字|字符|词|句|句话|条|点|段|篇)(?:以内|左右|上下|最多|不超过|内)?",
        q, re.I)
    # 交付物任务里的“56 条/20 篇”指条目数，不是回答篇幅；必须先于 explicit 判定，
    # 否则工具参数会按“56 条”换算成很紧的预算并在中途被截断（表现为 [ERR_PARSE]）。
    deliverable = bool(re.search(
        r"导出|生成(?:文件|表格|清单|报告|书目)|核验表|审查表|标注|文献证据|"
        r"引用表|书目|ris|bibtex|xlsx|表格文件", q, re.I))
    if deliverable:
        target = 16384
        guidance = ("本次要产出可下载文件：工具参数只保留必要字段，长清单分批"
                    "（每批不超过 20 条），一次调用只生成一份文件。")
    elif explicit:
        amount = max(1, int(explicit.group(1)))
        unit = explicit.group(2).lower()
        factor = (1 if unit.startswith("token") else
                  1.7 if unit in ("字", "字符") else
                  2.2 if unit == "词" else
                  240 if unit in ("句", "句话") else
                  260 if unit in ("条", "点") else
                  320 if unit == "段" else 1400)
        target = max(256, int(amount * factor))
        guidance = (f"严格遵守用户指定的篇幅：约 {amount}{explicit.group(2)}以内；"
                    "只保留完成请求所需的信息。")
        if paper_recommendation:
            guidance += ("这是单篇现有论文推荐请求：只给一篇真实且匹配的论文，"
                         "列标题、作者、期刊/年份和实际 DOI/URL，不附综述。")
    else:
        long_form = (not paper_recommendation) and bool(re.search(
            r"深度研究|深入分析|详细(?:解释|说明|分析)?|全面(?:分析|梳理)?|"
            r"系统梳理|文献综述|综述|比较(?:分析)?|对比(?:分析)?|逐篇|逐项|"
            r"完整报告|长文|多章节|写一篇|撰写|comprehensive|in depth|detailed review",
            q, re.I))
        concise = bool(re.search(
            r"只要|只需|仅需|只给|简短|简要|简洁|一句话|直接给|不要展开|"
            r"无需解释|不必解释|简答|tl;?dr|briefly|in one sentence",
            q, re.I))
        # SVG 代码本身就是长输出：按“简短”档给预算会在工具参数中途截断，
        # 表现为反复 [SVG_FORMAT_ERROR]/[SVG_INPUT_MISSING]，最后又被报成超时。
        visual = bool(re.search(
            r"示意图|流程图|架构图|框架图|时序图|关系图|图表|绘图|画一张|画个|画一幅|"
            r"画一下|绘制|插图|作图|diagram|flowchart|schematic|draw", q, re.I))
        if long_form:
            target = 16384
            guidance = "用户要求深入或长篇输出；按任务需要展开，但不要重复或凑章节。"
        elif paper_recommendation:
            target = 4096
            guidance = ("用户只要推荐一篇现有论文：只提供一篇最匹配且可核实的文献，"
                        "列出标题、作者、期刊/年份及实际 DOI 或 URL，最多用一句话说明相关性；"
                        "不要附领域综述或检索过程。")
        elif visual:
            target = 16384
            guidance = ("用户要图：调用 draw_svg 提交一份紧凑 SVG（viewBox 不超过 1000 宽，文字精简），"
                        "再在回答中用 [[FIGURE:n]] 占位符并附一句说明；不要把 SVG 源码直接写进回答。")
        elif concise:
            target = 2048
            guidance = "用户要求简短或只要指定内容；直接给结果，不补充未请求的背景。"
        elif len(q) <= 120 and not re.search(r"为什么|如何|机制|比较|对比|分析|设计", q):
            # 该模型在网关上是 reasoning 模型，推理会先吃掉预算；4096 实测会截断成空答案。
            target = 8192
            guidance = "按普通问答简洁直接回答；仅补充理解答案必需的信息。"
        else:
            target = 8192
            guidance = "默认简洁直接回答；只有解决问题所必需的细节才展开。"

    budget = next((b for b in _COMPLETION_TOKEN_BUCKETS if b >= target),
                  _COMPLETION_TOKEN_BUCKETS[-1])
    return budget, guidance


def _final_reserve_sec() -> float:
    """Scale synthesis time with the request deadline, with safe bounds."""
    request_sec = max(1.0, float(settings.AGENT_REQUEST_TIMEOUT_SEC))
    return min(_FINAL_RESERVE_MAX_SEC,
               max(_FINAL_RESERVE_MIN_SEC, request_sec * 0.30))


def _evidence_fingerprints(messages: list[ToolMessage]) -> set[str]:
    """Identify genuinely new tool output without relying on a fixed turn cap.

    URLs make repeated search results easy to recognize. A normalized content
    digest also catches local BM25 results and fetched pages without URLs.
    Error messages are deliberately excluded: retrying with a changed query
    after a transient failure can still make progress.
    """
    found: set[str] = set()
    for message in messages:
        content = str(getattr(message, "content", "") or "")
        if not content or content.startswith(("[WEB_TOOL_ERROR]", "[TOOL_ERROR]",
                                               "[TOOL_DUPLICATE_SKIPPED]", "[ERR_REQUEST_DEADLINE]")):
            continue
        found.update("url:" + url.rstrip(".,);]")
                     for url in re.findall(r"https?://[^\s<>\]\[\"']+", content))
        normalized = " ".join(content.split())
        if normalized:
            found.add("text:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest())
    return found


_EVIDENCE_DIGEST_ROWS = 5
_EVIDENCE_DIGEST_CHARS = 220


def _evidence_digest(rows: list) -> str:
    """Build a bounded evidence digest for session history.

    Full local record text stays request-scoped, but the digest keeps title,
    DOI and a short excerpt so a later turn can cite the same records instead
    of re-running retrieval (the trace must show what was found, not just that
    something ran).
    """
    lines = []
    for index, row in enumerate((rows or [])[:_EVIDENCE_DIGEST_ROWS], 1):
        if not isinstance(row, dict):
            continue
        title = display_label(row, with_section=True)[:180]
        doi = " ".join(str(row.get("doi") or "").split())[:80]
        source = " ".join(str(row.get("corpus_family") or row.get("source_type")
                              or row.get("_src") or "").split())[:40]
        excerpt = " ".join(str(row.get("text") or "").split())[:_EVIDENCE_DIGEST_CHARS]
        meta = "；".join(part for part in (
            f"DOI: {doi}" if doi else "",
            f"语料: {source}" if source else "") if part)
        lines.append(f"[{index}] {title}" + (f"（{meta}）" if meta else "")
                     + (f"\n    摘录: {excerpt}" if excerpt else ""))
    return "\n".join(lines)


def _safe_persisted_trace(messages: list) -> list:
    """Bound what a turn writes to session history.

    Request-scoped payloads (raw record text beyond the digest, image bytes,
    table cell samples) are trimmed here; execution timing/summary and a
    citable evidence digest are kept so the saved trace matches what ran.
    """
    safe = []
    for message in messages:
        artifact = getattr(message, "artifact", None)
        execution_trace = (artifact.get("_citrus_execution")
                           if isinstance(artifact, dict) else None)
        safe_artifact = ({"_citrus_execution": execution_trace}
                         if isinstance(execution_trace, dict) else {})
        if isinstance(message, ToolMessage) and getattr(message, "name", "") in {
                "local_bm25_search", "local_dense_search", "local_hybrid_search"}:
            rows = ((getattr(message, "artifact", None) or {}).get("main_results") or [])
            if rows:
                digest = _evidence_digest(rows)
                kept = min(len(rows), _EVIDENCE_DIGEST_ROWS)
                body = (f"本地检索已执行，命中 {len(rows)} 条去重候选"
                        f"（会话历史保留前 {kept} 条摘要，供后续轮次直接引用，避免重复检索）。"
                        + (f"\n{digest}" if digest else ""))
            else:
                _status = str((execution_trace or {}).get("status") or "")
                if _status in {"failed", "skipped", "not_run", "unavailable"}:
                    body = ("本轮本地检索未产生候选（该步骤未成功执行，原因见执行记录）；"
                            "不要把“未执行”理解为“语料中没有相关资料”。")
                else:
                    body = "本地检索已完成：未匹配到候选记录。"
            safe.append(ToolMessage(
                content=body,
                tool_call_id=message.tool_call_id, name=message.name, artifact=safe_artifact))
        elif isinstance(message, ToolMessage) and getattr(message, "name", "") == "inspect_uploaded_table":
            # Keep only schema-level hints in session history; sample cell values
            # and previews are request-scoped and must not persist with the trace.
            try:
                profile = json.loads(str(message.content or "{}"))
                profile.pop("raw_preview", None)
                profile.pop("sample_rows", None)
                for field in profile.get("fields", []):
                    field.pop("samples", None)
                    for key in ("min", "median", "max"):
                        field.pop(key, None)
                safe.append(ToolMessage(
                    content=json.dumps(profile, ensure_ascii=False, default=str),
                    tool_call_id=message.tool_call_id, name=message.name, artifact=safe_artifact))
            except Exception:
                safe.append(ToolMessage(
                    content="表格结构已在本轮检查；原始单元格样例未写入会话历史。",
                    tool_call_id=message.tool_call_id, name=message.name, artifact=safe_artifact))
        elif isinstance(message, HumanMessage) and isinstance(message.content, list):
            # Vision payloads are request-scoped; keep a trace placeholder, not
            # base64 image bytes, in session history.
            blocks = []
            for block in message.content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    blocks.append({"type": "text", "text": "[本轮图片附件已用于分析；图像数据未写入会话历史]"})
                else:
                    blocks.append(block)
            safe.append(HumanMessage(content=blocks, additional_kwargs=message.additional_kwargs))
        else:
            safe.append(message)
    return safe


def _parse_dsml_tool_calls(content: str, tools_by_name: dict) -> tuple[bool, list[dict]]:
    """Recover provider-emitted DSML text into calls from this request's allowlist.

    Some OpenAI-compatible endpoints return ``<｜｜DSML｜｜ invoke ...>`` as
    assistant text instead of populating ``AIMessage.tool_calls``. The loop must
    never treat that control text as a user-facing answer. Only fully parsed,
    schema-compatible calls to currently exposed tools are recoverable.
    """
    text = str(content or "")
    if not _DSML_PROTOCOL_RE.search(text):
        return False, []

    calls: list[dict] = []
    cursor = 0
    while True:
        opening = _DSML_INVOKE_OPEN_RE.search(text, cursor)
        if opening is None:
            break
        closing = _DSML_INVOKE_CLOSE_RE.search(text, opening.end())
        if closing is None:
            return True, []

        attrs = {k.lower(): html.unescape(v) for k, _, v in _DSML_ATTR_RE.findall(
            opening.group("attrs") or "")}
        name = attrs.get("name", "").strip()
        tool = tools_by_name.get(name)
        if tool is None:
            return True, []

        args: dict = {}
        block = text[opening.end():closing.start()]
        for parameter in _DSML_PARAMETER_RE.finditer(block):
            param_attrs = {k.lower(): html.unescape(v)
                           for k, _, v in _DSML_ATTR_RE.findall(
                               parameter.group("attrs") or "")}
            param_name = param_attrs.get("name", "").strip()
            if not param_name or param_name in args:
                return True, []
            value = html.unescape(parameter.group("value").strip())
            if param_attrs.get("string", "true").lower() != "true":
                try:
                    value = json.loads(value)
                except (TypeError, ValueError):
                    pass
            args[param_name] = value

        # Reject malformed or schema-incompatible calls before dispatch. The
        # normal tool executor still performs its own validation and sandboxing.
        input_model = getattr(tool, "args_schema", None)
        fields = (getattr(input_model, "model_fields", None)
                  or getattr(input_model, "__fields__", None))
        if fields:
            allowed_fields = set(fields)
            if not args or set(args) - allowed_fields:
                return True, []
            required = {
                field_name for field_name, field in fields.items()
                if (getattr(field, "is_required", lambda: False)()
                    if callable(getattr(field, "is_required", None))
                    else getattr(field, "required", False))
            }
            if required - set(args):
                return True, []

        calls.append({"name": name, "args": args,
                      "id": "dsml_" + uuid.uuid4().hex,
                      "type": "tool_call"})
        cursor = closing.end()

    return True, calls


def _sanitize_history_tool_protocol(messages: list) -> list:
    """Prevent previously persisted DSML leaks from being echoed on follow-ups."""
    cleaned = []
    for message in messages:
        if (isinstance(message, AIMessage)
                and not getattr(message, "tool_calls", None)
                and _DSML_PROTOCOL_RE.search(str(getattr(message, "content", "") or ""))):
            logger.warning("Removed unexecuted DSML tool-call text from conversation history")
            cleaned.append(AIMessage(content="[上一轮工具调用未被执行，因此没有形成有效回答。]"))
        else:
            cleaned.append(message)
    return cleaned


def _toolset():
    """Expose retrieval plus task-specific research workflow tools."""
    from src.tools import _TOOL_REGISTRY_BY_NAME

    allowed = {"deepseek_search", "deepseek_fetch", "deepseek_fetch_paper", "draw_svg",
               "local_hybrid_search", "read_account_file", "search_literature"}
    research_enabled = os.getenv("CITRUS_RESEARCH_TOOLS_ENABLED", "true").strip().lower() not in {
        "0", "false", "no", "off"}
    if research_enabled:
        allowed.update({
            "inspect_uploaded_table", "audit_uploaded_data",
            "verify_uploaded_references", "fetch_and_audit_open_supplements", "export_literature_evidence",
            "export_paper_analysis", "export_experiment_design", "save_research_note",
            "recall_research_notes", "export_research_notes", "export_corpus_gap_report",
            "word_document", "export_table",
        })
    return [tool for name, tool in _TOOL_REGISTRY_BY_NAME.items()
            if name in allowed], []


def _tool_call_dict(tc) -> dict:
    if isinstance(tc, dict):
        return {"id": tc.get("id", ""), "name": tc.get("name", ""),
                "args": dict(tc.get("args") or {})}
    return {"id": getattr(tc, "id", ""), "name": getattr(tc, "name", ""),
            "args": dict(getattr(tc, "args", None) or {})}


def _logical_key(tool_name: str, call: dict, step: dict | None = None) -> str:
    """Group attempts of the same logical step without exposing arguments.

    Built from the tool name plus a coarse target (explicit title, or the
    attachment slot), never from the payload itself, so a retried call updates
    one timeline row instead of stacking new ones.
    """
    args = call.get("args") if isinstance(call, dict) else {}
    args = args if isinstance(args, dict) else {}
    if tool_name == "word_document":
        target = "|".join(str(args.get(k) or "") for k in ("document_id", "operation", "batch_id", "expected_version", "offset"))
        return "word_document:" + hashlib.sha256(target.encode()).hexdigest()[:20]
    for key in ("title", "case_id", "case", "output_name", "note"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return f"{tool_name}:{value.strip()[:48]}"
    target = "|".join(str(args.get(name) or "") for name in
                      ("file_index", "file", "file_name", "path", "query", "doi", "arxiv_id"))
    if target.strip("|"):
        return f"{tool_name}:{target[:48]}"
    return tool_name


def _fallback_summary(name: str, content: str) -> str:
    """给没有专用摘要的工具产出可读的一行说明（来自真实返回内容）。"""
    text = str(content or "").strip()
    if not text:
        return ""
    match = re.match(r"^文件：([^\[\n]{1,80}?)\s*\[本文件共提取\s*([\d,]+)\s*个字符", text)
    if match:
        return f"已读取文件 {match.group(1).strip()}（{match.group(2)} 字符）"
    first = text.split("<untrusted_file_content>", 1)[0].strip()
    first = re.sub(r"^\[[A-Z_]+\]\s*", "", first)
    first = " ".join(first.split())
    if not first:
        return ""
    return first[:60]


_ARTIFACT_MARKDOWN_LINK = re.compile(
    r"\[([^\]]*)\]\((/api/v2/session/[A-Za-z0-9_-]{8,128}/artifacts/[a-f0-9]{32})\)")
_ARTIFACT_BARE_LINK = re.compile(
    r"(?<!\()(/api/v2/session/[A-Za-z0-9_-]{8,128}/artifacts/[a-f0-9]{32})")


_DELIVERY_DENIAL_RE = re.compile(
    r"(?:本轮|目前|现在)?\s*(?:没有|无)(?:可用的)?(?:文件)?"
    r"(?:导出|下载|保存)(?:通道|能力|接口|链接)?|"
    r"导出(?:工具|步骤|功能)?\s*(?:未|没有|并未)(?:实际)?(?:执行|运行|生效|成功)|"
    r"(?:未|没有|尚未)\s*(?:实际)?生成\s*(?:HTML|XLSX|PDF)?\s*报告|"
    r"(?:报告|文件|HTML|XLSX|PDF)[^。！？\n]{0,10}(?:未|没有|尚未)(?:实际)?生成|"
    r"(?:没有|无法给出|无法提供|不能提供)\s*(?:可)?下载(?:位置|链接)|"
    r"(?:未|没有|尚未)生成(?:任何)?文件|"
    r"(?:需要|需|要)重新(?:执行|尝试|运行)?导出|"
    r"(?:如果|如需|需要我|要我|你可以)[^。！？\n]{0,24}(?:导出|保存为)[^。！？\n]{0,16}"
    r"(?:报告|文件|HTML)|"
    r"(?:该项|本项)?交付(?:尚|仍|还)?未完成",
    re.I)
_DELIVERY_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?])|\n")


def _reconcile_delivery_claims(answer: str, delivered: bool) -> str:
    """Drop now-false "I could not create a file" claims after a real delivery.

    The model writes its prose before the delivery gate runs, so it can claim
    "no export channel / the export tool never ran" while the gate then supplies
    a verified file. Leaving both in one answer is a contradiction the user sees.
    Only denial clauses are removed (structure is preserved), and one correction
    sentence explains the change; the caller appends the real links.
    """
    text = str(answer or "")
    if not text or not delivered or not _DELIVERY_DENIAL_RE.search(text):
        return text
    parts = re.split(r"([。！？!?]|\n)", text)
    kept: list[str] = []
    removed = 0
    for index in range(0, len(parts), 2):
        content = parts[index]
        separator = parts[index + 1] if index + 1 < len(parts) else ""
        if content and _DELIVERY_DENIAL_RE.search(content):
            removed += 1
            # Keep a paragraph break, drop the sentence's terminator, so no
            # stray "。" is left where the removed claim used to be.
            kept.append("\n" if separator == "\n" else "")
            continue
        kept.append(content + separator)
    if not removed:
        return text
    body = "".join(kept)
    body = "\n".join(
        line for line in body.splitlines()
        if not line.lstrip().startswith("本轮未完成"))
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    correction = (
        "（交付状态更正：本轮已生成并通过校验的可下载文件，链接见文末。）")
    return (correction + "\n\n" + body) if body else correction


def _sanitize_answer_artifact_links(answer: str) -> str:
    """Keep only registry-, owner-, disk- and hash-verified artifact links visible."""
    text = str(answer or "")
    links = {
        match.group(2) for match in _ARTIFACT_MARKDOWN_LINK.finditer(text)
    } | set(_ARTIFACT_BARE_LINK.findall(text))
    if not links:
        return text
    try:
        from src.core.tracing import get_client_request_id, get_session_id, get_user_id
        from src.research.tools import validate_artifact_links
        validations = validate_artifact_links(
            sorted(links), session_id=get_session_id(), owner_user_id=get_user_id(),
            request_id=get_client_request_id())
        valid = {
            str(item.get("link") or "")
            for item in validations if item.get("valid")
        }
    except Exception:
        logger.warning("Could not validate final-answer artifact links", exc_info=True)
        valid = set()

    def markdown(match: re.Match) -> str:
        label, link = match.group(1), match.group(2)
        if link in valid:
            return match.group(0)
        return f"{label}（文件链接未通过交付校验）" if label else \
            "文件链接未通过交付校验"

    text = _ARTIFACT_MARKDOWN_LINK.sub(markdown, text)
    return _ARTIFACT_BARE_LINK.sub(
        lambda match: match.group(1) if match.group(1) in valid
        else "（文件链接未通过交付校验）", text)


_ARTIFACT_LINK_LABELS = {
    "audit_uploaded_data": "下载数据审查 Excel",
    "verify_uploaded_references": "下载 DOI 核验表",
    "fetch_and_audit_open_supplements": "下载补充材料",
    "export_literature_evidence": "下载文献证据表",
    "export_paper_analysis": "下载论文解读",
    "export_experiment_design": "下载实验设计",
    "export_research_notes": "下载课题记录",
    "export_corpus_gap_report": "下载语料缺口报告",
    "draw_svg": "下载 SVG",
}


def _append_verified_artifact_links(answer: str, records: list[dict]) -> str:
    """Expose every independently verified current-turn artifact to the user.

    Tool output is model-visible context, so the model can omit a download URL
    while correctly describing the generated file. File delivery must not
    depend on whether the model copied that URL into its prose. Only records
    that already passed registry, ownership, disk, format and SHA validation
    are eligible.
    """
    text = str(answer or "")
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for record in records or ():
        if not isinstance(record, dict):
            continue
        name = str(record.get("name") or "generated_file")
        label = _ARTIFACT_LINK_LABELS.get(name, "下载生成文件")
        for validation in record.get("artifact_validations") or ():
            if not isinstance(validation, dict) or not validation.get("valid"):
                continue
            link = str(validation.get("link") or "").strip()
            if not link or link in seen or link in text:
                continue
            seen.add(link)
            links.append((label, link))
    if not links:
        return text
    section = "\n\n**可下载文件**\n" + "\n".join(
        f"- [{label}](<{link}>)" for label, link in links)
    return text.rstrip() + section


def _emit_completed_tool_step(call: dict, message: ToolMessage,
                              tool_status_sink: list[dict] | None = None) -> dict[str, Any]:
    """Publish one completed tool as soon as it finishes, with no raw payload."""
    name = str(call.get("name") or "")
    content = str(getattr(message, "content", "") or "")
    artifact = getattr(message, "artifact", None) or {}
    artifact_validations: list[dict] = []
    step: dict[str, Any] = {
        "status": "failed", "reason_code": "status_unavailable", "retryable": True,
    }
    try:
        from src.tools.outcome import safe_tool_step
        step = safe_tool_step(name, content, artifact)
        # Tool links are claims until the artifact registry, ownership record and
        # bytes on disk corroborate them. Model-authored Markdown never counts.
        artifact_validations: list[dict] = []
        claimed_links = tuple(step.get("artifacts") or ())
        try:
            from src.core.progress_bus import get_turn_scope
            from src.core.tracing import get_client_request_id, get_user_id
            from src.research.tools import validate_artifact_links
            if claimed_links:
                active_session, _ = get_turn_scope()
                artifact_validations = validate_artifact_links(
                    claimed_links, session_id=active_session,
                    owner_user_id=get_user_id(),
                    request_id=get_client_request_id())
                verified_links = tuple(
                    str(validation.get("link") or "")
                    for validation in artifact_validations
                    if validation.get("valid") and validation.get("link")
                )
                step["artifacts"] = list(verified_links)
                if len(verified_links) != len(claimed_links):
                    step["status"] = "partial" if verified_links else "failed"
                    step["reason_code"] = (
                        "artifact_partially_verified" if verified_links
                        else "artifact_validation_failed"
                    )
                    step["retryable"] = True
        except Exception:
            logger.warning("Artifact validation unavailable for %s", name, exc_info=True)
            if claimed_links:
                step["artifacts"] = []
                step["status"] = "failed"
                step["reason_code"] = "artifact_validation_unavailable"
                step["retryable"] = True
        if tool_status_sink is not None:
            tool_status_sink.append({
                "name": name,
                "status": step["status"],
                "reason": step["reason_code"],
                # Links are held in the tool result ledger; only the verified
                # count crosses into the task result contract.
                "artifact_count": len(step.get("artifacts") or []),
                "artifact_validations": artifact_validations,
            })
        logical_key = _logical_key(name, call, step)
        attempt = max(1, int(call.get("_attempt") or 1))
        step["logical_key"] = logical_key
        step["attempt"] = attempt
        summary = step["summary"] or _fallback_summary(name, content)
        if name == "local_hybrid_search" and isinstance(artifact, dict):
            rows = artifact.get("main_results") or []
            cache_status = str(artifact.get("rag_cache_status") or "")
            if cache_status in {"hit", "miss", "disabled"}:
                from src.core.progress_bus import emit_progress
                emit_progress("retrieval_cache", {"kind": "rag", "status": cache_status})
            if step["status"] == "partial":
                errors = artifact.get("errors") or {}
                failed_channels = ", ".join(str(key).upper() for key in errors)
                summary = (f"找到 {len(rows)} 条候选资料；部分检索步骤未完成"
                           + (f"（{failed_channels}）" if failed_channels else ""))
            elif step["status"] == "no_results":
                summary = "本地资料检索已完成，没有找到候选资料"
            elif step["status"] == "complete":
                summary = f"本地资料检索已完成，找到 {len(rows)} 条候选资料"
        from src.core.progress_bus import emit_tool_result, tool_action_hint
        action_hint = tool_action_hint(name, call.get("args") or {})
        trace_record = emit_tool_result(
            name, content, str(call.get("id") or ""),
            summary=summary, outcome=step, action_hint=action_hint,
            logical_key=logical_key, attempt=attempt)
        # A2: 全文入库。消息里的 content 是给模型的裁剪版，刷新后用户只能
        # 看到它；这里按 tool_call_id 另存一份完整结果，供前端"查看原文"。
        try:
            from src.core import tool_results as _tool_results
            from src.core import turn_source_ledger as _source_ledger
            from src.core.progress_bus import get_turn_scope as _get_scope
            _sid, _rid = _get_scope()
            _tool_results.record(
                tool_call_id=str(call.get("id") or ""),
                session_id=_sid, request_id=_rid, tool_name=name,
                status=str(trace_record.get("status") or ""),
                args=call.get("args") or {}, result_text=content,
                error_text=(content if step.get("status") == "failed" else ""),
                elapsed_ms=int(trace_record.get("elapsed_ms") or 0),
                summary=str(trace_record.get("summary") or ""))
            if isinstance(artifact, dict) and step.get("status") in {
                    "complete", "partial", "no_results"}:
                _source_ledger.record_tool_sources(
                    _sid, _rid, artifact=artifact, result_text=content)
        except Exception:
            logger.debug("persist full tool result failed", exc_info=True)
        # ToolMessage.artifact is deliberately excluded from model-visible
        # content, but is checkpointed with the message. Persist only the
        # sanitized execution record so refresh/history can reproduce real
        # completion order and status without storing tool arguments or prose.
        persisted_artifact = dict(artifact) if isinstance(artifact, dict) else {}
        if artifact_validations:
            persisted_artifact["_artifact_validations"] = artifact_validations
        persisted_artifact["_citrus_execution"] = {
            key: trace_record[key] for key in (
                "status", "reason_code", "summary", "elapsed_ms",
                "completed_at_ms", "action_hint")
        }
        message.artifact = persisted_artifact
        return {**step, "artifact_validations": artifact_validations}
    except Exception:
        # 这里是“工具跑完了但状态发不出去”的关键失败点；DEBUG 级会把它藏起来，
        # 界面上只看到“执行状态无法读取”，无法定位。
        logger.warning("Unable to publish tool completion event", exc_info=True)
        try:
            from src.core.progress_bus import emit_tool_result
            emit_tool_result(
                name, content, str(call.get("id") or ""), is_error=True,
                summary="这一步的执行状态暂时无法读取",
                outcome={"status": "failed", "reason_code": "status_unavailable"})
        except Exception:
            pass
        return {"status": "failed", "reason_code": "status_unavailable",
                "retryable": True, "artifact_validations": artifact_validations}


def _verified_artifact_validations(messages) -> list[dict[str, Any]]:
    """Return only registry-verified artifact records attached to tool results."""
    validations: list[dict[str, Any]] = []
    for message in messages or ():
        if not isinstance(message, ToolMessage):
            continue
        artifact = getattr(message, "artifact", None) or {}
        if not isinstance(artifact, dict):
            continue
        for validation in artifact.get("_artifact_validations") or ():
            if not isinstance(validation, dict):
                continue
            record = dict(validation)
            record.setdefault("producer_tool", str(getattr(message, "name", "") or ""))
            if record.get("valid"):
                validations.append(record)
    return validations


def _has_export_artifact(messages, requirements=None) -> bool:
    validations = _verified_artifact_validations(messages)
    if requirements is None:
        return bool(validations)
    return _deliverables_satisfied(validations, requirements)


def _self_export_names(messages, requirements=None) -> list[str]:
    """Tools that produced verified artifacts matching this request."""
    names: list[str] = []
    for message in messages or ():
        if not isinstance(message, ToolMessage):
            continue
        artifact = getattr(message, "artifact", None) or {}
        if not isinstance(artifact, dict):
            continue
        name = str(getattr(message, "name", "") or "")
        for validation in artifact.get("_artifact_validations") or ():
            if not isinstance(validation, dict) or not validation.get("valid"):
                continue
            record = dict(validation)
            record.setdefault("producer_tool", name)
            matches = (requirements is None
                       or any(_artifact_matches_requirement(record, requirement)
                              for requirement in requirements))
            if matches and name and name not in names:
                names.append(name)
                break
    return names


def _export_gate_record(deliverable_needed: bool, messages, trace_start: int,
                        requirements=None) -> dict[str, Any]:
    """Machine-readable export attribution based on verified artifact types."""
    current = messages[trace_start:]
    names = (_self_export_names(current, requirements)
             if deliverable_needed else [])
    satisfied = _has_export_artifact(current, requirements) if deliverable_needed else False
    return {
        "requested": bool(deliverable_needed),
        "requirements": list(requirements or []),
        "status": "satisfied" if satisfied else ("pending" if deliverable_needed else "not_requested"),
        "initiator": "model" if names else "none",
        "self_export_tools": names,
        "forced_tool": "",
        "forced_link": "",
        "forced_attempts": [],
    }


async def _force_deliverable_export(
    query: str, answer: str, tool_by_name: dict,
    requirements: list[dict[str, list[str]]],
    existing_validations: list[dict[str, Any]] | None = None,
    messages=None, timeout: float | None = None,
) -> list[dict[str, Any]]:
    """Attempt each outstanding contract through its registered exporter."""
    validations = list(existing_validations or ())
    produced: list[dict[str, Any]] = []
    started_at = time.monotonic()
    for index, requirement in enumerate(requirements):
        if index in _deliverable_match_map(validations, requirements):
            continue
        report_kinds = {"report_html", "report_docx"}
        if (report_kinds.intersection(requirement.get("kinds") or ())
                and sum(bool(report_kinds.intersection(r.get("kinds") or ())) for r in requirements) > 1):
            # A single final answer does not establish separate internal/public
            # report bodies. Never manufacture a redacted brief by copying it.
            name, args, plan_reason = "", {}, "missing_product_content"
        else:
            name, args, plan_reason = _forced_export_plan(
                requirement, query, answer, messages, tool_by_name)
        if not name:
            produced.append({
                "name": "", "status": "not_run", "reason": plan_reason,
                "reason_code": plan_reason, "artifact_count": 0,
                "artifact_validations": [], "forced": True,
                "initiator": "delivery_gate", "requirement": requirement,
            })
            continue
        tool = tool_by_name.get(name)
        if tool is None:
            produced.append({
                "name": name, "status": "not_run", "reason": "tool_unavailable",
                "reason_code": "tool_unavailable", "artifact_count": 0,
                "artifact_validations": [], "forced": True,
                "initiator": "delivery_gate", "requirement": requirement,
            })
            continue
        call = {
            "id": f"forced-{name}-{index}-{uuid.uuid4().hex[:12]}",
            "name": name, "args": args, "_attempt": 1,
        }
        logical_key = _logical_key(name, call)
        try:
            emit_tool_call_start(name, args, call["id"], logical_key=logical_key, attempt=1)
            emit_tool_executing(f"{name}：交付门补充执行", name, call["id"],
                                logical_key=logical_key, attempt=1)
        except Exception:
            logger.debug("Unable to publish forced-tool start for %s", name, exc_info=True)
        terminal: dict[str, Any] = {}

        def publish(call_record, message):
            terminal["outcome"] = _emit_completed_tool_step(call_record, message)

        try:
            remaining_timeout = (
                None if timeout is None else
                max(0.0, timeout - (time.monotonic() - started_at)))
            results = await _run_regular_calls(
                [call], tool_by_name, completion_callback=publish,
                timeout=remaining_timeout)
            message = results[0]
            outcome = terminal.get("outcome") or {}
            checked = _verified_artifact_validations([message])
            validations.extend(checked)
            matching = [validation for validation in checked
                        if _artifact_matches_requirement(validation, requirement)]
            produced.append({
                "name": name,
                "link": str(matching[0].get("link") or "") if matching else "",
                "status": str(outcome.get("status") or "failed"),
                "reason": str(outcome.get("reason_code") or ""),
                "reason_code": str(outcome.get("reason_code") or ""),
                "artifact_count": len(checked),
                "artifact_validations": checked,
                "forced": True,
                "initiator": "delivery_gate",
                "requirement": requirement,
            })
        except Exception as exc:
            produced.append({
                "name": name, "status": "failed", "reason": type(exc).__name__,
                "reason_code": "forced_export_error", "artifact_count": 0,
                "artifact_validations": [], "forced": True,
                "initiator": "delivery_gate", "requirement": requirement,
            })
    return produced


async def _run_regular_calls(calls: list[dict], tool_by_name: dict,
                             completion_callback=None,
                             timeout: float | None = None) -> list[ToolMessage]:
    """Run tools concurrently, retaining completed results across batch timeout."""
    from src.tools.registry import _run_single_tool

    semaphore = asyncio.Semaphore(_MAX_PARALLEL_AGENT_TOOLS)

    def publish(call, message):
        if not completion_callback:
            return
        try:
            completion_callback(call, message)
        except Exception:
            logger.warning("Tool completion callback failed for %s",
                           call.get("name", ""), exc_info=True)

    async def run_one(index, call):
        tool = tool_by_name.get(call.get("name", ""))
        if tool is None:
            message = ToolMessage(
                content="[TOOL_UNAVAILABLE] " + notice("step_unavailable"),
                tool_call_id=call.get("id", ""),
                name=call.get("name", ""), artifact={})
        else:
            try:
                async with semaphore:
                    message = await _run_single_tool(tool, call)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # retain a correlated terminal event per call
                message = ToolMessage(
                    content=f"[TOOL_ERROR] {type(exc).__name__}",
                    tool_call_id=call.get("id", ""),
                    name=call.get("name", ""), artifact={})
        # Publish one terminal outcome only after an actual result exists.
        publish(call, message)
        try:
            if isinstance(message, ToolMessage):
                from src.core.tool_results import clamp_for_model
                _clamped, _cut = clamp_for_model(
                    str(message.content or ""), str(call.get("name") or ""))
                if _cut:
                    message = message.model_copy(update={"content": _clamped})
        except Exception:
            logger.debug("clamp tool result for model failed", exc_info=True)
        return index, message

    tasks = {asyncio.create_task(run_one(index, call)): index
             for index, call in enumerate(calls)}
    results: list[ToolMessage | None] = [None] * len(tasks)
    try:
        done, pending = await asyncio.wait(
            tasks, timeout=None if timeout is None else max(0.0, float(timeout)))
        for task in done:
            try:
                index, message = task.result()
            except asyncio.CancelledError:
                index = tasks[task]
                call = calls[index]
                message = ToolMessage(
                    content=("[ERR_REQUEST_DEADLINE] The request is nearing its time limit. "
                             "Use evidence already received and answer now."),
                    tool_call_id=call.get("id", ""),
                    name=call.get("name", ""), artifact={})
                publish(call, message)
            except Exception as exc:  # defensive: run_one normally normalizes errors
                index = tasks[task]
                call = calls[index]
                message = ToolMessage(
                    content=f"[TOOL_ERROR] {type(exc).__name__}",
                    tool_call_id=call.get("id", ""),
                    name=call.get("name", ""), artifact={})
                publish(call, message)
            results[index] = message

        if pending:
            for task in pending:
                task.cancel()
            settled = await asyncio.gather(*pending, return_exceptions=True)
            for task, outcome in zip(pending, settled):
                if not isinstance(outcome, BaseException):
                    index, message = outcome
                    results[index] = message
                    continue
                index = tasks[task]
                call = calls[index]
                message = ToolMessage(
                    content=("[ERR_REQUEST_DEADLINE] The request is nearing its time limit. "
                             "Use evidence already received and answer now."),
                    tool_call_id=call.get("id", ""),
                    name=call.get("name", ""), artifact={})
                results[index] = message
                publish(call, message)
    except asyncio.CancelledError:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return [message for message in results if message is not None]


_REFLECT_SYSTEM = (
    "你是科研 Agent 的反思节点。只做一件事：判断当前任务是否已经完成。"
    "必须严格输出 JSON，不要输出任何解释。字段：done(bool)、missing(字符串数组，"
    "尚未满足的用户要求)、next_steps(字符串数组，最多3条可执行的下一步)、"
    "evidence_ok(bool，主要论断是否都有来源支持)。"
    "判定规则：用户明确要求检索/核验/审查/生成文件而未调用对应工具，done=false；"
    "关键结论没有任何工具返回的来源，evidence_ok=false。"
)


_REFLECT_TIMEOUT_SEC = float(os.getenv("CITRUS_REFLECT_TIMEOUT_SEC", "45") or 45)
_REFLECT_FAILURES = {"n": 0}


async def _reflect_on_progress(llm, *, query: str, called: list[str],
                               pending: list[list[str]], deliverable: bool,
                               has_export: bool, results: list) -> dict:
    """One cheap LLM call that decides whether the turn may stop."""
    if _REFLECT_FAILURES["n"] >= 2:
        return {}
    notes: list[str] = []
    for item in results[-6:]:
        text = str(getattr(item, "content", "") or "")[:280].replace("\n", " ")
        notes.append(text)
    payload = {
        "question": str(query)[:1200],
        "tools_called": called[-12:],
        "required_tool_groups_not_called": pending,
        "deliverable_requested": bool(deliverable),
        "deliverable_produced": bool(has_export),
        "recent_observations": notes,
    }
    try:
        resp = await asyncio.wait_for(
            llm.ainvoke([SystemMessage(content=_REFLECT_SYSTEM),
                         HumanMessage(content=json.dumps(payload, ensure_ascii=False))]),
            timeout=_REFLECT_TIMEOUT_SEC)
        _REFLECT_FAILURES["n"] = 0
    except Exception as exc:  # noqa: BLE001
        _REFLECT_FAILURES["n"] += 1
        logger.warning("[reflect] skipped (failure %d): %s", _REFLECT_FAILURES["n"], exc)
        return {}
    raw = str(getattr(resp, "content", "") or "")
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return {}
    try:
        verdict = json.loads(match.group(0))
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(verdict, dict):
        return {}
    verdict.setdefault("done", False)
    verdict.setdefault("missing", [])
    verdict.setdefault("next_steps", [])
    return verdict


async def _execute_calls(response, *, tools_by_name: dict,
                         deadline: float,
                         seen_call_signatures: dict[str, dict[str, Any]],
                         tool_status_sink: list[dict] | None = None) -> list[ToolMessage]:
    calls = [_tool_call_dict(tc) for tc in (getattr(response, "tool_calls", None) or [])]
    fresh_calls = []
    queued_signatures: set[str] = set()
    attempts: dict[str, int] = {}
    completed: dict[str, ToolMessage] = {}
    for index, call in enumerate(calls):
        if index >= _MAX_TOOL_CALLS_PER_BATCH:
            completed[call["id"]] = ToolMessage(
                content=(f"[TOOL_BATCH_LIMIT] 本轮最多执行 {_MAX_TOOL_CALLS_PER_BATCH} 项工具；"
                         "此项未执行，请先综合已有结果。"),
                tool_call_id=call["id"], name=call["name"], artifact={})
            # Do NOT publish a start event for a call that will not execute:
            # the single terminal event below already reports status=skipped.
            continue
        signature = hashlib.sha256(json.dumps(
            [call["name"], call.get("args") or {}], ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        previous = seen_call_signatures.get(signature)
        previous_attempts = (int(previous.get("attempts", 1))
                             if isinstance(previous, dict) else 0)
        retry_allowed = bool(
            isinstance(previous, dict)
            and previous.get("retryable")
            and previous_attempts < 2
        )
        if signature in queued_signatures or (previous is not None and not retry_allowed):
            completed[call["id"]] = ToolMessage(
                content="[TOOL_DUPLICATE_SKIPPED] This identical tool request already has a usable or non-retryable outcome; use it or choose a new target.",
                tool_call_id=call["id"], name=call["name"], artifact={})
        else:
            queued_signatures.add(signature)
            call["_signature"] = signature
            call["_signature_attempt"] = previous_attempts + 1
            fresh_calls.append(call)
        if call["id"] in completed:
            # Terminal without execution (duplicate / batch limit): the result
            # event carries status=skipped, so no running row is created.
            continue
        try:
            key = _logical_key(call["name"], call)
            attempts[key] = max(
                attempts.get(key, 0) + 1,
                int(call.get("_signature_attempt") or 1))
            call["_attempt"] = attempts[key]
            emit_tool_call_start(call["name"], call["args"], call["id"],
                                 logical_key=key, attempt=call["_attempt"])
            emit_tool_executing(f"{call['name']}：调用中", call["name"], call["id"],
                                logical_key=key, attempt=call["_attempt"])
        except Exception:
            pass

    for call in calls:
        if call["id"] in completed:
            _emit_completed_tool_step(call, completed[call["id"]], tool_status_sink)
    if not fresh_calls:
        return [completed[c["id"]] for c in calls]
    remaining = max(0.0, deadline - time.monotonic() - _final_reserve_sec())
    terminal_steps: dict[str, dict[str, Any]] = {}

    def _publish_tool_completion(call: dict, message: ToolMessage) -> None:
        outcome = _emit_completed_tool_step(call, message, tool_status_sink)
        if isinstance(outcome, dict):
            terminal_steps[str(call.get("id") or "")] = outcome

    results = await _run_regular_calls(
        fresh_calls, tools_by_name,
        completion_callback=_publish_tool_completion,
        timeout=remaining)

    from src.tools.outcome import safe_tool_step
    for call, message in zip(fresh_calls, results):
        signature = str(call.get("_signature") or "")
        if not signature:
            continue
        step = terminal_steps.get(str(call.get("id") or "")) or safe_tool_step(
            str(call.get("name") or ""), str(getattr(message, "content", "") or ""),
            getattr(message, "artifact", None))
        seen_call_signatures[signature] = {
            "status": step["status"],
            "retryable": bool(step.get("retryable")),
            "attempts": int(call.get("_signature_attempt") or 1),
        }
    completed.update({str(message.tool_call_id): message for message in results})
    results = [completed[call["id"]] for call in calls]

    return results

_LLM_STREAM_ATTEMPTS = 3


def _transient_stream_errors() -> tuple[type[BaseException], ...]:
    """Transport failures that are worth one retry on a streaming call."""
    errors: list[type[BaseException]] = []
    try:
        import httpx
        errors.extend([httpx.ReadTimeout, httpx.RemoteProtocolError,
                       httpx.ConnectError, httpx.ReadError])
    except Exception:
        pass
    try:
        from openai import APIConnectionError
        errors.append(APIConnectionError)
    except Exception:
        pass
    return tuple(errors)


_TRANSIENT_STREAM_ERRORS = _transient_stream_errors()


async def _call_llm(llm, messages, *, timeout: float, on_text=None, llm_fallback=None):
    # Buffer assistant text until the response is classified. Providers can
    # leak DSML control blocks through ``content``; streaming those chunks
    # before checking ``tool_calls`` would expose executable protocol as an
    # answer even if the loop later recovers it.
    #
    # D-A1: this is the single funnel every model call goes through, so it is
    # also where the *actually sent* payload is recorded. Observation only —
    # the payload is passed through untouched and any failure here is swallowed
    # (a broken recorder must never break a turn).
    from src.core import turn_context
    from src.core.progress_bus import get_turn_scope
    session_id, request_id = get_turn_scope()
    snapshot = turn_context.capture(messages) if session_id else {}
    try:
        result = None
        last_exc: Exception | None = None
        for attempt in range(1, _LLM_STREAM_ATTEMPTS + 1):
            # 末次尝试改用关闭思维链的客户端：网关在长时间无字节（思维链/长生成）
            # 时会断开 SSE，关闭思维链后 token 连续输出，能绕过该空闲窗口。
            active = llm
            if attempt >= _LLM_STREAM_ATTEMPTS and llm_fallback is not None:
                active = llm_fallback
                logger.warning("[diag] llm stream retry with thinking disabled")
            try:
                result = await asyncio.wait_for(
                    stream_llm_response(active, messages, on_text=on_text),
                    timeout=max(0.1, timeout))
                break
            except _TRANSIENT_STREAM_ERRORS as exc:
                last_exc = exc
                if attempt >= _LLM_STREAM_ATTEMPTS:
                    raise
                logger.warning(
                    "[diag] llm stream interrupted (%s); retrying once",
                    type(exc).__name__)
                await asyncio.sleep(1.0)
        if result is None:
            raise last_exc or RuntimeError("llm stream produced no response")
    except _TRANSIENT_STREAM_ERRORS as stream_exc:
        # 网关在长时间静默（思维链或长生成）时会主动断开 SSE，此时流式重试
        # 仍然停在同一个空闲窗口；改为整请求非流式取回，服务端一次性返回，
        # 不依赖连接是否持续有字节流动。
        logger.warning(
            "[diag] stream exhausted (%s); falling back to non-stream",
            type(stream_exc).__name__)
        result = None
        # 非流式取回同样可能被上游慢响应拖死（长清单回合常见）。这里给两次机会，
        # 第二次换成关闭思维链的客户端并把等待上限抬到 300 s 起：一次性返回不依赖
        # 连接空闲窗口，失败只可能是生成本身太慢。
        candidates = [llm] if llm_fallback is None else [llm_fallback, llm]
        last_nonstream: Exception | None = None
        for attempt, candidate in enumerate(candidates, 1):
            try:
                result = await asyncio.wait_for(
                    candidate.ainvoke(messages),
                    timeout=max(300.0, float(timeout)) * attempt)
                break
            except Exception as exc:  # noqa: BLE001
                last_nonstream = exc
                logger.warning(
                    "[diag] non-stream attempt %d/%d failed (%s)",
                    attempt, len(candidates), type(exc).__name__)
                await asyncio.sleep(1.5)
        if result is None:
            logger.warning(
                "[diag] non-stream fallback exhausted (%s)",
                type(last_nonstream).__name__ if last_nonstream else "unknown")
            raise stream_exc
    except Exception as exc:
        if session_id:
            try:
                await asyncio.to_thread(
                    turn_context.record, snapshot, session_id=session_id,
                    request_id=request_id, error=type(exc).__name__)
            except Exception:
                pass
        raise
    if session_id:
        try:
            from src.core.cache_metrics import extract_usage
            await asyncio.to_thread(
                turn_context.record, snapshot, session_id=session_id,
                request_id=request_id, usage=extract_usage(result) or {})
        except Exception:
            pass
    return result


def _provider_failure_notice(exc: Exception) -> str:
    """Expose actionable provider failures without labeling ordinary timeouts as quota errors."""
    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    pieces = [str(exc)]
    for attr in ("body", "message", "code", "type"):
        value = getattr(exc, attr, None)
        if value:
            pieces.append(str(value))
    response = getattr(exc, "response", None)
    if response is not None:
        status = status or getattr(response, "status_code", None)
        try:
            pieces.append(str(response.text))
        except Exception:
            pass
    detail = " ".join(pieces).lower()
    quota_markers = ("insufficient_quota", "quota_exceeded", "quota exceeded", "insufficient balance",
                     "balance is insufficient", "billing_hard_limit", "out of credits", "no credits",
                     "usagelimiterror", "usage limit exceeded", "monthly limit", "余额不足", "额度不足",
                     "欠费", "充值")
    if status == 402 or any(marker in detail for marker in quota_markers):
        return notice("model_quota")
    if status == 429:
        return notice("model_rate_limit")
    if status in (500, 502, 503, 504):
        return notice("model_unavailable", status=status)
    if status in (401, 403) or any(token in detail for token in ("invalid api key", "unauthorized", "authentication")):
        return notice("model_auth")
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timeout" in detail:
        return notice("model_timeout")
    if any(token in detail for token in ("connectionerror", "connecterror", "connection refused", "name or service not known")):
        return notice("model_connection")
    return notice("model_unknown")


def _tool_failure_notice(messages: list) -> str:
    """Preserve a failed retrieval/tool status when the run expires before synthesis."""
    failed = []
    for message in messages:
        content = str(getattr(message, "content", "") or "")
        if content.startswith(("[WEB_TOOL_ERROR]", "[WEB_FETCH_FAILED]", "[LOCAL_SEARCH_ERROR]",
                               "[LOCAL_SEARCH_TRANSLATION_ERROR]", "[ERR_TIMEOUT]", "[ERR_NETWORK]")):
            failed.append(content)
    if not failed:
        return ""
    detail = " ".join(failed).lower()
    quota_markers = ("insufficient_quota", "quota_exceeded", "quota exceeded", "insufficient balance",
                     "out of credits", "no credits", "usage limit exceeded", "余额不足", "额度不足", "欠费")
    if any(marker in detail for marker in quota_markers) or "http 402" in detail:
        return notice("tool_quota")
    if any(marker in detail for marker in ("not configured", "not been configured", "未配置")):
        return notice("tool_not_configured")
    if "http 429" in detail or "rate limit" in detail:
        return notice("tool_rate_limit")
    if "http 401" in detail or "http 403" in detail or "invalid api key" in detail or "unauthorized" in detail:
        return notice("tool_auth")
    if any(code in detail for code in ("http 500", "http 502", "http 503", "http 504")):
        return notice("tool_unavailable")
    if any(token in detail for token in ("timeout", "timed out", "err_timeout", "request_deadline")):
        return notice("tool_timeout")
    if any(token in detail for token in ("local_search", "bm25", "dense", "rerank")):
        return notice("local_search_failed")
        return notice("web_tool_failed")


async def general_agent_node(state: AgentState) -> dict:
    query = str(state.get("query") or "")
    session_id = str(state.get("session_id") or "")
    if _request_timeout_disabled():
        deadline = math.inf
    else:
        deadline = float(state.get("request_deadline") or
                         (time.monotonic() + max(1, settings.AGENT_REQUEST_TIMEOUT_SEC)))
    started = time.monotonic()
    scope = _source_scope(query)
    explicit_reference_check = _explicit_reference_check(query)
    has_paper_attachment = bool(state.get("has_paper_attachment"))
    paper_only = _read_uploaded_paper_without_search(
        query, scope, has_paper_attachment)
    tool_policy = resolve_tool_policy(query, scope, paper_only=paper_only)
    recent_turns: list[int] = []
    try:
        from src.session.manager import session_manager as _session_manager
        recent_turns = _session_manager.recent_evidence_turns(session_id, limit=2)
    except Exception:
        logger.debug("recent evidence turns unavailable", exc_info=True)
    retrieval_decision = decide_retrieval(
        query, scope,
        has_history_evidence=bool(state.get("history_evidence_block")),
        recent_turn_seqs=recent_turns,
        local_corpus_match=(_local_corpus_matches(query) if not paper_only else False),
    )
    retrieval_disabled = not tool_policy.allow_retrieval
    if retrieval_decision.mode == "reuse":
        try:
            from src.core import turn_source_ledger
            from src.core.progress_bus import get_turn_scope
            _scope_sid, _scope_rid = get_turn_scope()
            turn_source_ledger.record_history_sources(
                _scope_sid or session_id, _scope_rid,
                list(retrieval_decision.history_turn_seqs))
        except Exception:
            logger.debug("history source registration failed", exc_info=True)
    figures.begin_request()
    try:
        from src.research.context import get_request_tables
        request_tables = get_request_tables()
        has_table_attachments = bool(request_tables)
    except Exception:
        request_tables = []
        has_table_attachments = False
    image_attachments = state.get("image_attachments") or []
    attachment_context = str(state.get("attachment_context") or "").strip()
    visualization_enabled = bool(state.get("visualization_enabled"))
    # Keep autonomous routing advice separate from user-mandated work. In the
    # default advisory mode keyword-based suggestions never become a completion
    # gate; only explicit source/action requests are required. ``enforce`` is a
    # temporary compatibility switch for the former keyword-driven behavior.
    from src.graph.retrieval_router import (
        parse_request_constraints,
        required_route_groups,
        retrieval_tools_allowed,
    )
    request_constraints = parse_request_constraints(
        query, scope, has_table_attachment=has_table_attachments)
    required_tool_groups: list[set[str]] = [
        set(group) for group in required_route_groups(retrieval_decision)
    ]
    if scope != "web" and _explicit_local_tool_request(query):
        # A direct English instruction to call local_hybrid_search is an
        # executable requirement, even when the query omits the word "citrus".
        local_requirement = {"local_hybrid_search"}
        if local_requirement not in required_tool_groups:
            required_tool_groups.append(local_requirement)
    if explicit_reference_check and scope != "local":
        required_tool_groups.append({"verify_uploaded_references"})
    if request_constraints.data_audit_requested and has_table_attachments:
        required_tool_groups.append({"audit_uploaded_data"})
    # Every requested deliverable is a separate requirement, mapped through the
    # registry's declared output kinds so a figure cannot satisfy a report.
    deliverable_requirements = _deliverable_contract(query)
    for export_group in _deliverable_tool_groups(deliverable_requirements):
        if export_group not in required_tool_groups:
            required_tool_groups.append(export_group)
    tools, _ = _scoped_toolset(
        scope, query=query, has_table=has_table_attachments,
        has_paper_attachment=has_paper_attachment,
        has_account_files=bool(state.get("account_files")),
        visualization_enabled=visualization_enabled)
    if not retrieval_tools_allowed(request_constraints, retrieval_decision):
        tools = [tool for tool in tools if tool.name not in RETRIEVAL_TOOLS]
    available_names = {tool.name for tool in tools}
    unavailable_required_groups = [
        group for group in required_tool_groups if not (group & available_names)
    ]
    required_tool_groups = [
        group for group in required_tool_groups if group & available_names
    ]
    turn_budget = _tool_turn_budget(query)
    deliverable_needed = bool(deliverable_requirements)
    export_nudge_sent = False
    disease_image_query = bool(image_attachments and re.search(
        r"病害|病症|什么病|识别.{0,6}病|诊断|症状|黄龙病|溃疡病|黑斑病|病斑|disease|diagnos|symptom",
        query, re.I))
    main_model_can_see_images = bool(image_attachments) and _main_model_supports_image_input()
    tools = sorted(tools, key=lambda tool: str(getattr(tool, "name", "")))
    tools_by_name = {tool.name: tool for tool in tools}
    system_prompt = assemble_system_prompt(mode="expert", format_hint=state.get("format_hint"),
                                           query=query, available_tools=set(tools_by_name))
    # Keep the core system prefix stable for provider prompt caching. The active
    # corpus catalog is request-scoped and appended only after that prefix.
    # Only advertise the local corpus when the tool is actually bound for this
    # request: an advertised-but-unavailable tool makes the model call it and
    # yields a spurious "[TOOL_UNAVAILABLE]" step in the visible trace.
    if "local_hybrid_search" in tools_by_name:
        try:
            from src.retrieval.corpus_registry import load_corpus_registry
            registry = load_corpus_registry()
            system_prompt += (
                "\n\n<registered_local_corpus_families>\n"
                "以下类别由运行时语料清单动态提供。调用 local_hybrid_search 时，"
                "corpus_types 只能使用这些已登记 family id；按问题选择匹配类别，不得猜测或改写 ID。\n"
                + registry.describe_families() + "\n</registered_local_corpus_families>"
            )
        except Exception as exc:
            logger.warning("Could not append dynamic corpus registry to prompt: %s", exc)

    from src.core.context_manager import (
        assemble_supervisor_messages, build_human_message, build_loaded_context,
    )
    loaded = build_loaded_context(state, mode="expert", format_hint=state.get("format_hint"))
    human = build_human_message(loaded, retrieval_context="")
    completion_budget, answer_guidance = _answer_profile(query)
    if disease_image_query:
        completion_budget = min(completion_budget, 512)
        answer_guidance += (
            "\n病害图片问题请用极简用户格式回答：最多 3 行，依次写“最可能判断 + 置信度（高/中/低）”、"
            "“鉴别（仅 1 个关键点）”、以及“建议（仅在把握中低时给 1 条优先筛查）”。"
            "不要写长篇机制、多种诱因、候选排名、重复总结或多个检查清单。"
            "把握不足时明确说明不确定；不得声称确诊或给出农药用量。"
        )
    if request_constraints.reason_code == "data_audit_only":
        decision_guidance = (
            "本轮是数据审查任务。只使用附件读取、表结构检查和数据审查工具，"
            "不调用联网或本地语料检索；除非用户另行明确要求证据检索。"
        )
    elif retrieval_decision.policy == "deny":
        decision_guidance = "用户已明确禁止本轮检索。只依据附件、当前上下文或稳定知识回答；不得调用检索工具，也不得声称刚刚检索了新资料。"
    elif retrieval_decision.policy == "required":
        if retrieval_decision.mode == "local":
            decision_guidance = "用户明确要求检索。请实际调用本地语料检索；不得调用联网检索。"
        elif retrieval_decision.mode == "web":
            decision_guidance = "用户明确要求检索。请实际调用联网检索；不得调用本地语料检索。"
        elif retrieval_decision.mode == "fusion":
            decision_guidance = "用户明确要求检索。请同时尝试联网检索和本地语料检索，并分别说明证据边界。"
        else:
            decision_guidance = "用户明确要求检索。请至少实际调用一次合规检索工具。"
    elif retrieval_decision.mode == "reuse":
        decision_guidance = "最近两轮已有匹配证据。优先复用并核对证据缺口；如确实缺少关键证据，可以再补一次精准检索。"
    else:
        decision_guidance = "按本轮问题选择必要工具。已有匹配上下文时优先复用，不因普通科研关键词自动双查。"
    request_policy = (
        "\n\n<request_policy>\n" + decision_guidance + "\n"
        + "系统提示中的工具说明是本轮任务路由的唯一文字规则；代码会强制执行用户指定的来源范围。\n"
        + ("若账号资料库索引中有与当前对话相关的旧文件，先由你依据完整语境判断是否需要读取；用户只是问已上传文件时，不要因此调用联网检索。\n"
           if state.get("account_files") else "")
        + "对用户只呈现简洁、自然的进度和结论；不要输出内部工具名、路由决策、调用参数、错误代码或系统实现细节。"
        "步骤未完成时，只说明与用户任务有关的结果限制，并继续利用已有材料回答。\n"
        + ("可使用 draw_svg 生成图示；用户明确要求绘图时按要求执行，并在验证后原样返回 SVG。\n"
           if visualization_enabled or _explicit_visualization_request(query) else "")
        + "</request_policy>"
    )
    human.content = (str(human.content or "")
                     + "\n\n<answer_length_and_scope>\n" + answer_guidance
                     + "\n</answer_length_and_scope>" + request_policy)
    if attachment_context:
        human.content += "\n\n" + attachment_context
    image_attachments = state.get("image_attachments") or []
    if image_attachments:
        image_scope = f"用户本轮上传了 {len(image_attachments)} 张图片；图片仅用于本轮，不写入知识库。"
        if disease_image_query:
            if main_model_can_see_images:
                vision_rules = (
                    "\n\n" + image_scope
                    + "你已收到原图，请由主模型直接判断最可能的病害或生理问题。"
                    + "回答最多 3 行：最可能判断及把握（高/中/低，非校准概率）；"
                    + "1 个关键鉴别点；仅在把握中低时给 1 条优先筛查建议。"
                    + "照片不足以判断时明确说不确定，并指出最有价值的一项补充观察或检测；"
                    + "不得声称确诊或给出农药用量。"
                )
            else:
                vision_rules = (
                    "\n\n" + image_scope
                    + "当前所选主模型不支持图片输入；不要声称看到了原图或给出图像诊断。"
                    + "请说明当前无法读取图片，并请用户补充症状描述或切换支持图像输入的主模型。"
                )
        elif main_model_can_see_images:
            vision_rules = "\n\n" + image_scope + "请按用户问题检查原图；不得仅凭照片断言品种。"
        else:
            vision_rules = (
                "\n\n" + image_scope
                + "当前所选主模型不支持图片输入，不要声称看到了原图；请依据文字和已有工具结果回答，"
                + "并请用户补充必要的图片描述。"
            )
        human.content += vision_rules
        text_block = {"type": "text", "text": str(human.content)}
        human.content = [text_block]
        if main_model_can_see_images:
            human.content.extend([
                {"type": "image_url", "image_url": {"url": image["url"]}}
                for image in image_attachments if image.get("url")
            ])
    messages, trace_start = assemble_supervisor_messages(system_prompt, state, human)
    messages = _sanitize_history_tool_protocol(messages)
    reset_emitted()  # S2: per-request dedupe of public working notes
    # First display a text estimate for the fully assembled messages. Provider
    # input_tokens later replaces it with measured usage for each model call.
    try:
        from src.core.context_budget import ContextBudget, ContextBudgetConfig
        from src.core.progress_bus import emit_encoded
        budget = ContextBudget(ContextBudgetConfig(
            max_tokens=settings.RESOLVED_CONTEXT_BUDGET_MAX_TOKENS,
            soft_threshold=settings.CONTEXT_BUDGET_SOFT_THRESHOLD,
            hard_threshold=settings.CONTEXT_BUDGET_HARD_THRESHOLD,
        ))
        history_messages = state.get("messages") or []
        estimate = budget.estimate_tokens(messages)
        emit_encoded("context_status", {
            "history_msgs": len(history_messages),
            "history_chars": sum(len(str(getattr(m, "content", "") or "")) for m in history_messages),
            "estimate_scope": "assembled_message_text",
            "estimate_label": "本轮消息文本估算（不含工具定义和图片 token）",
            "ltm_recalled": bool(state.get("long_term_memory")),
            "ltm_chars": len(str(state.get("long_term_memory") or "")),
            "resident_cards": bool(state.get("resident_cards")),
            "user_preferences": bool(state.get("user_preferences")),
            "memory_status": "recalled" if any(state.get(k) for k in
                ("long_term_memory", "resident_cards", "user_preferences")) else "empty",
            "estimated_tokens": estimate,
            "max_tokens": budget.config.max_tokens,
            "soft_threshold": budget.config.soft_threshold,
            "hard_threshold": budget.config.hard_threshold,
            "compressed": bool(state.get("compacted")),
            "compression_len": 0,
        })
    except Exception:
        pass

    from src.core.llm_pool import get_llm
    llm_base = get_llm(
        model=get_deepseek_model(), api_key=settings.RESOLVED_MAIN_API_KEY,
        base_url=settings.RESOLVED_MAIN_BASE_URL,
        temperature=settings.TEMPERATURE_MAIN, max_tokens=completion_budget,
        timeout=min(300, max(5, settings.AGENT_REQUEST_TIMEOUT_SEC)))
    llm_tools = llm_base.bind_tools(tools) if tools else llm_base
    try:
        llm_base_nothink = get_llm(
            model=get_deepseek_model(), api_key=settings.RESOLVED_MAIN_API_KEY,
            base_url=settings.RESOLVED_MAIN_BASE_URL,
            temperature=settings.TEMPERATURE_MAIN, max_tokens=completion_budget,
            timeout=min(300, max(5, settings.AGENT_REQUEST_TIMEOUT_SEC)),
            thinking_off=True)
        llm_tools_nothink = (llm_base_nothink.bind_tools(tools)
                             if tools else llm_base_nothink)
    except Exception:
        llm_base_nothink = llm_base
        llm_tools_nothink = llm_tools
    tool_names_called: list[str] = []
    # Real execution status per tool call, collected in _execute_tool_calls.
    # Used to distinguish "the tool ran and produced evidence" from "the tool
    # was invoked but failed / returned nothing".
    tool_status_records: list[dict] = []
    seen_evidence: set[str] = set()
    stagnant_batches = 0
    tool_turns = 0
    main_results: list[dict] = []
    web_results: list[dict] = []
    answer = ""
    stop_reason = "model_finished"
    failure_notice = ""
    source_enforcement_attempts = 0
    protocol_recovery_attempts = 0
    budget_retry_attempts = 0
    extensions_used = 0
    reflection_attempts = 0
    seen_call_signatures: dict[str, dict[str, Any]] = {}

    try:
        emit_status("step_done", step_id="load")
        emit_status("step_active", step_id="retrieve")
    except Exception:
        pass

    while True:
        # S1: 协作式取消的第一道关口——已请求停止就不再发起新的模型/工具轮次。
        from src.core.cancel import is_cancelled as _cancel_requested
        if _cancel_requested():
            stop_reason = "cancelled"
            logger.info("General Agent stopped by user request before next round")
            break
        if tool_turns >= turn_budget:
            _unfinished = [g for g in required_tool_groups
                           if not g.intersection(tool_names_called)]
            _needs_export = deliverable_needed and not _has_export_artifact(messages[trace_start:], deliverable_requirements)
            if (_unfinished or _needs_export) and extensions_used < _MAX_BUDGET_EXTENSIONS:
                extensions_used += 1
                turn_budget = min(turn_budget + _BUDGET_EXTENSION, 40)
                logger.info("[budget] extended to %d turns (missing=%d, needs_export=%s)",
                            turn_budget, len(_unfinished), _needs_export)
                emit_status("agent_progress",
                            message=f"继续执行：已延长 {_BUDGET_EXTENSION} 轮预算…")
                messages.append(HumanMessage(content=(
                    "预算已延长。请继续完成尚未完成的步骤："
                    + ("还需调用 " + "; ".join(" / ".join(sorted(g)) for g in _unfinished) + "。" if _unfinished else "")
                    + ("还需要生成用户要求的文件。" if _needs_export else ""))))
            else:
                stop_reason = "tool_turn_limit"
                break
        final_reserve = _final_reserve_sec()
        remaining = deadline - time.monotonic()
        if math.isfinite(deadline) and remaining <= final_reserve:
            stop_reason = "request_deadline"
            break
        try:
            emit_status("agent_progress", message="正在分析当前问题与已有证据…")
            # S2: collect this round's public text. It is classified only after
            # the round ends (tool calls vs final answer), never streamed raw.
            _round_text_chunks: list[str] = []
            # Tool rounds may stream public working notes, but the note is not
            # the answer. Buffer it here; classify the round first and emit the
            # final text only after ``answer_start``.
            _stream_state: dict = {"pending": [], "last": 0.0, "sent": 0}

            def _flush_text(_force: bool = False) -> None:
                if not _stream_state["pending"]:
                    return
                now = time.monotonic()
                if not _force and (now - _stream_state["last"]) < 0.06:
                    return
                chunk = "".join(_stream_state["pending"])
                _stream_state["pending"].clear()
                _stream_state["last"] = now
                _stream_state["sent"] += len(chunk)

            def _on_text_delta(chunk: str) -> None:
                _round_text_chunks.append(chunk)
                _stream_state["pending"].append(chunk)
                _flush_text()

            export_reserve = 4 if any("docx" in r.get("formats", []) for r in deliverable_requirements) else 2
            if (deliverable_needed and not export_nudge_sent
                    and tool_turns >= max(0, turn_budget - export_reserve)):
                export_nudge_sent = True
                messages.append(HumanMessage(content=(
                    f"还剩 {turn_budget - tool_turns} 个回合。用户要求交付文件，"
                    "请立即用已有材料和用户指定格式交付文件。Word 使用 word_document "
                    "create→分批 append_blocks→finalize；若已有草稿先 inspect 恢复，"
                    "所有章节和末块确认后再发布。表格可用 export_table，文献用 "
                    "export_literature_evidence，HTML 用现有导出器。"
                    "不要用 HTML 充当 DOCX，不要在此时继续检索。")))
                emit_status("agent_progress", message="准备交付文件，保留最后回合用于导出…")
            response = await _call_llm(
                llm_tools, messages, timeout=_llm_timeout(remaining - final_reserve),
                llm_fallback=llm_tools_nothink,
                on_text=_on_text_delta)
        except asyncio.TimeoutError:
            logger.warning("General Agent model call reached reserved finalization window")
            stop_reason = "model_timeout"
            failure_notice = _provider_failure_notice(asyncio.TimeoutError())
            break
        except Exception as exc:  # noqa: BLE001
            logger.exception("General Agent model call failed")
            stop_reason = f"model_error:{type(exc).__name__}"
            failure_notice = _provider_failure_notice(exc)
            break

        emit_usage_from_response(session_id, "general_agent", response)
        _finish = str((getattr(response, "response_metadata", None) or {}).get("finish_reason") or "")
        logger.info("[diag] llm_response finish=%s tool_calls=%s content_chars=%d budget=%d",
                    _finish or "-", len(getattr(response, "tool_calls", None) or []),
                    len(str(getattr(response, "content", "") or "")), completion_budget)
        if (_finish in ("length", "max_tokens", "max_output_tokens")
                and budget_retry_attempts < 2):
            # Provider stopped at the output cap before any text arrived. Retry
            # once with a larger budget instead of reporting a fake timeout.
            budget_retry_attempts += 1
            completion_budget = min(_COMPLETION_TOKEN_BUCKETS[-1],
                                    max(completion_budget * 2, 8192))
            llm_base = get_llm(
                model=get_deepseek_model(), api_key=settings.RESOLVED_MAIN_API_KEY,
                base_url=settings.RESOLVED_MAIN_BASE_URL,
                temperature=settings.TEMPERATURE_MAIN, max_tokens=completion_budget,
                timeout=min(120, max(5, settings.AGENT_REQUEST_TIMEOUT_SEC)))
            llm_tools = llm_base.bind_tools(tools) if tools else llm_base
            logger.warning("Model output hit the %s cap; retrying with %d tokens",
                           _finish, completion_budget)
            truncated_calls = getattr(response, "tool_calls", None) or []
            repair_note = (
                "上一轮工具参数在输出长度上限处被截断，该次调用没有执行。"
                "请只保留必要字段，把长清单分批（每批不超过 20 条）后重新调用。"
                if truncated_calls else
                "上一轮回答因输出长度上限被截断。请更精简地继续：需要绘图时调用 draw_svg "
                "并提交紧凑 SVG；否则直接给出完整但简短的答案。")
            messages.extend([
                AIMessage(content="[上一轮输出在长度上限处被截断，未形成有效内容。]"),
                HumanMessage(content=repair_note),
            ])
            emit_status("agent_progress", message="输出长度上限被截断，正在以更大预算重试…")
            continue
        calls = getattr(response, "tool_calls", None) or []
        if not calls:
            detected, recovered_calls = _parse_dsml_tool_calls(
                str(getattr(response, "content", "") or ""), tools_by_name)
            if recovered_calls:
                logger.warning("Recovered %d DSML tool call(s) emitted as assistant text",
                               len(recovered_calls))
                response = AIMessage(content="", tool_calls=recovered_calls)
                calls = recovered_calls
                emit_status("agent_progress",
                            message="已恢复模型返回的工具调用格式，继续执行检索…")
            elif detected:
                protocol_recovery_attempts += 1
                logger.error("Rejected unparseable or unavailable DSML tool-call text")
                messages.extend([
                    AIMessage(content="[上一轮返回的是未执行的工具协议，不是有效回答。]"),
                    HumanMessage(content=(
                        "上一轮输出了未执行的工具调用协议。不要复述或输出任何 DSML、XML 或工具调用标记。"
                        "请根据用户原始问题与已有证据继续：若当前工具列表含有合适工具，请通过标准结构化工具调用；"
                        "否则直接给出正常答案，并简要说明确实缺少的证据。")),
                ])
                if protocol_recovery_attempts < 2:
                    emit_status("agent_progress",
                                message="检测到未执行的工具协议，正在安全恢复回答…")
                    continue
                stop_reason = "invalid_tool_protocol"
                break

        messages.append(response)
        if not calls:
            missing_groups = [group for group in required_tool_groups
                              if not group.intersection(tool_names_called)]
            if (missing_groups and
                    source_enforcement_attempts < _MAX_SOURCE_ENFORCEMENT_ATTEMPTS and tools):
                source_enforcement_attempts += 1
                missing = [" / ".join(sorted(group)) for group in missing_groups]
                messages.append(HumanMessage(content=(
                    "用户明确要求的任务或来源尚未完成。请调用以下尚未尝试的工具组："
                    f"{'; '.join(missing)}。完成一次尝试后检查回执；若工具失败或无结果，"
                    "如实说明状态并继续回答，不要把失败写成没有相关资料。")))
                emit_status("agent_progress", message="正在补齐用户明确要求的检索或审查步骤…")
                continue
            answer = str(response.content or "")
            # No raw tool protocol is ever accepted as an answer, even if a
            # provider emits it during an unbound final response.
            if _DSML_PROTOCOL_RE.search(answer):
                protocol_recovery_attempts += 1
                logger.error("Blocked DSML tool-call text from final answer (attempt %s)",
                             protocol_recovery_attempts)
                if protocol_recovery_attempts < 2:
                    messages.extend([
                        AIMessage(content="[上一轮返回的是未执行的工具协议，不是有效回答。]"),
                        HumanMessage(content=(
                            "上一轮输出了未执行的工具调用协议。不要复述或输出任何 DSML、XML 或工具调用标记。"
                            "请直接给出正常答案；若确实需要工具，请通过标准结构化工具调用发起。")),
                    ])
                    emit_status("agent_progress", message="正在安全恢复回答…")
                    continue
                answer = ""
            else:
                # Finalization may still append required-step notices and
                # verified artifact links. Publish only after that processing.
                _flush_text(True)
                answer = _sanitize_answer_artifact_links(answer)
            break

        # S2: this round ended in tool calls, so its buffered text is only a
        # working note. Do not emit either ``text`` or ``text_reset`` for the
        # normal tool path; the progress timeline receives it below.
        emit_public_progress(public_progress_text(_round_text_chunks, bool(calls)))

        emit_status("agent_progress", message=f"已选择 {len(calls)} 项工具操作，开始执行…")

        tool_turns += 1
        results = await _execute_calls(response, tools_by_name=tools_by_name,
                                       deadline=deadline,
                                       seen_call_signatures=seen_call_signatures,
                                       tool_status_sink=tool_status_records)
        messages.extend(results)
        # S1: 停止请求在工具执行期间到达 → 不再请模型继续下一轮，直接收尾。
        from src.core.cancel import is_cancelled as _cancel_requested2
        if _cancel_requested2():
            stop_reason = "cancelled"
            logger.info("General Agent stopped by user request during tool batch")
            break
        emit_status("agent_progress", message="工具结果已并入上下文，正在判断是否还需补充…")
        new_evidence = _evidence_fingerprints(results) - seen_evidence
        seen_evidence.update(new_evidence)
        if new_evidence:
            stagnant_batches = 0
        else:
            stagnant_batches += 1
        for call, result in zip(calls, results):
            d = _tool_call_dict(call)
            if (not str(getattr(result, "content", "") or "").startswith("[TOOL_BATCH_LIMIT]")
                    and d["name"] not in tool_names_called):
                tool_names_called.append(d["name"])
            artifact = getattr(result, "artifact", None) or {}
            if isinstance(artifact, dict):
                main_results.extend(artifact.get("main_results") or [])
                web_results.extend(artifact.get("web_results") or [])

        # Reflect: explicit gate on plan completeness and evidence sufficiency.
        if reflection_attempts < _MAX_REFLECTIONS and not _arm_off("CITRUS_REFLECTION_DISABLED"):
            pending_groups = [sorted(g) for g in required_tool_groups
                              if not g.intersection(tool_names_called)]
            has_export_now = _has_export_artifact(messages[trace_start:], deliverable_requirements)
            needs_work = bool(pending_groups) or (
                deliverable_needed and not has_export_now) or not tool_status_records
            if needs_work:
                reflection_attempts += 1
                verdict = await _reflect_on_progress(
                    llm_base, query=query, called=list(tool_names_called),
                    pending=pending_groups, deliverable=deliverable_needed,
                    has_export=has_export_now, results=results)
                if verdict:
                    missing = [str(x) for x in (verdict.get("missing") or [])][:6]
                    next_steps = [str(x) for x in (verdict.get("next_steps") or [])][:3]
                    logger.info("[reflect] attempt=%d done=%s missing=%s next=%s",
                                reflection_attempts, verdict.get("done"),
                                missing, next_steps)
                    emit_status("agent_progress",
                                message="反思：已检查证据完整性与交付状态…")
                    if (verdict.get("done") and not pending_groups
                            and (not deliverable_needed or has_export_now)):
                        stop_reason = "reflection_done"
                        break
                    if next_steps or missing:
                        messages.append(HumanMessage(content=(
                            "[反思] 尚未满足的要求：" + ("；".join(missing) or "无")
                            + "。下一步：" + ("；".join(next_steps) or "继续收集必要证据"))))
                        emit_status("agent_progress",
                                    message="反思：根据缺口继续执行下一步…")

        if stagnant_batches >= _NO_PROGRESS_BATCHES_TO_STOP:
            stop_reason = "no_new_evidence"
            logger.info("General Agent stopped retrieval after %d consecutive tool batches "
                        "without new evidence", stagnant_batches)
            break

    # Reserve a final model pass without tools so the Agent can synthesize all
    # evidence already in its transcript, while keeping a strict total deadline.
    # S1: 用户已停止 —— 不再请求模型做最终综述（否则"停止"之后还会再花一次
    # 模型调用，且可能把停止后的新结论写进上下文）。
    if not answer and stop_reason != "cancelled":
        remaining = (math.inf if not math.isfinite(deadline)
                     else max(0.0, deadline - time.monotonic() - 1.0))
        if not math.isfinite(remaining) or remaining > 1:
            missing_groups = [group for group in required_tool_groups
                              if not group.intersection(tool_names_called)]
            missing_notice = ""
            if missing_groups:
                missing_names = [" / ".join(sorted(group)) for group in missing_groups]
                missing_notice = (
                    "\n明确要求的步骤仍未实际调用：" + "; ".join(missing_names) + ". "
                    "不得声称该步骤已完成，也不得把未执行说成无结果；请明确标记未完成。"
                )
            final_messages = messages + [HumanMessage(content=_FINAL_INSTRUCTION + missing_notice)]
            try:
                final_response = await _call_llm(
                    llm_base, final_messages, timeout=_llm_timeout(remaining),
                        llm_fallback=llm_base_nothink)
                emit_usage_from_response(session_id, "general_agent", final_response)
                final_text = str(final_response.content or "")
                if _DSML_PROTOCOL_RE.search(final_text):
                    logger.error("Blocked DSML tool-call text from final synthesis")
                    answer = ""
                else:
                    answer = final_text
                if answer:
                    if answer == final_text:
                        messages.append(final_response)
                    answer = _sanitize_answer_artifact_links(answer)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Final synthesis failed (%s): %s", stop_reason, exc)
                failure_notice = _provider_failure_notice(exc)
        if not answer:
            # Tool failures from prior turns remain in conversation history for
            # context, but must never mark this unrelated turn as failed.  The
            # closing notice names the actual stop reason: reporting every empty
            # answer as "超出请求时限" hid truncation and 500s behind a timeout.
            tool_notice = _tool_failure_notice(messages[trace_start:])
            if stop_reason == "cancelled":
                # S1: 停止优先于一切失败/超时话术——这是用户的选择，不是故障。
                from src.core.turn_notices import stopped_notice
                answer = stopped_notice()
            elif failure_notice:
                answer = failure_notice
            elif tool_notice:
                answer = tool_notice
            elif stop_reason == "request_deadline":
                answer = notice("deadline")
            elif stop_reason == "tool_turn_limit":
                answer = notice("tool_turn_limit", limit=turn_budget)
            elif tool_turns:
                answer = notice("no_evidence", detail="请换个更具体的问法，或指定数据来源。")
            else:
                answer = notice("output_truncated", budget=completion_budget)
    missing_groups = [group for group in required_tool_groups
                      if not group.intersection(tool_names_called)]
    missing_requirements = [
        "unavailable:" + " / ".join(sorted(group))
        for group in unavailable_required_groups
    ] + [
        "unattempted:" + " / ".join(sorted(group))
        for group in missing_groups
    ]
    # A required tool that ran but failed / produced no evidence is NOT a
    # completed research step.  Distinguish the two, so the answer never
    # claims success on the strength of a mere invocation.
    # An explicit evidence/file request is satisfied only by a complete
    # execution with a usable result. A successful-but-empty search or a
    # partial channel failure is recorded, but must not make the task complete.
    def _required_ok(record: dict[str, Any]) -> bool:
        """A required group counts as satisfied when its tool produced a result.

        ``partial`` is a real outcome for long operations (a verification run
        that resolved 29 of 30 records and delivered the workbook). Treating it
        as a failure made every such turn open with "本轮未完成", which is
        misleading when the deliverable exists. A partial run *without* an
        artifact stays a failure.
        """
        if record.get("status") == "complete":
            return True
        return (record.get("status") == "partial"
                and int(record.get("artifact_count") or 0) > 0)

    # Deliverable attribution: "self" = the model called an export tool itself,
    # "gate" = the post-answer delivery gate had to supply the file. Without this
    # record the two are indistinguishable in logs, and the reported export rate
    # silently mixes model behaviour with the safety net.
    export_gate: dict[str, Any] = _export_gate_record(
        deliverable_needed, messages, trace_start, deliverable_requirements)
    forced_records: list[dict[str, Any]] = []
    current_validations = _verified_artifact_validations(messages[trace_start:])
    if (deliverable_needed and answer and not is_incomplete(answer)
            and not _deliverables_satisfied(current_validations, deliverable_requirements)
            and not _arm_off("CITRUS_DELIVERY_GATE_DISABLED")):
        forced_records = await _force_deliverable_export(
            query, answer, tools_by_name, deliverable_requirements,
            current_validations, messages=messages[trace_start:],
            timeout=max(0.0, deadline - time.monotonic()))
        for forced_record in forced_records:
            forced_name = str(forced_record.get("name") or "")
            if forced_name and forced_name not in tool_names_called:
                tool_names_called.append(forced_name)
            tool_status_records.append(dict(forced_record))

    final_validations = _verified_artifact_validations(messages[trace_start:])
    final_validations.extend(
        validation
        for record in forced_records
        for validation in record.get("artifact_validations", ())
        if isinstance(validation, dict) and validation.get("valid")
    )
    final_satisfied = (
        _deliverables_satisfied(final_validations, deliverable_requirements)
        if deliverable_needed else False
    )
    matched_deliverable_indexes = _deliverable_match_map(
        final_validations, deliverable_requirements)
    unmet_deliverables = [
        f"{'+'.join(requirement.get('kinds') or ['artifact'])}"
        f"/{'+'.join(requirement.get('formats') or ['any-format'])}"
        for index, requirement in enumerate(deliverable_requirements)
        if index not in matched_deliverable_indexes
    ]
    matched_deliverables = len(matched_deliverable_indexes)
    if deliverable_needed:
        export_gate["status"] = (
            "satisfied" if final_satisfied else
            "partial" if matched_deliverables else "unmet")
        export_gate["missing_requirements"] = unmet_deliverables
        forced_names = list(dict.fromkeys(
            str(record.get("name") or "") for record in forced_records
            if record.get("name")))
        forced_links = list(dict.fromkeys(
            str(record.get("link") or "") for record in forced_records
            if record.get("link")))
        export_gate["forced_tools"] = forced_names
        export_gate["forced_links"] = forced_links
        export_gate["forced_attempts"] = [
            {
                "name": str(record.get("name") or ""),
                "status": str(record.get("status") or ""),
                "reason_code": str(record.get("reason_code") or ""),
                "requirement": record.get("requirement") or {},
            }
            for record in forced_records
        ]
        export_gate["forced_tool"] = ",".join(forced_names)
        export_gate["forced_link"] = forced_links[0] if forced_links else ""
        if forced_names:
            export_gate["initiator"] = (
                "mixed" if export_gate.get("self_export_tools") else "delivery_gate")
        if forced_links and answer:
            links = " ｜ ".join(f"[交付文件 {index + 1}]({link})"
                                 for index, link in enumerate(forced_links))
            answer = answer.rstrip() + "\n\n已生成通过校验的交付文件：" + links
        if final_satisfied and answer and not is_incomplete(answer):
            # The model wrote its prose before the delivery gate ran, so it may
            # claim "no export channel / export tool never ran". Those claims are
            # now false and must not reach the user next to the download link.
            pass  # reconciliation happens after the completion notices below
        if unmet_deliverables:
            missing_requirements.append(
                "deliverable_unmet:" + ";".join(unmet_deliverables))

    # The gate may have executed a required tool after the earlier check, so
    # recompute what is genuinely still missing. Otherwise a delivered file is
    # still announced as "1 step not executed".
    missing_groups = [group for group in required_tool_groups
                      if not group.intersection(tool_names_called)]

    failed_required: list[str] = []
    if required_tool_groups and tool_status_records:
        for group in required_tool_groups:
            if not group.intersection(tool_names_called):
                continue          # already covered by missing_groups
            statuses = [r["status"] for r in tool_status_records
                        if r["name"] in group]
            ok_records = [r for r in tool_status_records if r["name"] in group]
            if statuses and not any(_required_ok(r) for r in ok_records):
                names = sorted({n for n in group if n in
                                {r["name"] for r in tool_status_records}})
                detail = "; ".join(
                    f"{r['name']}={r['status']}"
                    + (f"({r['reason']})" if r.get("reason") else "")
                    for r in tool_status_records if r["name"] in group)
                failed_required.append(f"{' / '.join(names)} [{detail}]")
    if failed_required and answer and not is_incomplete(answer):
        # 用户可见的提示只说明“有几步没做成”，工具名/状态码留在内部记录里。
        answer = (notice("required_steps_failed", count=len(failed_required))
                  + "\n\n" + answer)
    missing_requirements.extend(
        "unmet:" + value for value in failed_required)
    if (missing_groups or unavailable_required_groups or unmet_deliverables) and answer and not is_incomplete(answer):
        missing_names = [
            " / ".join(sorted(group))
            for group in list(unavailable_required_groups) + list(missing_groups)
        ]
        if unmet_deliverables:
            missing_names.append("requested deliverable " + ", ".join(unmet_deliverables))
        answer = notice("required_steps_missing",
                        count=len(missing_names)) + "\n\n" + answer

    delivery_complete = bool(
        deliverable_needed and final_satisfied
        and not missing_groups and not unavailable_required_groups
        and not unmet_deliverables and not failed_required)
    if delivery_complete and answer:
        answer = _reconcile_delivery_claims(answer, True)

    answer = _append_verified_artifact_links(answer, tool_status_records)
    if delivery_complete:
        from src.graph.document_delivery import compact_word_delivery
        answer = compact_word_delivery(answer, final_validations, deliverable_requirements)

    if answer and "[[FIGURE:" in answer:
        answer = figures.expand(answer)
        # 内部记录键必须止于此：模型偶尔会把 `paper_<hash>...` 抄进正文。
        answer = scrub_internal_keys(answer)
    # Single publication point: the streamed answer must be byte-for-byte the
    # same finalized answer that is persisted and returned in the done payload.
    if answer:
        emit_answer_start()
        emit_text(answer)
    elapsed = (time.monotonic() - started) * 1000
    from src.core.task_result import build_task_result
    task_terminal_status = ""
    if stop_reason == "cancelled":
        task_terminal_status = "cancelled"
    elif stop_reason in {"request_deadline", "tool_turn_limit", "model_timeout",
                         "invalid_tool_protocol"} or stop_reason.startswith("model_error:"):
        task_terminal_status = "partial" if tool_status_records else "failed"
    task_result = build_task_result(
        tool_status_records, missing_requirements,
        requested_status=task_terminal_status)
    logger.info("General Agent finished: %d chars, %d tool types, %.0fms, stop=%s",
                len(answer), len(tool_names_called), elapsed, stop_reason)
    try:
        emit_status("agent_progress", message="已完成本轮来源与工具核对。")
    except Exception:
        pass
    return {
        "answer": answer,
        "gen_time_ms": elapsed,
        "main_results": main_results,
        "web_results": web_results,
        "tools_called": tool_names_called,
        # Structured per-tool terminal states, so the API layer can classify the
        # turn without inferring from answer prose.
        "tool_status": [dict(r) for r in tool_status_records],
        # Machine-readable export attribution: model-initiated vs delivery-gate.
        "export_gate": export_gate,
        "turn_trace": _safe_persisted_trace(messages[trace_start:]),
        "_trace": {"node": "supervisor", "elapsed_ms": elapsed,
                   "summary": f"{len(answer)} chars, {len(tool_names_called)} tool types, {stop_reason}",
                   "tool_turns": tool_turns,
                   "source_scope": scope,
                   "retrieval_decision": retrieval_decision.as_diagnostic(),
                   "retrieval_mode": retrieval_decision.mode,
                   "retrieval_reason_code": retrieval_decision.reason_code,
                   "history_turn_seqs": list(retrieval_decision.history_turn_seqs),
                   "required_tools_unattempted": [sorted(group) for group in missing_groups],
                   "required_tools_unavailable": [sorted(group) for group in unavailable_required_groups],
                   "request_constraints": {
                       "source_policy": request_constraints.source_policy,
                       "requested_mode": request_constraints.requested_mode,
                       "data_audit_requested": request_constraints.data_audit_requested,
                       "pure_transform": request_constraints.pure_transform,
                       "reason_code": request_constraints.reason_code,
                   },
                   "task_result": task_result.as_dict(),
                   "local_route": "called" if any(n.startswith("local_") for n in tool_names_called) else "not_called",
                   "local_tool_types": [n for n in tool_names_called if n.startswith("local_")],
                   "retrieval_disabled_simple_query": retrieval_disabled,
                   "tool_policy_reason": tool_policy.reason},
    }


def build_general_agent_graph():
    from src.graph.session_nodes import load_session_context, save_session_context

    graph = StateGraph(AgentState)
    graph.add_node("session_load", load_session_context)
    graph.add_node("supervisor", general_agent_node)
    graph.add_node("session_save", save_session_context)
    graph.set_entry_point("session_load")
    graph.add_edge("session_load", "supervisor")
    graph.add_edge("supervisor", "session_save")
    graph.add_edge("session_save", END)
    return graph.compile()

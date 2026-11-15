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

from src.config import get_deepseek_model, settings
from src.core.cache_metrics import emit_usage_from_response
from src.core.execution_ledger import (
    ExecutionLedger, bind_execution_ledger, current_execution_ledger,
    reset_execution_ledger,
)
from src.core.model_capabilities import model_supports_images
from src.core import figures
from src.core.record_label import display_label, scrub_internal_keys
from src.core.turn_notices import is_incomplete, notice
from src.core.progress_bus import (
    emit_answer_start, emit_status, emit_text,
    emit_tool_call_start, emit_tool_executing, emit_tool_result,
)
from src.core.public_progress import (
    emit_public_progress, public_progress_text, reset_emitted,
)
from src.graph.state import AgentState
from src.graph.tool_availability import available_tool_names, runtime_env
from src.graph.retrieval_router import (
    decide_retrieval, parse_request_constraints, retrieval_tools_allowed,
    source_scope as _source_scope,
)
from src.graph.tool_policy import resolve_tool_policy
from src.prompts.loader import assemble_system_prompt

from src.graph.model_driver import (
    _budgeted_model_attempt,
    _call_llm,
    _call_llm_body,
    _final_reserve_sec,
    _llm_timeout,
    _model_timeout,
    _provider_failure_notice,
    _request_timeout_disabled,
    _transient_stream_errors,
    request_deadline_from_settings
)

logger = logging.getLogger(__name__)

_FINAL_INSTRUCTION = (
    "现在请根据用户的原始问题，以及本轮已取得的全部网页和本地证据，"
    "直接给出最终回答。用户明确指定的长度、数量和格式优先；默认简洁，"
    "不要把单篇论文请求扩成综述。按问题需要自然组织篇幅和结构；重要事实可直接附上"
    "工具实际返回的 Markdown URL 或 DOI 链接。不要声称做过未完成的核验，"
    "不要输出检索过程或固定格式说明。"
)
_MAX_MODEL_TOOL_TURNS = 8
# A request that must end with a file spends its last turns assembling and exporting
# it; without headroom the loop stops right after evidence collection and the
# deliverable never happens.  The budget therefore grows for deliverable requests
# and two turns are reserved for the export itself.
_DELIVERABLE_TURN_HEADROOM = 4


def _tool_turn_budget(query: str) -> int:
    base = int(getattr(settings, "MAX_REACT_STEPS", _MAX_MODEL_TOOL_TURNS) or _MAX_MODEL_TOOL_TURNS)
    base = max(6, min(base, 24))
    if _deliverable_export_requested(query):
        base += _DELIVERABLE_TURN_HEADROOM
        if any("docx" in r.get("formats", []) for r in _deliverable_contract(query)):
            base += 4  # create + batched commits + explicit finalization
    return max(6, min(base, 24))








_MAX_TOOL_CALLS_PER_BATCH = 8
_MAX_SOURCE_ENFORCEMENT_ATTEMPTS = 2
_COMPLETION_TOKEN_BUCKETS = (512, 1024, 1536, 2048, 4096, 8192, 12288, 16384, 32768)
_MAX_PARALLEL_AGENT_TOOLS = 8
# Tools that declare the same concurrency_key contend for one bounded resource
# (the same workbook, the same artifact directory, the same provider). The total
# pool stays at _MAX_PARALLEL_AGENT_TOOLS; this is an additional per-key bound.
_MAX_PARALLEL_PER_KEY = 4
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
    r"参考文献|文献|论文|引用|出版记录|书目信息|citation|reference|bibliograph|书目|doi|题录", re.I)
_REFERENCE_ANAPHOR = re.compile(
    r"(?:这|那|上述|前述|这些|该)\s*(?:\d+\s*)?(?:篇|条|记录|文献|论文|引用)|"
    r"\b(?:these|those|their|above)\b", re.I)
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
    previous_sentence_context = False
    for sentence in re.split(r"[。！？!?；;\n]+", str(query or "")):
        if not sentence.strip():
            continue
        seen_context = False
        for clause in _intent_clauses(sentence):
            has_context = bool(_REFERENCE_CONTEXT.search(clause))
            inherited = bool(_REFERENCE_ANAPHOR.search(clause)) and (
                seen_context or previous_sentence_context)
            seen_context = seen_context or has_context
            if not (has_context or inherited):
                continue
            for match in _REFERENCE_ACTION.finditer(clause):
                if _negated_before(clause, match.start()):
                    continue
                if _negated_after(clause, match.end()):
                    continue
                return True
        previous_sentence_context = seen_context
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


_EXPLICIT_PAPER_ONLY = re.compile(
    r"(?:只|仅)(?:需|需要)?(?:阅读|读|看|使用|依据|根据|基于|解读|分析|总结)\s*"
    r"(?:(?:我|用户)?(?:上传|提供)的?|本轮|这[份篇]){0,2}"
    r"(?:附件|论文附件|本文|这篇论文|论文正文|PDF)|"
    r"\b(?:only|solely|exclusively)\b.{0,32}\b(?:attachments?|uploaded\s+(?:papers?|pdfs?)|provided\s+papers?)\b",
    re.I,
)


def _read_uploaded_paper_without_search(query: str, scope: str,
                                       has_paper_attachment: bool) -> bool:
    """An upload alone never removes search; only an explicit restriction does."""
    from src.graph.retrieval_router import retrieval_denied
    return bool(
        has_paper_attachment
        and scope == "auto"
        and (retrieval_denied(query, scope)
             or _intent_with_negation(query, _EXPLICIT_PAPER_ONLY))
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
    from src.graph.request_plan import explicit_note_save_request
    return explicit_note_save_request(query)


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


def _bind_request_candidate_pool(state) -> object | None:
    """Bind this request's candidate pool to the current context.

    The pool itself lives in the process registry; only its id travels through
    ``AgentState``. The binding is strict: a pool that no longer exists (TTL,
    cleanup, or a state that was copied between requests) clears the binding
    instead of falling back to whatever happens to be bound.
    """
    from src.retrieval.candidate_pool import (
        bind_candidate_pool, clear_current_candidate_pool, get_pool,
    )

    snapshot = state.get("candidate_pool") if isinstance(state, dict) else None
    pool_id = str((snapshot or {}).get("pool_id") or "")
    if not pool_id:
        clear_current_candidate_pool()
        return None
    pool = get_pool(pool_id=pool_id)
    if pool is None:
        clear_current_candidate_pool()
        return None
    bind_candidate_pool(pool)
    return pool


def _scoped_toolset(scope: str, *, query: str = "", has_table: bool = False,
                    has_paper_attachment: bool = False,
                    has_account_files: bool = False,
                    visualization_enabled: bool = True,
                    has_candidate_pool: bool = False,
                    plan=None):
    """Return the tools that can actually run in this request's environment.

    Visibility is a function of the environment, not of the question's wording.
    A tool disappears only when a declared precondition is unmet (no attachment
    to inspect, no account file to read, no session to write into, research
    tools disabled) or when the *user said so in so many words* (local-only,
    web-only, or "do not search this turn"). Everything else is advertised and
    the model decides whether calling it is worth a step.

    Visibility and execution authorization are separate. ``save_research_note``
    is visible when the environment can run it; the executor rejects the write
    unless the request plan recorded the user's explicit request. A rejected
    call still produces a correlated terminal receipt, so the model cannot
    silently perform an account mutation or pretend it succeeded.
    """
    tools, _schemas = _toolset()
    available = {tool.name: tool for tool in tools}
    q = str(query or "")
    paper_only = _read_uploaded_paper_without_search(q, scope, has_paper_attachment)
    from src.core.tracing import get_session_id, web_search_enabled
    # Configuration readiness is a fact, not a network probe. Direct scholarly
    # APIs remain usable when the separate paid web-search provider lacks a key.
    from src.graph.request_plan import RequestFacts, build_request_plan
    from src.graph.tool_availability import provider_denials
    research_enabled = os.getenv("CITRUS_RESEARCH_TOOLS_ENABLED", "true").strip().lower() not in {
        "0", "false", "no", "off"}
    if plan is None:
        plan = build_request_plan(
            q,
            RequestFacts(
                session_id=get_session_id(),
                attachments=1 if has_table else 0,
                account_files=1 if has_account_files else 0,
                has_paper_attachment=has_paper_attachment,
                network=bool(web_search_enabled()),
                local_corpus=True,
                research_tools=research_enabled,
                visualization=visualization_enabled,
                has_session=bool(get_session_id()),
                has_candidate_pool=has_candidate_pool,
            ),
            selected_scope=scope,
            paper_only=paper_only,
            provider_denials=provider_denials(settings),
        )
    return [available[name] for name in plan.visible_tools if name in available], []


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
        excerpt = " ".join(str(row.get("text") or row.get("snippet") or "").split())[:_EVIDENCE_DIGEST_CHARS]
        meta = "；".join(part for part in (
            f"DOI: {doi}" if doi else "",
            f"语料: {source}" if source else "") if part)
        lines.append(f"[{index}] {title}" + (f"（{meta}）" if meta else "")
                     + (f"\n    摘录: {excerpt}" if excerpt else ""))
    return "\n".join(lines)


_RETRIEVAL_CONTEXT_RE = re.compile(
    r"\n*<retrieval_context>\n.*?\n</retrieval_context>\n*", re.S)


def _strip_retrieval_context(content):
    """Remove the request-scoped ``<retrieval_context>`` block before saving.

    The block carries this turn's candidate-pool counts. A later turn must not
    see them: the pool it describes no longer exists.
    """
    if isinstance(content, str):
        return _RETRIEVAL_CONTEXT_RE.sub("", content)
    if isinstance(content, list):
        blocks = []
        for block in content:
            if (isinstance(block, dict) and block.get("type") == "text"
                    and isinstance(block.get("text"), str)):
                text = dict(block)
                text["text"] = _RETRIEVAL_CONTEXT_RE.sub("", block["text"])
                blocks.append(text)
            else:
                blocks.append(block)
        return blocks
    return content


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
                if _status in {"failed", "skipped", "not_run", "unavailable", "unknown", "cancelled"}:
                    body = ("本轮本地检索未产生候选（该步骤未成功执行，原因见执行记录）；"
                            "不要把“未执行”理解为“语料中没有相关资料”。")
                else:
                    body = "本地检索已完成：未匹配到候选记录。"
            safe.append(ToolMessage(
                content=body,
                tool_call_id=message.tool_call_id, name=message.name, artifact=safe_artifact,
                status=message.status, additional_kwargs=dict(message.additional_kwargs)))
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
                    tool_call_id=message.tool_call_id, name=message.name, artifact=safe_artifact,
                status=message.status, additional_kwargs=dict(message.additional_kwargs)))
            except Exception:
                safe.append(ToolMessage(
                    content="表格结构已在本轮检查；原始单元格样例未写入会话历史。",
                    tool_call_id=message.tool_call_id, name=message.name, artifact=safe_artifact,
                status=message.status, additional_kwargs=dict(message.additional_kwargs)))
        elif isinstance(message, ToolMessage) and getattr(message, "name", "") in {
                "inspect_candidates", "search_candidates"}:
            # Candidate metadata only. The pool is request-scoped, so the
            # history keeps "what was inspected" and not the candidate text.
            rows = ((getattr(message, "artifact", None) or {}).get("main_results") or [])
            if rows:
                digest = _evidence_digest(rows)
                body = (f"候选池已查看：{len(rows)} 条候选元数据"
                        "（会话历史仅保留标题/DOI/短摘录，候选正文未写入历史）。"
                        + (f"\n{digest}" if digest else ""))
            else:
                body = ("本轮候选池查看未返回候选"
                        "（该步骤未成功执行，原因见执行记录）；"
                        "不要把“未执行”理解为“语料中没有相关资料”。")
            safe.append(ToolMessage(
                content=body,
                tool_call_id=message.tool_call_id, name=message.name, artifact=safe_artifact,
                status=message.status, additional_kwargs=dict(message.additional_kwargs)))
        elif isinstance(message, ToolMessage) and getattr(message, "name", "") == "read_candidate":
            # The read slice is request-scoped evidence; the history records
            # that a candidate was read, never the slice itself.
            safe.append(ToolMessage(
                content="已读取候选原文片段（请求级；候选正文未写入会话历史）。",
                tool_call_id=message.tool_call_id, name=message.name, artifact=safe_artifact,
                status=message.status, additional_kwargs=dict(message.additional_kwargs)))
        elif isinstance(message, HumanMessage) and isinstance(message.content, list):
            # Vision payloads are request-scoped; keep a trace placeholder, not
            # base64 image bytes, in session history.
            blocks = []
            for block in message.content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    blocks.append({"type": "text", "text": "[本轮图片附件已用于分析；图像数据未写入会话历史]"})
                elif isinstance(block, dict) and block.get("type") == "text":
                    text = dict(block)
                    text["text"] = _RETRIEVAL_CONTEXT_RE.sub("", block.get("text") or "")
                    blocks.append(text)
                else:
                    blocks.append(block)
            safe.append(HumanMessage(content=blocks, additional_kwargs=message.additional_kwargs))
        elif isinstance(message, HumanMessage):
            # The candidate-pool notice belongs to this turn only. Keeping it in
            # history would tell the next turn about a pool that no longer
            # exists.
            safe.append(HumanMessage(
                content=_strip_retrieval_context(message.content),
                additional_kwargs=message.additional_kwargs))
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

        # Reject malformed or schema-incompatible calls before dispatch, using
        # the same checker the executor uses. The executor still performs its
        # own validation and sandboxing.
        from src.tools.argument_check import argument_problem, schema_fields

        if not args or argument_problem(name, args, schema_fields(tool)):
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
    """Expose the active registry; capability filtering belongs to ``Env``."""
    from src.tools import _TOOL_REGISTRY

    return list(_TOOL_REGISTRY), []


def _tool_call_dict(tc) -> dict:
    if isinstance(tc, dict):
        return {"id": tc.get("id", ""), "name": tc.get("name", ""),
                "args": dict(tc.get("args") or {})}
    return {"id": getattr(tc, "id", ""), "name": getattr(tc, "name", ""),
            "args": dict(getattr(tc, "args", None) or {})}


def _logical_key(tool_name: str, call: dict, step: dict | None = None) -> str:
    """Group attempts of the same logical step without exposing arguments.

    Dispatched calls reuse their exact retry/dedup signature. Gate calls retain
    their explicit target identity; the fallback hashes arguments rather than
    grouping unrelated inputs under a bare tool name.
    """
    args = call.get("args") if isinstance(call, dict) else {}
    args = args if isinstance(args, dict) else {}
    # Provider retry/dedup identity is already established from the complete
    # input signature. Reuse it for the ledger and acceptance, not just a name
    # or a truncated title/query that could merge distinct operations.
    if call.get("_signature"):
        return f"{tool_name}:{call['_signature']}"
    if tool_name == "word_document":
        target = "|".join(str(args.get(k) or "") for k in ("document_id", "operation", "batch_id", "expected_version", "offset"))
        return "word_document:" + hashlib.sha256(target.encode()).hexdigest()[:20]
    for key in ("title", "case_id", "case", "output_name", "note"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return f"{tool_name}:{value.strip()[:48]}"
    if tool_name in {"inspect_candidates", "read_candidate", "search_candidates"}:
        # Candidate steps are grouped by the candidate they act on, so a
        # re-read of the same candidate updates one timeline row.
        candidate = str(args.get("candidate_id") or "").strip()
        if candidate:
            return f"{tool_name}:{candidate[:48]}"
        gap = str(args.get("gap") or "").strip()
        if gap:
            return f"{tool_name}:{gap[:48]}"
        query = str(args.get("query") or "").strip()
        if query:
            return f"{tool_name}:{query[:48]}"
        return tool_name
    target = "|".join(str(args.get(name) or "") for name in
                      ("file_index", "file_indexes_json", "file", "file_name", "path",
                       "sheet_name", "header_rows_json", "query", "doi", "arxiv_id"))
    if target.strip("|"):
        return f"{tool_name}:{target[:48]}"
    return tool_name + ":" + hashlib.sha256(json.dumps(
        args, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


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
    r"(?:未|没有|尚未|未能)\s*(?:实际)?生成\s*(?:任何|两份|三份|\d+\s*份)?"
    r"\s*(?:(?:HTML|XLSX|PDF|DOCX|Word)\b\s*(?:报告|文件|简报)?|报告|文件|简报)|"
    r"(?:报告|文件|简报|HTML|XLSX|PDF|DOCX)[^。！？；;，,\n]{0,16}"
    r"(?:未|没有|尚未|未能)(?:实际)?(?:生成|导出)|"
    r"(?:没有|无法给出|无法提供|不能提供)\s*(?:可)?下载(?:位置|链接)|"
    r"(?:没有|无)\s*下载链接(?:可|能)?(?:提供|给出)|"
    # 「需要重新执行一次导出」允许量词，但不能把科学内容的「重新生成数据」删掉。
    r"(?:仍?需要|仍?需|要|须)\s*(?:重新|再次|再)\s*(?:执行|尝试|运行|进行|做)?"
    r"[^。！？；;，,\n]{0,8}?(?:导出|保存(?:为)?(?:报告|文件)?|生成\s*(?:报告|文件|HTML|DOCX))|"
    r"(?:如果|如需|需要我|要我|你可以)[^。！？\n]{0,24}(?:导出|保存为)[^。！？\n]{0,16}"
    r"(?:报告|文件|HTML)|"
    r"(?:该项|本项)?交付(?:尚|仍|还)?未完成",
    re.I)
# 部分交付时只纠正「没有任何下载／通道」这类整体否认，保留特定未交付文件
# 的说明。仅凭已有一份文件，无法推断另一份简报也已经存在。
_GLOBAL_DELIVERY_DENIAL_RE = re.compile(
    r"(?:没有|无|无法给出|无法提供|不能提供)[^。！？；;，,\n]{0,12}"
    r"(?:下载|导出通道|导出能力|导出接口)|"
    r"导出(?:工具|步骤|功能)?\s*(?:未|没有|并未)|"
    r"(?:未|没有|尚未|未能)\s*(?:实际)?生成\s*(?:任何|两份|三份|\d+\s*份)|"
    r"(?:仍?需要|仍?需|要|须)[^。！？；;，,\n]{0,16}导出", re.I)
_DELIVERY_CORRECTION_MARK = "交付状态更正"
_DELIVERY_CORRECTION_RE = re.compile(r"（交付状态更正：[^）]*）")


def _is_delivery_denial(sentence: str) -> bool:
    """A claim that a *file* is missing — not a correction saying it exists."""
    text = str(sentence or "")
    if not text.strip() or _DELIVERY_CORRECTION_MARK in text:
        return False
    return bool(_DELIVERY_DENIAL_RE.search(text.replace("**", "")))


def _delivery_correction_text(delivered_count: int, outstanding_count: int) -> str:
    if outstanding_count > 0:
        return (
            f"（{_DELIVERY_CORRECTION_MARK}：本轮已生成 {delivered_count or 1} 份通过校验的可下载文件，"
            f"链接见文末；仍有 {outstanding_count} 项要求的产物未生成，"
            "未生成的部分不会用已完成文件冒充。）")
    return (f"（{_DELIVERY_CORRECTION_MARK}："
            "本轮已生成并通过校验的可下载文件，链接见文末。）")


def _refresh_required_step_notices(answer: str, *, missing_count: int,
                                   failed_count: int) -> str:
    """Recount generated notices from final receipts, never from prose deletions."""
    text = str(answer or "")
    if not text:
        return text
    for code in ("required_steps_failed", "required_steps_missing"):
        pattern = re.escape(notice(code, count="COUNT")).replace("COUNT", r"\d+")
        text = re.sub(pattern, "", text)
    from src.core.turn_notices import INCOMPLETE_PREFIX
    combined = re.escape(notice("required_steps_incomplete", failed="FAILED", missing="MISSING").rstrip("。"))
    combined = combined.replace("FAILED", r"\d+").replace("MISSING", r"\d+")
    # A merged underlying cause uses a semicolon instead of the final period.
    # Recounting must remove the old counts while retaining that cause's prefix.
    text = re.sub(combined + r"(?P<ending>[。；])",
                  lambda match: INCOMPLETE_PREFIX if match["ending"] == "；" else "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not (missing_count or failed_count):
        return text
    summary = notice("required_steps_incomplete", failed=failed_count, missing=missing_count)
    if is_incomplete(text):
        from src.core.turn_notices import INCOMPLETE_PREFIX
        # Keep the underlying failure once in the same notice; preserve normal
        # answer text and any verified artifact links after it.
        return summary.rstrip("。") + "；" + text[len(INCOMPLETE_PREFIX):]
    return summary + "\n\n" + text


def _contract_repair(contract, coverage, available: set[str]) -> tuple[str, str] | None:
    """Next repair instruction for an open obligation, when a bound tool can act.

    The loop asks the contract, not a keyword list, what is still owed. Only
    obligations with an available repair tool are retried here; a gap whose tool
    is not bound ends the turn as an honest partial instead of looping.
    """
    gaps = coverage.gaps(contract)
    if not gaps:
        return None
    names = sorted(str(name) for name in available)
    for obligation in getattr(contract, "obligations", ()) or ():
        tools = obligation.repair_tools(names)
        if not tools:
            continue
        relevant = [gap for gap in gaps
                    if str(gap.get("obligation_id")) == obligation.obligation_id]
        if not relevant:
            continue
        gap = relevant[0]
        if gap.get("reason_code") == "reference_extraction_incomplete":
            # Repeating DOI extraction cannot prove coverage of non-DOI rows.
            continue
        tool = tools[0]
        detail = str(gap.get("detail") or gap.get("reason_code") or "")
        if obligation.kind == "input":
            return ("任务缺少必要输入：" + detail + "。请通过 request_user_input 询问用户，并等待答复。",
                    "需要补充任务所需的输入。")
        if obligation.kind == "figure" and obligation.subject.get("phase") == "edit":
            return ("已有图件的修改尚未保存。用 research_workspace(operation='figure_inspect') 读取真实对象，"
                    "再用 figure_edit 提交 overrides 与 expected_revision。按用户意愿编辑，不重新套用 Nature 美学。",
                    "正在保存所需的图件修改…")
        if obligation.kind == "figure":
            return (
                "用户要求的图件尚未生成（" + detail + "）。请调用 " + tool
                + " 生成图件后再结束本轮：初始绘图必须加载并遵循 nature-figure 技能，"
                "并确认返回了 figure_id 与至少一个可打开导出格式（svg/png/pdf）。"
                "不要用表格、代码或说明文字代替图件。",
                "正在补做用户要求的图件…")
        if obligation.kind == "verification":
            return (
                "文献核验仍有未完成的记录（" + detail + "）。请调用 " + tool
                + " 继续未完成的记录，用起始位置或参数覆盖剩余范围。"
                "不要重复已完成的同一批记录；若范围确实已到末尾或缺少可核验标识符，"
                "如实说明未完成原因并结束。",
                "正在补齐未完成的文献核验…")
        if obligation.kind == "audit":
            return (
                "数据审查尚未覆盖用户要求的全部文件（" + detail + "）。请调用 " + tool
                + " 审查尚未覆盖的文件索引，不要重复已完成的文件。",
                "正在补齐未覆盖的数据审查…")
    return None


def _append_contract_notices(answer: str, notices) -> str:
    """State unmet obligations in the answer, once, using the shared marker."""
    from src.core.turn_notices import INCOMPLETE_PREFIX, is_incomplete

    text = str(answer or "")
    extra = [str(item).strip().rstrip("。") for item in (notices or ())
             if str(item or "").strip()]
    extra = [item for item in extra if item and item not in text]
    if not extra:
        return text
    body = "；".join(extra) + "。"
    if is_incomplete(text):
        head, _, rest = text.partition("\n")
        merged = head.rstrip("。") + "；" + body
        return merged + (("\n\n" + rest.strip()) if rest.strip() else "")
    block = INCOMPLETE_PREFIX + body
    return (block + "\n\n" + text) if text else block


def _reconcile_delivery_claims(answer: str, delivered: bool, *,
                               delivered_count: int = 0,
                               outstanding_count: int = 0) -> str:
    """Drop now-false "I could not create a file" claims after a real delivery.

    The model writes its prose before the delivery gate runs, so it can claim
    "no export channel / the export tool never ran" while the gate then supplies
    a verified file. Leaving both in one answer is a contradiction the user sees.
    Only denial clauses are removed (structure is preserved), and one correction
    sentence explains the change; the caller appends the real links.

    A partially delivered turn still needs this: the files that *do* exist must
    not be described as missing. What is genuinely outstanding stays stated.
    """
    text = str(answer or "")
    if not text or not delivered:
        return text
    body, previous_corrections = _DELIVERY_CORRECTION_RE.subn("", text)
    # Commas/semicolons can separate a false export claim from a true unfinished
    # verification. Preserve the latter even when both were written on one line.
    parts = re.split(r"([。！？!?；;，,]|\n)", body)
    kept: list[str] = []
    removed = 0
    for index in range(0, len(parts), 2):
        content = parts[index]
        separator = parts[index + 1] if index + 1 < len(parts) else ""
        denial = _is_delivery_denial(content)
        if outstanding_count and not _GLOBAL_DELIVERY_DENIAL_RE.search(
                content.replace("**", "")):
            denial = False
        if denial:
            removed += 1
            kept.append("\n" if separator == "\n" else "")
            continue
        kept.append(content + separator)
    if not removed and not previous_corrections:
        return text
    body = re.sub(r"\n{3,}", "\n\n", "".join(kept)).strip()
    correction = _delivery_correction_text(delivered_count, outstanding_count)
    # Keep any genuine incomplete notice at the head for existing clients. Only
    # the generated required-step counts are refreshed later from machine data.
    if is_incomplete(body):
        head, _, rest = body.partition("\n")
        return head + "\n\n" + correction + ("\n\n" + rest.strip() if rest.strip() else "")
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


def _artifact_link_labels() -> dict[str, str]:
    """Download labels are declared next to capability metadata."""
    from src.core.tool_catalog import artifact_link_labels

    return artifact_link_labels()


# Compatibility alias; new code should call ``_artifact_link_labels()``.
_ARTIFACT_LINK_LABELS = _artifact_link_labels()


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
        label = _artifact_link_labels().get(name, "下载生成文件")
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


def _artifact_failure_receipt(failure: str, records: list[dict]) -> str:
    """A deterministic file receipt never substitutes for scientific acceptance."""
    count = len({str(v.get("link")) for row in records
                 for v in row.get("artifact_validations", ())
                 if isinstance(v, dict) and v.get("valid") and v.get("link")})
    if not count:
        return failure
    reason = str(failure).removeprefix("本轮未完成：")
    return (f"本轮已生成并校验 {count} 个文件，可在下方查看或下载。"
            f"\n\n最后的文字整理未完成：{reason}"
            "尚未完成的分析或核验仍以任务记录为准；文件生成不代表这些步骤已完成。")


def _record_execution_terminal(call: dict, step: dict, content: str,
                               artifact_validations: list[dict]) -> None:
    """Register one terminal state in the request-scoped execution ledger."""
    ledger = current_execution_ledger()
    if ledger is None:
        return
    try:
        # Skips have no running event, but still share the existing operation
        # identity. start() is idempotent and cannot reset a registered attempt.
        ledger.start(
            str(call.get("id") or ""), str(call.get("name") or ""),
            call.get("args") or {}, source=str(call.get("_source") or "provider"),
            logical_key=str(step.get("logical_key") or ""),
            attempt=int(step.get("attempt") or 1))
        ledger.terminal(
            str(call.get("id") or ""),
            str(step.get("status") or "failed"),
            tool_name=str(call.get("name") or ""),
            reason_code=str(step.get("reason_code") or ""),
            result_chars=len(content),
            artifact_validations=artifact_validations,
            source=str(call.get("_source") or "provider"),
        )
    except Exception:
        logger.debug("execution ledger terminal failed", exc_info=True)


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
        from src.core.task_events import result_details
        step.update(result_details(content, artifact))
        # Tool links are claims until the artifact registry, ownership record and
        # bytes on disk corroborate them. Model-authored Markdown never counts.
        artifact_validations: list[dict] = []
        claimed_links = tuple(step.get("artifacts") or ())
        try:
            from src.core.progress_bus import get_turn_scope
            from src.core.tracing import get_client_request_id, get_user_id
            if claimed_links:
                from src.research.tools import validate_artifact_links
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
        logical_key = _logical_key(name, call, step)
        attempt = max(1, int(call.get("_attempt") or 1))
        ledger = current_execution_ledger()
        registered = ledger.record(str(call.get("id") or "")) if ledger else None
        if registered is not None:
            logical_key, attempt = registered.logical_key, registered.attempt
        step["logical_key"] = logical_key
        step["attempt"] = attempt
        if tool_status_sink is not None:
            from copy import deepcopy
            tool_status_sink.append({
                "name": name,
                "tool_call_id": str(call.get("id") or ""),
                "logical_key": logical_key,
                "attempt": attempt,
                "reused_from_tool_call_id": str(call.get("_reused_from_tool_call_id") or ""),
                "status": step["status"],
                "reason": step["reason_code"],
                # Links are held in the tool result ledger; only the verified
                # count crosses into the task result contract.
                "artifact_count": len(step.get("artifacts") or []),
                "artifact_validations": artifact_validations,
                "op_receipt": deepcopy(artifact.get("op_receipt") or {}),
            })
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
        # Persist the actual execution output before creating any first-delivery
        # reference view. A failed write must never advertise a readback handle.
        try:
            from src.core import tool_results as _tool_results
            from src.core import turn_source_ledger as _source_ledger
            from src.core.progress_bus import get_turn_scope as _get_scope
            _sid, _rid = _get_scope()
            reference = _tool_results.record_with_reference(
                tool_call_id=str(call.get("id") or ""),
                session_id=_sid, request_id=_rid, tool_name=name,
                status=str(trace_record.get("status") or ""),
                args=call.get("args") or {}, result_text=content,
                error_text=(content if step.get("status") == "failed" else ""),
                elapsed_ms=int(trace_record.get("elapsed_ms") or 0),
                summary=str(trace_record.get("summary") or ""))
            message.additional_kwargs["result_storage"] = (
                reference["storage"] if reference else {"state": "unavailable"})
            if reference:
                message.additional_kwargs["result_reference"] = reference
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
        persisted_artifact["_citrus_execution"].update(
            tool_call_id=str(call.get("id") or ""), logical_key=logical_key, attempt=attempt,
            reused_from_tool_call_id=str(call.get("_reused_from_tool_call_id") or ""))
        message.artifact = persisted_artifact
        _record_execution_terminal(call, step, content, artifact_validations)
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
        failed_step = {"status": "failed", "reason_code": "status_unavailable"}
        _record_execution_terminal(call, failed_step, content, artifact_validations)
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


async def _run_regular_calls(calls: list[dict], tool_by_name: dict,
                             completion_callback=None,
                             timeout: float | None = None) -> list[ToolMessage]:
    """Run tools concurrently, retaining completed results across batch timeout."""
    from src.tools.registry import _run_single_tool, concurrency_key_for

    semaphore = asyncio.Semaphore(_MAX_PARALLEL_AGENT_TOOLS)
    key_semaphores: dict[str, asyncio.Semaphore] = {}

    def concurrency_lock(tool_name: str) -> asyncio.Semaphore | None:
        key = concurrency_key_for(tool_name)
        if not key:
            return None
        return key_semaphores.setdefault(
            key, asyncio.Semaphore(_MAX_PARALLEL_PER_KEY))

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
                    lock = concurrency_lock(call.get("name", ""))
                    if lock is None:
                        message = await _run_single_tool(tool, call)
                    else:
                        async with lock:
                            message = await _run_single_tool(tool, call)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # retain a correlated terminal event per call
                message = ToolMessage(
                    content=f"[TOOL_ERROR] {type(exc).__name__}",
                    tool_call_id=call.get("id", ""),
                    name=call.get("name", ""), artifact={})
        # Preserve schema receipts before any model projection can shorten JSON.
        # These values originate in tool execution, never assistant arguments.
        if isinstance(message, ToolMessage):
            try:
                payload = json.loads(str(message.content or ""))
            except (ValueError, TypeError):
                payload = None
            if isinstance(payload, dict):
                artifact = dict(message.artifact or {})
                if message.name == "inspect_uploaded_table" and isinstance(payload.get("source_refs"), list):
                    artifact["source_refs"] = payload["source_refs"]
                if isinstance(payload.get("dataset_ref"), dict):
                    artifact["dataset_ref"] = payload["dataset_ref"]
                message = message.model_copy(update={"artifact": artifact})
        # Preserve facts before the model-facing projection can truncate JSON.
        from src.core.op_receipt import make_receipt
        artifact = dict(message.artifact or {})
        if "op_receipt" not in artifact:
            artifact["op_receipt"] = make_receipt(message.name or call.get("name", ""),
                message.tool_call_id, message.content, artifact)
        message = message.model_copy(update={"artifact": artifact})
        # Publish one terminal outcome only after an actual result exists.
        publish(call, message)
        # A verified large result may first enter context through its reader
        # contract. Once delivered, this message stays unchanged for the turn.
        from src.core.tool_result_view import first_delivery
        message = first_delivery(message, call=call,
                                 reader_available="read_tool_result" in tool_by_name)
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


async def _execute_calls(response, *, tools_by_name: dict,
                         deadline: float,
                         seen_call_signatures: dict[str, dict[str, Any]],
                         tool_status_sink: list[dict] | None = None) -> list[ToolMessage]:
    calls = [_tool_call_dict(tc) for tc in (getattr(response, "tool_calls", None) or [])]
    # A clarification is a barrier, not one more parallel action. Even tools
    # listed before it must not execute using an answer that does not exist.
    questions = [c for c in calls if c["name"] == "request_user_input"]
    question_id = questions[0]["id"] if questions else None
    fresh_calls = []
    queued_signatures: dict[str, dict] = {}
    attempts: dict[str, int] = {}
    completed: dict[str, ToolMessage] = {}
    for index, call in enumerate(calls):
        # Deferred calls must keep the same identity when a later batch executes
        # them; otherwise their not-run receipt remains an unrelated failure.
        signature = hashlib.sha256(json.dumps(
            [call["name"], call.get("args") or {}], ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        call["_signature"] = signature
        if questions and (call["id"] != question_id or call["name"] != "request_user_input"):
            completed[call["id"]] = ToolMessage(
                content="[TOOL_DUPLICATE_SKIPPED] 等待用户答复；同批依赖操作未执行。",
                tool_call_id=call["id"], name=call["name"], artifact={
                    "_tool_outcome": {"status": "not_run", "reason_code": "awaiting_user_input"}})
            continue
        previous = seen_call_signatures.get(signature)
        # Script repair admission is persisted and scoped by task/input/output
        # in script_runtime. A global per-tool counter would block unrelated
        # figures and reset across clarification continuations.
        previous_attempts = (int(previous.get("attempts", 1))
                             if isinstance(previous, dict) else 0)
        retry_allowed = bool(
            isinstance(previous, dict)
            and previous.get("retryable")
            and previous_attempts < 2
        )
        if signature in queued_signatures or (previous is not None and not retry_allowed):
            source = queued_signatures.get(signature) or previous or {}
            call["_reused_from_tool_call_id"] = str(source.get("id") or source.get("tool_call_id") or "")
            call["_attempt"] = int(source.get("_attempt") or source.get("attempts") or 1)
            completed[call["id"]] = ToolMessage(
                content="[TOOL_DUPLICATE_SKIPPED] This identical tool request already has a usable or non-retryable outcome; use it or choose a new target.",
                tool_call_id=call["id"], name=call["name"], artifact={})
        elif index >= _MAX_TOOL_CALLS_PER_BATCH and not questions:
            # Reuse proven outcomes before applying the batch limit: an overflow
            # duplicate must not overwrite an already successful operation.
            completed[call["id"]] = ToolMessage(
                content=(f"[TOOL_BATCH_LIMIT] 本轮最多执行 {_MAX_TOOL_CALLS_PER_BATCH} 项工具；"
                         "此项未执行，请先综合已有结果。"),
                tool_call_id=call["id"], name=call["name"], artifact={})
        else:
            queued_signatures[signature] = call
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
            _ledger = current_execution_ledger()
            if _ledger is not None:
                _ledger.start(
                    call["id"], call["name"], call.get("args") or {},
                    source=str(call.get("_source") or "provider"),
                    logical_key=key, attempt=call["_attempt"])
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
    from src.tools.registry import get_tool_spec
    for call, message in zip(fresh_calls, results):
        signature = str(call.get("_signature") or "")
        if not signature:
            continue
        step = terminal_steps.get(str(call.get("id") or "")) or safe_tool_step(
            str(call.get("name") or ""), str(getattr(message, "content", "") or ""),
            getattr(message, "artifact", None))
        spec = get_tool_spec(str(call.get("name") or ""))
        # A timeout may leave its worker running. Only a declared read can be
        # repeated safely without duplicating persistent side effects. The same
        # two-attempt limit above applies; cancellation and validation never qualify.
        transient_read = bool(
            spec is not None and spec.is_readonly and spec.write_scope is None
            and step["status"] in {"failed", "partial"}
            and step.get("reason_code") in {"timeout", "network"}
            and not step.get("artifacts") and not step.get("artifact_validations"))
        seen_call_signatures[signature] = {
            "name": str(call.get("name") or ""),
            "tool_call_id": str(call.get("id") or ""),
            "status": step["status"],
            "retryable": bool(step.get("retryable")) or transient_read,
            "attempts": int(call.get("_signature_attempt") or 1),
        }
    completed.update({str(message.tool_call_id): message for message in results})
    results = [completed[call["id"]] for call in calls]

    return results

# One stream attempt, at most one non-stream transport recovery. Repeating
# the same large prompt up to five times concealed the real cost of timeouts.














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


def _control_message(*, content):
    return HumanMessage(content=content, additional_kwargs={
        "context_origin": "internal_control", "context_trusted": True})


def _task_working_state(state, query, records, requirements):
    request_id = str(state.get("request_id") or state.get("idempotency_key") or "current")
    pending, resolved, facts = [], [], []
    validations = [v for record in records for v in record.get("artifact_validations", []) if v.get("valid")]
    for index, requirement in enumerate(requirements):
        source_id = request_id + ":deliverable:" + str(index)
        if _deliverables_satisfied(validations, [requirement]):
            resolved.append(source_id)
        else:
            pending.append({"source_id": source_id, "text": json.dumps(requirement, ensure_ascii=False)})
    for record in records:
        refs = [dict(v) for v in record.get("artifact_validations", []) if v.get("valid")]
        if refs:
            facts.append({"text": "已校验产物；科学结论须以分析来源为准", "refs": refs})
    previous = state.get("context_working_state") or {}
    def unique(items):
        seen, result = set(), []
        for item in items:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result
    return {"goal": query,
            "plot_intent": state.get("plot_intent") or previous.get("plot_intent") or {},
            "script_budget_scope": state.get("script_budget_scope") or previous.get("script_budget_scope") or request_id,
            "script_refs": unique(list(previous.get("script_refs", [])) + list(state.get("script_refs") or []))[-32:],
            "run_refs": unique(list(previous.get("run_refs", [])) + list(state.get("run_refs") or []))[-32:],
            "dataset_refs": unique(list(previous.get("dataset_refs", [])) + list(state.get("dataset_refs") or [])),
            "source_refs": unique(list(previous.get("source_refs", [])) + list(state.get("source_refs") or [])),
            "constraints": unique(list(previous.get("constraints", [])) + [{"text": query, "source_id": request_id + ":user"}]),
            "verified_facts": unique(list(previous.get("verified_facts", [])) + facts),
            "unfinished": unique([item for item in previous.get("unfinished", [])
                                    if not isinstance(item, dict) or item.get("source_id") not in resolved] + pending),
            "resolved_source_ids": resolved}


def _capture_interaction_refs(state, results):
    """Capture host/tool-owned receipts, never assistant prose or model args."""
    working = state.get("context_working_state") or {}
    datasets = list(state.get("dataset_refs") or working.get("dataset_refs") or [])
    sources = list(state.get("source_refs") or working.get("source_refs") or [])
    scripts = list(state.get("script_refs") or working.get("script_refs") or [])
    runs = list(state.get("run_refs") or working.get("run_refs") or [])
    for message in results:
        artifact = getattr(message, "artifact", None)
        payloads = [artifact] if isinstance(artifact, dict) else []
        try:
            value = json.loads(str(message.content or ""))
            if isinstance(value, dict):
                payloads.append(value)
        except (ValueError, TypeError):
            pass
        for value in payloads:
            if message.name in {"research_workspace", "run_research_figure_script"}:
                ref = value.get("script_ref")
                if isinstance(ref, dict) and ref.get("kind") in {"research_script", "builtin_script"} and ref not in scripts:
                    scripts.append(ref)
                if value.get("run_id"):
                    run_ref = {"kind": "script_run", "id": value["run_id"]}
                    if run_ref not in runs:
                        runs.append(run_ref)
                if message.name == "research_workspace" and isinstance(value.get("research_intent"), dict):
                    state["plot_intent"] = value["research_intent"]
            candidates = list(value.get("dataset_refs") or [])
            if isinstance(value.get("dataset_ref"), dict):
                candidates.append(value["dataset_ref"])
            for ref in candidates:
                if isinstance(ref, dict) and ref.get("kind") == "dataset_snapshot" and ref not in datasets:
                    datasets.append(ref)
            if message.name == "inspect_uploaded_table":
                for ref in value.get("source_refs") or []:
                    if isinstance(ref, dict) and ref not in sources:
                        sources.append(ref)
    state["dataset_refs"], state["source_refs"] = datasets, sources
    state["script_refs"], state["run_refs"] = scripts[-32:], runs[-32:]


def _question_context_for(state, query, records, requirements):
    working = _task_working_state(state, query, records, requirements)
    return {"query": query, "context_working_state": working,
            "source_refs": working.get("source_refs", []),
            "dataset_refs": working.get("dataset_refs", []),
            "script_refs": working.get("script_refs", []),
            "run_refs": working.get("run_refs", []),
            "attachment_refs": state.get("attachment_refs") or [],
            "visualization_enabled": bool(state.get("visualization_enabled")),
            "tool_status": [dict(r) for r in records],
            "no_replay": True}

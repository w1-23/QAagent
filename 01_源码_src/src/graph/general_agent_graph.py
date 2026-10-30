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
import os
import re
import time
import uuid

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph

from src.config import get_deepseek_model, settings
from src.core.cache_metrics import emit_usage_from_response
from src.core.model_capabilities import model_supports_images
from src.core import figures
from src.core.turn_notices import is_incomplete, notice
from src.core.progress_bus import (
    emit_status, emit_text, emit_text_reset,
    emit_tool_call_start, emit_tool_executing, emit_tool_result,
)
from src.core.stream_llm import stream_llm_response
from src.core.public_progress import (
    emit_public_progress, public_progress_text, reset_emitted,
)
from src.graph.state import AgentState
from src.graph.tool_availability import available_tool_names, runtime_env
from src.graph.tool_policy import resolve_tool_policy
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


# ---------------------------------------------------------------- negation
# A clause may explicitly forbid an action ("不要核验", "不用导出").  Every
# intent predicate below runs through this helper so a forbidden capability is
# never advertised.  The check looks at the text BEFORE the intent match inside
# the same clause, which is where the negation naturally sits in Chinese.
_CLAUSE_SPLIT = re.compile(r"[，,。！？!?；;\n]+|但是|不过|然而|\bbut\b|\bhowever\b", re.I)
_NEGATION_TAIL = re.compile(
    r"(?:不要|不需要|无需|不用|不必|不想|不希望|暂不|先不|别|请勿|勿|禁止|"
    r"do\s+not|don't|no\s+need\s+to|without)\s*$", re.I)
# "只列出参考文献，不要核验" — the negation follows the keyword, so a predicate
# that only looks backwards would miss it.  These patterns catch the forward case.
_NEGATION_AFTER = re.compile(
    r"(?:不要|不需要|无需|不用|不必|请勿|勿)\s*(?:再|进行|做)?\s*"
    r"(?:核验|核查|检查|审查|导出|保存|验证|verify|check|export)\b", re.I)


def _intent_clauses(text: str) -> list[str]:
    return [c for c in _CLAUSE_SPLIT.split(str(text or "")) if c.strip()]


def _negated_before(clause: str, start: int) -> bool:
    """True when the text immediately preceding the match forbids the action."""
    return bool(_NEGATION_TAIL.search(clause[:start].rstrip()))


def _negated_after(clause: str, end: int) -> bool:
    """True when the match itself is followed by an explicit prohibition."""
    return bool(_NEGATION_AFTER.match(clause[end:].lstrip()))


def _intent_with_negation(query: str, pattern: re.Pattern) -> bool:
    """Return True only if some clause requests the intent without negating it."""
    for clause in _intent_clauses(query):
        for match in pattern.finditer(clause):
            if _negated_before(clause, match.start()):
                continue
            if _negated_after(clause, match.end()):
                continue
            return True
    return False


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
    for clause in _intent_clauses(text):
        if _GAP_CONTEXT.search(clause) and _GAP_TOPIC.search(clause):
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
    env = runtime_env(
        source_scope=scope,
        attachments=1 if has_table else 0,
        account_files=1 if has_account_files else 0,
        retrieval=policy.allow_retrieval,
        denied=denied | policy.deny,
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
    if explicit:
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
            target = 12288
            guidance = "用户要求深入或长篇输出；按任务需要展开，但不要重复或凑章节。"
        elif paper_recommendation:
            target = 2048
            guidance = ("用户只要推荐一篇现有论文：只提供一篇最匹配且可核实的文献，"
                        "列出标题、作者、期刊/年份及实际 DOI 或 URL，最多用一句话说明相关性；"
                        "不要附领域综述或检索过程。")
        elif visual:
            target = 8192
            guidance = ("用户要图：调用 draw_svg 提交一份紧凑 SVG（viewBox 不超过 1000 宽，文字精简），"
                        "再在回答中用 [[FIGURE:n]] 占位符并附一句说明；不要把 SVG 源码直接写进回答。")
        elif concise:
            target = 1024
            guidance = "用户要求简短或只要指定内容；直接给结果，不补充未请求的背景。"
        elif len(q) <= 120 and not re.search(r"为什么|如何|机制|比较|对比|分析|设计", q):
            # 2048 对“先检索再作答”的一轮（含推理 token）偏紧，实测会截断成空答案。
            target = 4096
            guidance = "按普通问答简洁直接回答；仅补充理解答案必需的信息。"
        else:
            target = 4096
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
        title = " ".join(str(row.get("title") or row.get("section_name") or "Untitled").split())[:160]
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
               "local_hybrid_search", "read_account_file"}
    research_enabled = os.getenv("CITRUS_RESEARCH_TOOLS_ENABLED", "true").strip().lower() not in {
        "0", "false", "no", "off"}
    if research_enabled:
        allowed.update({
            "inspect_uploaded_table", "audit_uploaded_data",
            "verify_uploaded_references", "fetch_and_audit_open_supplements", "export_literature_evidence",
            "export_paper_analysis", "export_experiment_design", "save_research_note",
            "recall_research_notes", "export_research_notes", "export_corpus_gap_report",
        })
    return [tool for name, tool in _TOOL_REGISTRY_BY_NAME.items()
            if name in allowed], []


def _tool_call_dict(tc) -> dict:
    if isinstance(tc, dict):
        return {"id": tc.get("id", ""), "name": tc.get("name", ""),
                "args": dict(tc.get("args") or {})}
    return {"id": getattr(tc, "id", ""), "name": getattr(tc, "name", ""),
            "args": dict(getattr(tc, "args", None) or {})}


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


def _emit_completed_tool_step(call: dict, message: ToolMessage,
                              tool_status_sink: list[dict] | None = None) -> None:
    """Publish one completed tool as soon as it finishes, with no raw payload."""
    name = str(call.get("name") or "")
    content = str(getattr(message, "content", "") or "")
    artifact = getattr(message, "artifact", None) or {}
    try:
        from src.tools.outcome import safe_tool_step
        step = safe_tool_step(name, content, artifact)
        if tool_status_sink is not None:
            tool_status_sink.append({
                "name": name,
                "status": step["status"],
                "reason": step["reason_code"],
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
            summary=summary, outcome=step, action_hint=action_hint)
        # ToolMessage.artifact is deliberately excluded from model-visible
        # content, but is checkpointed with the message. Persist only the
        # sanitized execution record so refresh/history can reproduce real
        # completion order and status without storing tool arguments or prose.
        persisted_artifact = dict(artifact) if isinstance(artifact, dict) else {}
        persisted_artifact["_citrus_execution"] = {
            key: trace_record[key] for key in (
                "status", "reason_code", "summary", "elapsed_ms",
                "completed_at_ms", "action_hint")
        }
        message.artifact = persisted_artifact
    except Exception:
        logger.debug("Unable to publish tool completion event", exc_info=True)
        try:
            from src.core.progress_bus import emit_tool_result
            emit_tool_result(
                name, content, str(call.get("id") or ""), is_error=True,
                summary="这一步的执行状态暂时无法读取",
                outcome={"status": "failed", "reason_code": "status_unavailable"})
        except Exception:
            pass


async def _run_regular_calls(calls: list[dict], tool_by_name: dict,
                             completion_callback=None) -> list[ToolMessage]:
    """Run independent read-only tools concurrently and publish each completion immediately."""
    from src.tools.registry import _run_single_tool

    semaphore = asyncio.Semaphore(_MAX_PARALLEL_AGENT_TOOLS)

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
            except Exception as exc:  # retain a correlated terminal event per call
                message = ToolMessage(
                    content=f"[TOOL_ERROR] {type(exc).__name__}",
                    tool_call_id=call.get("id", ""),
                    name=call.get("name", ""), artifact={})
        if completion_callback:
            completion_callback(call, message)
        return index, message

    indexed = [asyncio.create_task(run_one(index, call))
               for index, call in enumerate(calls)]
    results: list[ToolMessage | None] = [None] * len(indexed)
    try:
        for task in asyncio.as_completed(indexed):
            # as_completed yields each actual completion, rather than waiting for
            # the slowest parallel tool before displaying all results.
            index, message = await task
            results[index] = message
    finally:
        for task in indexed:
            if not task.done():
                task.cancel()
        await asyncio.gather(*indexed, return_exceptions=True)
    return [message for message in results if message is not None]


async def _execute_calls(response, *, tools_by_name: dict,
                         deadline: float, seen_call_signatures: set[str],
                         tool_status_sink: list[dict] | None = None) -> list[ToolMessage]:
    calls = [_tool_call_dict(tc) for tc in (getattr(response, "tool_calls", None) or [])]
    fresh_calls = []
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
        if signature in seen_call_signatures:
            completed[call["id"]] = ToolMessage(
                content="[TOOL_DUPLICATE_SKIPPED] This identical tool request was already attempted; use its earlier result or choose a new target.",
                tool_call_id=call["id"], name=call["name"], artifact={})
        else:
            seen_call_signatures.add(signature)
            fresh_calls.append(call)
        if call["id"] in completed:
            # Terminal without execution (duplicate / batch limit): the result
            # event carries status=skipped, so no running row is created.
            continue
        try:
            emit_tool_call_start(call["name"], call["args"], call["id"])
            emit_tool_executing(f"{call['name']}：调用中", call["name"], call["id"])
        except Exception:
            pass

    for call in calls:
        if call["id"] in completed:
            _emit_completed_tool_step(call, completed[call["id"]], tool_status_sink)
    if not fresh_calls:
        return [completed[c["id"]] for c in calls]
    remaining = max(0.01, deadline - time.monotonic() - _final_reserve_sec())
    try:
        results = await asyncio.wait_for(
            _run_regular_calls(
                fresh_calls, tools_by_name,
                completion_callback=lambda call, message: _emit_completed_tool_step(
                    call, message, tool_status_sink)),
            timeout=remaining)
    except asyncio.TimeoutError:
        results = [ToolMessage(
            content="[ERR_REQUEST_DEADLINE] The request is nearing its time limit. Use evidence already received and answer now.",
            tool_call_id=call["id"], name=call["name"], artifact={}) for call in fresh_calls]
        for call, message in zip(fresh_calls, results):
            _emit_completed_tool_step(call, message, tool_status_sink)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tool batch failed: %s", exc)
        results = [ToolMessage(content=f"[TOOL_ERROR] {type(exc).__name__}: {exc}",
                               tool_call_id=call["id"], name=call["name"], artifact={})
                   for call in fresh_calls]
        for call, message in zip(fresh_calls, results):
            _emit_completed_tool_step(call, message, tool_status_sink)

    completed.update({str(message.tool_call_id): message for message in results})
    results = [completed[call["id"]] for call in calls]

    return results

async def _call_llm(llm, messages, *, timeout: float, on_text=None):
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
        result = await asyncio.wait_for(
            stream_llm_response(llm, messages, on_text=on_text),
            timeout=max(0.1, timeout))
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
    deadline = float(state.get("request_deadline") or
                     (time.monotonic() + max(1, settings.AGENT_REQUEST_TIMEOUT_SEC)))
    started = time.monotonic()
    scope = _source_scope(query)
    explicit_literature = _explicit_literature_request(query)
    explicit_reference_check = _explicit_reference_check(query)
    has_paper_attachment = bool(state.get("has_paper_attachment"))
    paper_only = _read_uploaded_paper_without_search(
        query, scope, has_paper_attachment)
    tool_policy = resolve_tool_policy(query, scope, paper_only=paper_only)
    retrieval_disabled = not tool_policy.allow_retrieval
    figures.begin_request()
    explicit_data_audit = _explicit_data_audit(query)
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
    required_tool_groups: list[set[str]] = []
    if scope == "local":
        required_tool_groups.append({"local_hybrid_search"})
    elif scope != "web" and _explicit_local_tool_request(query):
        # A direct English instruction to call local_hybrid_search is an
        # executable requirement, even when the query omits the word "citrus".
        required_tool_groups.append({"local_hybrid_search"})
    elif scope in {"web", "fusion"}:
        required_tool_groups.append({"deepseek_search", "deepseek_fetch", "deepseek_fetch_paper"})
    elif scope == "auto" and ((explicit_literature and not paper_only) or explicit_reference_check):
        required_tool_groups.append({"deepseek_search"})
    local_literature_candidate = (
        explicit_literature and not paper_only and not explicit_reference_check
        and _local_corpus_matches(query))
    if scope == "fusion" and local_literature_candidate:
        required_tool_groups.append({"local_hybrid_search"})
    elif scope == "auto" and local_literature_candidate:
        # Local corpus is a complementary candidate source for explicit literature requests.
        required_tool_groups.append({"local_hybrid_search"})
    if explicit_reference_check and scope != "local":
        required_tool_groups.append({"verify_uploaded_references"})
    if explicit_data_audit and has_table_attachments:
        required_tool_groups.append({"audit_uploaded_data"})
    tools, _ = _scoped_toolset(
        scope, query=query, has_table=has_table_attachments,
        has_paper_attachment=has_paper_attachment,
        has_account_files=bool(state.get("account_files")),
        visualization_enabled=visualization_enabled)
    available_names = {tool.name for tool in tools}
    required_tool_groups = [group for group in required_tool_groups
                            if group & available_names]
    disease_image_query = bool(image_attachments and re.search(
        r"病害|病症|什么病|识别.{0,6}病|诊断|症状|黄龙病|溃疡病|黑斑病|病斑|disease|diagnos|symptom",
        query, re.I))
    main_model_can_see_images = bool(image_attachments) and _main_model_supports_image_input()
    tools = sorted(tools, key=lambda tool: str(getattr(tool, "name", "")))
    tools_by_name = {tool.name: tool for tool in tools}
    system_prompt = assemble_system_prompt(mode="expert", format_hint=state.get("format_hint"),
                                           query=query)
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
    scope_guidance = {
        "local": "用户明确限定仅使用本地语料；不要调用联网工具。",
        "web": "用户明确限定仅使用联网来源；不要调用本地语料。",
        "fusion": "用户要求融合检索；联网优先，并且只有本地语料目录覆盖问题时才调用本地检索。",
        "auto": "按系统提示中的来源规则选择工具；简单稳定问题直接回答。",
    }.get(scope, "按系统提示中的来源规则选择工具。")
    request_policy = (
        "\n\n<request_policy>\n" + scope_guidance + "\n"
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
        timeout=min(120, max(5, settings.AGENT_REQUEST_TIMEOUT_SEC)))
    llm_tools = llm_base.bind_tools(tools) if tools else llm_base
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
    seen_call_signatures: set[str] = set()

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
        if tool_turns >= _MAX_MODEL_TOOL_TURNS:
            stop_reason = "tool_turn_limit"
            break
        final_reserve = _final_reserve_sec()
        remaining = deadline - time.monotonic()
        if remaining <= final_reserve:
            stop_reason = "request_deadline"
            break
        try:
            emit_status("agent_progress", message="正在分析当前问题与已有证据…")
            # S2: collect this round's public text. It is classified only after
            # the round ends (tool calls vs final answer), never streamed raw.
            _round_text_chunks: list[str] = []
            # A1: 边生成边推送（节流 ~60ms）。此前增量只收集、等轮次结束才
            # 一次性 emit_text，于是模型生成 19 秒期间 SSE 只有 5 秒一次心跳，
            # 前端表现为"请求无响应"。这里把 content 增量按 text 事件发出；
            # 若该轮实际是工具轮，轮末用 text_reset 作废，不污染回答。
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
                emit_text(chunk)

            def _on_text_delta(chunk: str) -> None:
                _round_text_chunks.append(chunk)
                _stream_state["pending"].append(chunk)
                _flush_text()

            response = await _call_llm(
                llm_tools, messages, timeout=min(remaining - final_reserve, 120),
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
        if (_finish in ("length", "max_tokens", "max_output_tokens")
                and not (getattr(response, "tool_calls", None) or [])
                and not str(getattr(response, "content", "") or "").strip()
                and budget_retry_attempts < 1):
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
            messages.extend([
                AIMessage(content="[上一轮输出在长度上限处被截断，未形成有效内容。]"),
                HumanMessage(content=(
                    "上一轮回答因输出长度上限被截断。请更精简地继续：需要绘图时调用 draw_svg "
                    "并提交紧凑 SVG；否则直接给出完整但简短的答案。")),
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
                logger.error("Blocked DSML tool-call text from final answer")
                emit_text_reset("invalid_protocol")
                answer = "本轮模型返回了未执行的工具调用格式，无法据此形成可靠回答。请重试。"
                emit_text(answer)
            else:
                # A1: 增量已在生成过程中推完，这里只补尾包；一个增量都没发过
                # （例如非流式回退）才整体发一次，避免重复上屏。
                _flush_text(True)
                if not _stream_state["sent"]:
                    emit_text(answer)
            break

        # S2: this round ended in tool calls, so its public text is a working
        # note for the timeline. Text of a final (no-tool) round is the answer
        # and must never be duplicated into the trace.
        # A1: 工具轮里已上屏的临时文本必须作废，否则协议文本会留在回答气泡里。
        emit_text_reset("tool_round")
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
        remaining = max(0.0, deadline - time.monotonic() - 1.0)
        if remaining > 1:
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
                    llm_base, final_messages, timeout=remaining)
                emit_usage_from_response(session_id, "general_agent", final_response)
                final_text = str(final_response.content or "")
                if _DSML_PROTOCOL_RE.search(final_text):
                    logger.error("Blocked DSML tool-call text from final synthesis")
                    answer = "本轮模型返回了未执行的工具调用格式，无法据此形成可靠回答。请重试。"
                else:
                    answer = final_text
                if answer:
                    if answer == final_text:
                        messages.append(final_response)
                    emit_text(answer)
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
                answer = notice("tool_turn_limit", limit=_MAX_MODEL_TOOL_TURNS)
            elif tool_turns:
                answer = notice("no_evidence", detail="请换个更具体的问法，或指定数据来源。")
            else:
                answer = notice("output_truncated", budget=completion_budget)
    missing_groups = [group for group in required_tool_groups
                      if not group.intersection(tool_names_called)]
    # A required tool that ran but failed / produced no evidence is NOT a
    # completed research step.  Distinguish the two, so the answer never
    # claims success on the strength of a mere invocation.
    _OK_STATUSES = {"complete", "partial", "no_results"}
    failed_required: list[str] = []
    if required_tool_groups and tool_status_records:
        for group in required_tool_groups:
            if not group.intersection(tool_names_called):
                continue          # already covered by missing_groups
            statuses = [r["status"] for r in tool_status_records
                        if r["name"] in group]
            if statuses and not any(s in _OK_STATUSES for s in statuses):
                names = sorted({n for n in group if n in
                                {r["name"] for r in tool_status_records}})
                detail = "; ".join(
                    f"{r['name']}={r['status']}"
                    + (f"({r['reason']})" if r.get("reason") else "")
                    for r in tool_status_records if r["name"] in group)
                failed_required.append(f"{' / '.join(names)} [{detail}]")
    if failed_required and answer and not is_incomplete(answer):
        answer = (notice("required_steps_failed",
                         names="; ".join(failed_required),
                         detail="调用本身不等于完成。")
                  + "\n\n" + answer)
    if missing_groups and answer and not is_incomplete(answer):
        missing_names = [" / ".join(sorted(group)) for group in missing_groups]
        answer = notice("required_steps_missing",
                        names="; ".join(missing_names)) + "\n\n" + answer

    if answer and "[[FIGURE:" in answer:
        answer = figures.expand(answer)
    elapsed = (time.monotonic() - started) * 1000
    logger.info("General Agent finished: %d chars, %d tool types, %.0fms, stop=%s",
                len(answer), len(tool_names_called), elapsed, stop_reason)
    try:
        emit_status(
            "agent_progress",
            message=(f"检索摘要：来源模式 {scope}；工具暴露策略 {tool_policy.reason}；"
                     f"{'本轮按要求不检索' if retrieval_disabled else '检索工具已绑定，由模型决定是否调用'}。"),
        )
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
        "turn_trace": _safe_persisted_trace(messages[trace_start:]),
        "_trace": {"node": "supervisor", "elapsed_ms": elapsed,
                   "summary": f"{len(answer)} chars, {len(tool_names_called)} tool types, {stop_reason}",
                   "tool_turns": tool_turns,
                   "source_scope": scope,
                   "required_tools_unattempted": [sorted(group) for group in missing_groups],
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

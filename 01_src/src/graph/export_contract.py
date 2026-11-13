"""Deliverable contract: what file did the user ask for, and is it satisfied?

The General Agent must create a downloadable file when (and only when) the user
explicitly asks for one, and must never claim a delivered file that the artifact
registry cannot corroborate. That policy is text-in/data-out, so it lives here
instead of inside the agent graph:

* :func:`deliverable_export_requested` — is this a direct, non-negated request?
* :func:`deliverable_contract` — which (kind, format) pairs must be produced?
* :func:`deliverable_tool_groups` — which registered tools can produce them?
* :func:`artifact_matches_requirement` /
  :func:`deliverable_match_map` — one verified artifact per requirement.
* :func:`forced_export_plan` — the registered exporter (and arguments) for an
  outstanding requirement, or an explicit reason why none exists.

Nothing here executes a tool, emits progress, or touches configuration, which
keeps the rules unit-testable and keeps the graph module focused on execution.
"""
from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import ToolMessage

from src.graph.intent_text import (
    intent_clauses,
    intent_with_negation,
    mask_literal_payload,
    negated_before,
)

_EXPORT_FORMAT_RE = re.compile(
    r"(?i)(?<![a-z])(?:html?|xlsx|xlsm?|excel|word|docx|pdf|csv|tsv|ris|bibtex|svg|zip|tar\.gz|"
    r"report|brief|document|file|workbook|spreadsheet|figure|chart|diagram|table|sheet|"
    r"bibliography|archive|报告|简报|文档|文件|表格|清单|核验表|审查表|标注|书目|压缩包)(?![a-z])")
# Longer forms first: "另存为" must not leave "为可直接下载的 XLSX" as the tail.
_EXPORT_ACTION_ALT = (
    "整理成", "保存为", "另存为", "存为", "写成", "撰写", "编写", "制作", "绘制",
    "做成", "形成", "交付", "提供", "导出", "生成", "下载", "输出", "另存",
    "标出", "标记", "画", "写",
    "export", "download", "create", "make", "write", "prepare", "produce",
    "deliver", "save", "draw", "plot",
)
_EXPORT_ACTION_RE = re.compile(
    "(?i)(" + "|".join(_EXPORT_ACTION_ALT) + ")")
_EXPORT_DISCUSSION_RE = re.compile(
    r"(?i)(是什么|什么是|什么意思|含义|如何理解|怎么理解|怎么用|如何使用|"
    r"(?:如何|怎么|怎样)(?:去|来)?\s*(?:导出|生成|下载|输出|整理成|保存为|制作|绘制|画)|"
    r"使用方法|用法|功能|步骤|教程|技巧|how\s+(?:do|can|to)\b|"
    r"what\s+(?:is|does)\b|meaning|tutorial|guide)")
_EXPORT_DIRECT_COMMAND_RE = re.compile(
    r"(?i)(?:请你?|麻烦|帮我|为我|我需要|我要|请给我|please|can you|could you)"
    r"(?!\s*(?:问|解释|说明|讲解|分析|如何|怎么|what|how|explain|describe|tell me))"
    r".{0,28}(?:导出|生成|下载|输出|整理成|保存为|存为|写成|撰写|编写|制作|"
    r"做成|形成|交付|提供|export|download|create|make|write|prepare|produce|deliver|save)")

_KIND_INTENTS = (
    ("annotation_table", re.compile(r"标注|annotation|spreadsheet audit", re.I)),
    ("verification_sheet", re.compile(
        r"核验表|(?:参考文献|文献|DOI).{0,8}(?:核查表|验证表)|reference verification sheet|doi check sheet", re.I)),
    ("bibliography", re.compile(
        r"文献证据|参考文献表|书目|bibliography|\bRIS\b|\bBibTeX\b", re.I)),
    ("supplement_archive", re.compile(
        r"(?:补充材料|补充数据|supplement(?:ary)? data).{0,16}(?:压缩包|归档|archive|zip)", re.I)),
    ("supplement_report", re.compile(
        r"(?:补充材料|supplement(?:ary)? data).{0,16}(?:报告|审查|review|report)", re.I)),
    ("gap_csv", re.compile(r"语料缺口|coverage gap|gap report", re.I)),
    ("figure", re.compile(r"流程图|示意图|图表|绘图|SVG|figure|chart|diagram", re.I)),
    ("report_html", re.compile(
        r"报告|简报|report|brief|文档|document|实验方案|实验设计", re.I)),
)
_KIND_FORMATS = {
    "annotation_table": {"xlsx"},
    "verification_sheet": {"xlsx"},
    "bibliography": {"xlsx", "ris", "bibtex"},
    "supplement_archive": {"tar.gz"},
    "supplement_report": {"xlsx"},
    "gap_csv": {"csv"},
    "figure": {"svg"},
    "report_html": {"html"},
    "report_docx": {"docx"},
    "table_file": {"csv", "tsv", "xlsx"},
}
_FORMAT_ALIASES = {
    "html": "html", "htm": "html", "xlsx": "xlsx", "xls": "xls",
    "xlsm": "xlsm", "excel": "xlsx", "workbook": "xlsx",
    "spreadsheet": "xlsx", "word": "docx", "docx": "docx", "pdf": "pdf",
    "csv": "csv", "tsv": "tsv", "ris": "ris", "bibtex": "bibtex",
    "svg": "svg", "zip": "zip", "tar.gz": "tar.gz",
}
_FORMAT_TOKEN_RE = re.compile(
    r"(?i)(?<![a-z])(?:html?|xlsx|xlsm?|excel|workbook|spreadsheet|word|docx|pdf|csv|tsv|ris|bibtex|svg|zip|tar\.gz)(?![a-z])")
_DELIVERABLE_SPLIT_RE = re.compile(r"(?i)\s+(?:and|&|plus|also)\s+|以及|和|与|并且|同时|、")
_CHINESE_DIGITS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9,
}
_ENGLISH_NUMBERS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
# A quantity belongs to a *product*, not to an input table ("这三张表") or an
# adjective ("五维对照"). A Chinese numeral must be followed by a measure word;
# an Arabic/English numeral may stand directly before the product noun. The
# modifier gap is bounded and never crosses a sentence boundary.
_QUANTITY_NOUNS = (
    r"(?:报告|简报|文档|文件|工作簿|表格|清单|核验表|审查表"
    r"|reports?\b|documents?\b|files?\b|workbooks?\b|briefs?\b|tables?\b)")
_QUANTITY_RE = re.compile(
    r"(?i)(?:"
    r"(?P<cjk>[一二两三四五六七八九十]+)\s*(?:份|篇|套|个|张)\s*"
    r"[^。；;\n]{0,30}?" + _QUANTITY_NOUNS +
    r"|"
    r"(?P<latin>\d+|two|three|four|five|six|seven|eight|nine|ten)\s*"
    r"(?:(?:份|篇|套|个|张)\s*)?"
    r"(?:[A-Za-z0-9._+\-]{1,12}\s*){0,4}?" + _QUANTITY_NOUNS +
    r")")
_MULTI_PRODUCT_RE = re.compile(
    r"(?i)分别|各自|面向|针对|另一份|第[二三四五六]份|respectively|"
    r"separate\b|for\s+(?:finance|sales|operations|customers?|research|development)\b")
_SEPARATE_PRODUCT_RE = re.compile(
    r"(?i)分别|各自|另一份|第[二三四五六]份|不同的|不同内容|"
    r"respectively|separate\b|distinct\b|different\b|"
    r"for\s+\w+\s+and\b")
_SAME_BODY_RE = re.compile(
    r"(?i)内容相同|同一份正文|同一正文|同样的内容|同样的正文|"
    r"same\s+(?:content|body|text)|identical\s+(?:content|body|text)")
# Only nouns that name a *deliverable*. Bare data words ("table", "sheet",
# "list", "file", "dataset") are deliberately excluded: a research prompt says
# "Table S1" or "this sheet" when it names a real worksheet, and treating that
# as a request for a file invented an unsatisfiable requirement.
_DELIVERABLE_OUTPUT_NOUN_RE = re.compile(
    r"(?i)\b(?:report|brief|workbook|spreadsheet|figure|chart|diagram|"
    r"bibliography|archive|checklist)\b|"
    r"报告|简报|文档|文件|表格|清单|核验表|审查表|标注|书目|压缩包|流程图|图表|示意图")
_FORMAT_EXTENSIONS = {
    "html": {".html", ".htm"}, "xlsx": {".xlsx"}, "xls": {".xls"},
    "xlsm": {".xlsm"}, "docx": {".docx"}, "pdf": {".pdf"},
    "csv": {".csv"}, "tsv": {".tsv"}, "ris": {".ris"},
    "bibtex": {".bib", ".bibtex"}, "svg": {".svg"}, "zip": {".zip"},
    "tar.gz": {".tar.gz", ".tgz"},
}
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s<>\[\]{}\"']+", re.I)


def _retrieval_tools() -> frozenset[str]:
    """Source-observation tools are derived from the declarative catalog."""
    from src.core.tool_catalog import capability_groups

    groups = capability_groups()
    return groups["retrieval"] | groups["route_web"] | groups["route_local"]


# Compatibility alias for existing callers.
_RETRIEVAL_TOOLS = _retrieval_tools()


def deliverable_segment_has_output(text: str) -> bool:
    return (_DELIVERABLE_OUTPUT_NOUN_RE.search(text) is not None
            or _FORMAT_TOKEN_RE.search(text) is not None
            or any(pattern.search(text) for _kind, pattern in _KIND_INTENTS))


def deliverable_segments(clause: str) -> list[str]:
    """Split only conjunctions that join two output nouns/types.

    A product boundary needs a deliverable on both sides. The second item may
    omit its action verb ("一份 HTML 营养报告"); its quantifier or the active
    product list is enough for :func:`_output_tail` to keep it as a candidate.
    """
    pieces = [piece.strip() for piece in _DELIVERABLE_SPLIT_RE.split(clause) if piece.strip()]
    segments: list[str] = []
    for piece in pieces:
        if segments and deliverable_segment_has_output(segments[-1]) and \
                deliverable_segment_has_output(piece) and not re.match(r"(?i)(?:save\b|保存为|存为)", piece):
            segments.append(piece)
        elif segments:
            separator = "，" if re.search(r"[一-鿿]", piece) else " and "
            segments[-1] += separator + piece
        else:
            segments.append(piece)
    return segments


def _output_tail(clause: str) -> tuple[str, str]:
    """Return ``(tail, clause)`` for the first positive output action.

    The tail carries the *format* ("另存为可直接下载的 XLSX" → xlsx); the whole
    clause carries the *kind* ("把这份工作簿标注后另存为…" → annotation_table).
    Judging the kind on the tail alone dropped 标注/标记 and mis-filed an
    annotated workbook as an ordinary table.

    A product list may also lead with its quantifier ("分别生成一份…和一份…"
    or "三份 HTML 报告，分别面向…"). In that case the clause itself remains the
    candidate tail: identity and quantity are decided structurally, not from
    the substring after a verb.
    """
    if (_EXPORT_DISCUSSION_RE.search(clause)
            and not _EXPORT_DIRECT_COMMAND_RE.search(clause)):
        return "", clause
    for match in _EXPORT_ACTION_RE.finditer(clause):
        if negated_before(clause, match.start()):
            continue
        # "导出链路 / 生成流程 / 导出功能" name an operation, not a command.
        if _ACTION_MENTION_SUFFIX_RE.match(clause[match.end():]):
            continue
        # Uploaded/provided inputs are provenance, not requested products.
        if re.match(r"\s*的", clause[match.end():]):
            continue
        tail = clause[match.end():].strip()
        if not (_FORMAT_TOKEN_RE.search(tail) or deliverable_segment_has_output(tail)):
            # A leading quantifier or separable product noun belongs to the
            # same request; do not discard it merely because the verb comes first.
            tail = clause.strip()
        return tail, clause
    if _QUANTITY_RE.search(clause) or _MULTI_PRODUCT_RE.search(clause):
        return clause.strip(), clause
    return "", clause


# A product list may open with an adverb ("分别生成…") before the request verb.
# That is still a request; only the continuation that names no action is the
# product that needs its own body.
_LEADING_REQUEST_RE = re.compile(
    r"(?i)^(?:请|请你|帮我|麻烦|分别|各自|另外|再|同时|please)?\s*"
    r"(?:生成|导出|输出|制作|撰写|编写|创建|整理成|保存为|另存为|存为|写成|"
    r"做成|形成|交付|提供|下载|create|generate|export|prepare|write|make|"
    r"produce|deliver|save|download)")


# A sentence that only constrains how the answer must be written ("所有结论必须能
# 追溯到具体文献中的句子或表格") names a table it must cite, not a file to produce.
_CONSTRAINT_CLAUSE_RE = re.compile(
    r"(?i)必须|不得|不能|不可以|须(?:能|要|在)|追溯到|指到|指向|逐项指出|逐行指出|"
    r"注明|注明出处|must\b|shall\b|cannot\b|may\s+not\b")
# "把复核清单…写进去" continues the report named in the previous sentence; it is
# not a second downloadable product.
_CONTINUATION_TARGET_RE = re.compile(r"(?:进去|里面|其中|其上|该(?:报告|文档|文件)|it\b|there\b)", re.I)
# "导出链路/生成流程" are nouns built on an action word; the verb form is a
# command, the noun form is a description. A clause may mention one without
# asking for a file.
_ACTION_MENTION_SUFFIX_RE = re.compile(
    r"^\s*(?:链路|流程|功能|能力|工具|模块|接口|服务|过程|方式|方法|步骤|环节|"
    r"逻辑|机制|系统|代码|脚本|设置|选项|按钮|菜单)")
# "标题：《…》" / "文件名：…" labels the requested product; it is metadata,
# not a second request (the product still has to be named by a real clause).
_LABEL_CLAUSE_RE = re.compile(
    r"(?i)^\s*(?:文档|文件|报告)?(?:标题|题目|文件名|文件名称|文档名|名称|"
    r"section\s+title|title|filename|file\s+name)\s*[:：]")
# "使用 Word 文档工具完成" says which tool to use, not which file to produce.
_TOOL_USAGE_CLAUSE_RE = re.compile(
    r"(?i)(?:使用|利用|调用|通过|借助)\s*[^。；;\n]{0,24}(?:工具|tool|插件|接口|函数)")


def _has_positive_action(clause: str) -> bool:
    """True only for a real, non-negated command verb (not a noun mention)."""
    for match in _EXPORT_ACTION_RE.finditer(clause):
        if negated_before(clause, match.start()):
            continue
        if _ACTION_MENTION_SUFFIX_RE.match(clause[match.end():]):
            continue
        if re.match(r"\s*的", clause[match.end():]):
            continue
        return True
    return False


def _is_constraint_clause(clause: str) -> bool:
    """True when the clause states an obligation rather than requesting a file."""
    if not _CONSTRAINT_CLAUSE_RE.search(clause):
        return False
    # A clause with its own export verb is a real request even when it also
    # carries an obligation ("请导出 XLSX，问题必须指到行列").
    return not _has_positive_action(clause)


def deliverable_contract(query: str) -> list[dict[str, Any]]:
    """Positive output objects only, with stable identity for each product.

    Format suffixes of input files and negated output clauses never become
    requirements. Conjoined outputs retain identity even when kinds coincide.

    Explicit carriers win: when the turn names a downloadable format anywhere,
    bare nouns ("清单", "结论") are answer content, not extra files. When no
    format is named at all, those nouns stay requirements — that is the only
    product the user asked for.
    """
    requirements: list[dict[str, Any]] = []
    active = False
    query_text = mask_literal_payload(str(query or ""))
    separate_products = bool(_SEPARATE_PRODUCT_RE.search(query_text))
    for clause_index, clause in enumerate(intent_clauses(query_text)):
        if _is_constraint_clause(clause):
            continue
        # Metadata ("标题：…") and tool-choice ("使用…工具") clauses describe
        # the already requested product; they never open a new requirement.
        if ((_LABEL_CLAUSE_RE.match(clause) or _TOOL_USAGE_CLAUSE_RE.search(clause))
                and not _has_positive_action(clause)):
            continue
        for segment_index, segment in enumerate(deliverable_segments(clause)):
            tail, kind_text = _output_tail(segment)
            has_action = _has_positive_action(segment)
            if not tail:
                if has_action or not active or re.search(
                        r"不要|无需|不用|请勿|禁止|读取|上传|附件|输入|原始|read\b|input\b", segment, re.I):
                    continue
                if not (_FORMAT_TOKEN_RE.search(segment) or deliverable_segment_has_output(segment)):
                    continue
                tail = kind_text = segment.strip()
            # Read the count before normalizing the label: the leading
            # quantifier is part of the request, not part of the product name.
            quantity = _quantity(tail)
            # A leading quantifier/adverb belongs to the product's identity.
            # Normalize only that prefix; action and modifier words in the rest
            # of the clause remain part of the label and kind detection.
            tail = re.sub(
                r"^(?:请|帮我|麻烦|分别|各自|另外|再|同时|please\s+)?"
                r"(?:[一二两三四五六七八九十]+|\d+)\s*(?:份|个|篇|张|套)?\s*",
                "", tail, flags=re.I).strip() or tail.strip()
            if not (_EXPORT_FORMAT_RE.search(tail) or deliverable_segment_has_output(tail)):
                continue
            active = True
            formats = list(dict.fromkeys(_FORMAT_ALIASES[m.group(0).casefold()]
                                         for m in _FORMAT_TOKEN_RE.finditer(tail)))
            kinds = list(dict.fromkeys(kind for kind, pattern in _KIND_INTENTS
                                       if pattern.search(kind_text)))
            if len(kinds) > 1 and "report_html" in kinds:
                kinds.remove("report_html")
            if kinds == ["report_html"] and not formats:
                kinds, formats = ["report_docx"], ["docx"]
            if kinds == ["report_html"] or (not kinds and "docx" in formats):
                kinds = (["report_docx"] if formats == ["docx"] else
                         ["report_html", "report_docx"] if "docx" in formats else ["report_html"])
            if not kinds and set(formats) & {"csv", "tsv", "xlsx"}:
                kinds = ["table_file"]
            if not kinds and not formats:
                kinds = ["generic_output"]
            normalized = _normalize_requirement({"kinds": kinds, "formats": formats})
            normalized.update(product_id=f"p{clause_index}_{segment_index}", label=tail[:240])
            normalized["quantity"] = quantity
            normalized["body_source"] = "answer_or_tool_output"
            normalized["needs_clarification"] = False
            normalized["content_same"] = (
                True if _SAME_BODY_RE.search(query_text) else None)
            if _REPORT_KINDS.intersection(kinds):
                normalized["body_scope"] = report_body_scope(clause)
                if (separate_products and not _SAME_BODY_RE.search(query_text)
                        and not _LEADING_REQUEST_RE.search(segment)):
                    normalized["body_scope"] = "distinct"
                if normalized["body_scope"] == "distinct":
                    normalized["body_source"] = "product_specific"
            # "save it as HTML" is a format continuation, not a second report.
            if (requirements and re.match(r"(?i)(?:it\s+as|为|成|as\b)", tail)
                    and not deliverable_segment_has_output(tail)):
                requirements[-1]["formats"] = formats
                continue
            if (not formats and not _FORMAT_TOKEN_RE.search(tail)
                    and _CONTINUATION_TARGET_RE.search(segment)):
                # Content to write *into* an already requested file.
                continue
            requirements.append(normalized)
    return _expand_product_identity(
        _assign_report_body_ownership(
            _explicit_carrier_requirements(
                requirements, separate_products=separate_products),
            query_text),
        query_text)


def _chinese_number(token: str) -> int:
    """Parse 一…二十 (the range a request realistically uses) without guessing."""
    token = str(token or "").strip()
    if not token:
        return 1
    if token == "十":
        return 10
    if "十" in token:
        tens, _, ones = token.partition("十")
        return ((_CHINESE_DIGITS.get(tens, 1) if tens else 1) * 10
                + (_CHINESE_DIGITS.get(ones, 0) if ones else 0))
    return _CHINESE_DIGITS.get(token, 1)


def _quantity(text: str) -> int:
    """Read the count of *products*; a count of input tables is not a request."""
    matches = list(_QUANTITY_RE.finditer(str(text or "")))
    for match in matches:
        prefix = str(text or "")[max(0, match.start() - 20):match.start()]
        # "这三张表 / 上述两个工作表 / 附件里的…" count inputs, not outputs.
        if re.search(r"(?i)(?:这|那|以上|上述|附件|输入|上传)\s*$", prefix):
            continue
        token = str(match.group("cjk") or match.group("latin") or "").casefold()
        number = re.search(r"\d+", token)
        if number:
            return max(1, min(20, int(number.group(0))))
        if token in _ENGLISH_NUMBERS:
            return _ENGLISH_NUMBERS[token]
        return max(1, min(20, _chinese_number(token)))
    return 1


def _expand_product_identity(requirements: list[dict[str, Any]],
                             query: str) -> list[dict[str, Any]]:
    """Materialize an explicit count of same-format products without inventing bodies.

    Conjoined products already have separate identities. An explicit count on a
    single product ("三份 HTML 报告，分别面向…") creates three specs sharing a
    format/kind, each with its own stable ID. Independent audiences/content make
    later specs ``distinct``; a phrase saying the content is the same keeps one
    answer as the shared body. The delivery gate still refuses to copy that
    answer into independent products without their own body.

    The first spec stays ``shared`` so the final answer can be its body. It is
    never copied into the later specs; those either come from the model or are
    reported as missing product content.
    """
    if len(requirements) != 1:
        return requirements
    requirement = requirements[0]
    count = int(requirement.get("quantity") or 1)
    if count <= 1:
        return requirements
    same_body = bool(_SAME_BODY_RE.search(query))
    explicit_split = bool(_MULTI_PRODUCT_RE.search(query)
                          or _SEPARATE_PRODUCT_RE.search(query))
    explicitly_distinct = bool(_DISTINCT_BODY_RE.search(query))
    base_id = str(requirement.get("product_id") or "p0_0")
    base_label = str(requirement.get("label") or "output")
    specs: list[dict[str, Any]] = []
    for index in range(count):
        spec = dict(requirement)
        spec["product_id"] = base_id if index == 0 else f"{base_id}_{index + 1}"
        spec["quantity"] = 1
        spec["content_same"] = same_body
        spec["label"] = f"{base_label} ({index + 1}/{count})"[:240]
        if same_body:
            spec["body_scope"] = "shared"
            spec["body_source"] = "answer_or_tool_output"
        elif explicitly_distinct:
            spec["body_scope"] = "distinct"
            spec["body_source"] = "product_specific"
        elif index == 0:
            spec["body_scope"] = "shared"
            spec["body_source"] = "answer_or_tool_output"
        else:
            spec["body_scope"] = "distinct"
            spec["body_source"] = "product_specific"
        # Without "respectively"/audiences/same-content, two products are
        # ambiguous. Keep the specs and say so instead of silently dropping one.
        spec["needs_clarification"] = bool(not same_body and not explicit_split)
        specs.append(spec)
    return specs


_REPORT_KINDS = frozenset({"report_html", "report_docx"})
# A product with its own audience or its own content ("把可以对外、不含敏感信息的
# 部分单独整理成一份 HTML 简报") needs its own body. One answer is not two.
# "另外写一份 HTML 报告，把复核清单写进去" is NOT distinct: it asks for the same
# analysis in another format, so the answer is its body.
_DISTINCT_BODY_RE = re.compile(
    r"(?i)对外|脱敏|不含敏感|公开版|外部版|内部版|redact|public(?:\s|-)facing|external\b|"
    r"(?:另外|单独|另|再)[^。；;]{0,24}?(?:部分|子集|摘要版|精简版|节选)|"
    r"另一份|第[二三四五六]份")


def report_body_scope(clause: str) -> str:
    """``distinct`` when a report clause asks for a body of its own, else ``shared``.

    The delivery gate may fill one report from the final answer. A second report
    that asks for different content must be written by the model itself, or the
    turn must honestly report it as outstanding.
    """
    text = str(clause or "")
    if not _REPORT_KINDS.intersection(
            kind for kind, pattern in _KIND_INTENTS if pattern.search(text)):
        return "shared"
    return "distinct" if _DISTINCT_BODY_RE.search(text) else "shared"


# Words that describe the *act* or the carrier, not the product's identity.
# CJK phrases are removed as substrings (there is no whitespace to tokenize on);
# ASCII words are filtered as whole tokens below, so "a" cannot mangle
# "finance" and "in" cannot mangle "analysis".
_IDENTITY_CJK_NOISE = (
    "可直接下载", "可下载", "直接下载", "整理成", "保存为", "另存为", "存为",
    "写成", "做成", "形成", "交付", "提供", "下载", "另存", "标出", "标记",
    "生成", "导出", "输出", "制作", "撰写", "编写", "创建", "帮我", "麻烦",
    "分别", "各自", "另外", "同时", "以及", "并且", "面向", "针对",
    "一份", "两份", "三份", "四份", "五份", "六份", "七份", "八份", "九份",
    "十份", "这个", "那个", "这些", "那些", "所有", "内容相同",
    "同一份正文", "同一正文", "同样的内容", "同样的正文", "报告书",
    "报告", "简报", "文档", "文件", "表格", "清单", "核验表", "审查表",
    "标注", "书目", "压缩包", "流程图", "图表", "示意图", "工作簿",
)
_IDENTITY_ASCII_NOISE = frozenset({
    "html", "htm", "xlsx", "xlsm", "xls", "excel", "word", "docx", "pdf",
    "csv", "tsv", "ris", "bibtex", "svg", "zip", "tar.gz",
    "reports", "report", "briefs", "brief", "documents", "document", "files",
    "file", "workbooks", "workbook", "spreadsheets", "spreadsheet", "tables",
    "table", "checklists", "checklist", "figures", "figure", "charts", "chart",
    "diagrams", "diagram", "bibliography", "archives", "archive",
    "create", "generate", "export", "prepare", "write", "make", "produce",
    "deliver", "save", "download", "please", "separate", "separately",
    "another", "same", "content", "body", "text", "identical",
    "a", "an", "the", "and", "or", "for", "of", "to", "with", "in", "on",
    "by", "as", "it", "this", "that", "these", "those",
})


def _product_identity(label: str) -> frozenset[str]:
    """The modifier/audience tokens that distinguish one product from another.

    "HTML 报告" and "HTML 简报" share a kind and a format but are two products
    (an internal report and a redacted public brief). "实验设计" and
    "实验设计报告" are the same product; restating it adds no new identity.
    """
    text = str(label or "").casefold()
    for noise in sorted(_IDENTITY_CJK_NOISE, key=len, reverse=True):
        text = text.replace(noise, " ")
    tokens = re.findall(r"[a-z0-9][a-z0-9._+-]*|[一-鿿]+", text)
    return frozenset(token for token in tokens
                     if len(token) > 1 and token not in _IDENTITY_ASCII_NOISE
                     and token not in {"第", "份", "个"})


def _product_nouns(label: str) -> frozenset[str]:
    """The deliverable nouns in a label (kept for compatibility and diagnostics)."""
    return frozenset(m.group(0).casefold()
                     for m in _DELIVERABLE_OUTPUT_NOUN_RE.finditer(str(label or "")))


def _is_bare_output(requirement: dict[str, Any]) -> bool:
    """A bare noun ("清单") is answer content when a concrete carrier exists."""
    return (tuple(requirement.get("kinds") or ()) == ("generic_output",)
            and not requirement.get("formats"))


def _explicit_carrier_requirements(
    requirements: list[dict[str, Any]],
    *,
    separate_products: bool = False,
) -> list[dict[str, Any]]:
    """Drop bare-noun requirements when an explicit carrier exists; merge restatements.

    A concrete no-format carrier ("参考文献表" → bibliography) is not a bare noun
    and survives. Consolidation lives in parsing only; ``deliverable_match_map``
    still gives every surviving requirement its own artifact identity.
    """
    if any(r.get("formats") for r in requirements):
        requirements = [r for r in requirements if not _is_bare_output(r)]
    merged: list[dict[str, Any]] = []
    for requirement in requirements:
        kinds = tuple(requirement.get("kinds") or ())
        formats = tuple(requirement.get("formats") or ())
        identity = _product_identity(requirement.get("label"))
        duplicate = next((kept for kept in merged
                          if tuple(kept.get("kinds") or ()) == kinds
                          and tuple(kept.get("formats") or ()) == formats
                          and kept.get("body_scope") != "distinct"
                          and requirement.get("body_scope") != "distinct"
                          and _product_identity(kept.get("label")) == identity), None)
        if duplicate is not None:
            label = str(requirement.get("label") or "")
            if label and label not in str(duplicate.get("label") or ""):
                duplicate["label"] = (str(duplicate.get("label") or "") + "；" + label)[:240]
            if requirement.get("body_scope") == "distinct":
                duplicate["body_scope"] = "distinct"
            if requirement.get("content_same"):
                duplicate["content_same"] = True
            continue
        merged.append(requirement)
    return merged


def _assign_report_body_ownership(
    requirements: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    """One answer can be the body of one report; different products need their own.

    Products that differ only in format (HTML report + DOCX report) may share the
    same body. Products with different identities ("病害报告" vs "营养报告",
    "finance" vs "operations") may not: the delivery gate must not copy one
    answer into both. An explicit "内容相同 / same content" keeps the shared body.
    """
    if _SAME_BODY_RE.search(str(query or "")):
        return requirements
    reports = [r for r in requirements
               if _REPORT_KINDS.intersection(r.get("kinds") or ())]
    if len(reports) <= 1:
        return requirements
    first_identity = _product_identity(reports[0].get("label"))
    for requirement in reports[1:]:
        if requirement.get("body_scope") == "distinct":
            continue
        if _product_identity(requirement.get("label")) != first_identity:
            requirement["body_scope"] = "distinct"
            requirement["body_source"] = "product_specific"
    return requirements


def _normalize_requirement(requirement: dict[str, list[str]]) -> dict[str, list[str]]:
    """Fill in the only allowed format of a single-kind requirement.

    "实验设计" and "实验设计报告" describe the same product; without this the two
    phrasings produced two requirements and the delivery gate exported the file
    twice.
    """
    kinds = list(requirement.get("kinds") or ())
    formats = list(requirement.get("formats") or ())
    if len(kinds) == 1:
        allowed = _KIND_FORMATS.get(kinds[0]) or set()
        if len(allowed) == 1 and not formats:
            formats = sorted(allowed)
    return {"kinds": kinds, "formats": formats}


def deliverable_export_requested(query: str) -> bool:
    """Detect an explicit, non-negated request to create a downloadable file."""
    return bool(deliverable_contract(query))


def artifact_matches_requirement(validation: dict[str, Any],
                                 requirement: dict[str, list[str]]) -> bool:
    if (not validation.get("valid")
            or validation.get("request_matches") is False):
        return False
    producer = str(validation.get("producer_tool") or "")
    required_kinds = set(requirement.get("kinds") or ())
    if required_kinds == {"generic_output"}:
        # "Just give me a file" — any verified artifact satisfies it.
        return bool(str(validation.get("filename") or "").strip())
    if required_kinds:
        from src.graph.tool_availability import declarations
        produced = set(declarations().get(producer, ((), (), ""))[1])
        if not (produced & required_kinds):
            return False
    required_formats = set(requirement.get("formats") or ())
    if required_formats:
        filename = str(validation.get("filename") or "").casefold()
        if not any(filename.endswith(ext) for fmt in required_formats
                   for ext in _FORMAT_EXTENSIONS.get(fmt, set())):
            return False
    return True


def deliverable_match_map(
    validations: list[dict[str, Any]],
    requirements: list[dict[str, list[str]]],
) -> dict[int, int]:
    """Bipartite matching: one registry identity per requested product.

    Return original validation indices, including after augmenting-path remaps.
    Product consolidation belongs to parsing, never to this matching layer.
    """
    unique = []
    seen = set()
    for original_index, validation in enumerate(validations or ()):
        if (not isinstance(validation, dict) or not validation.get("valid")
                or validation.get("request_matches") is False):
            continue
        identity = validation.get("artifact_id") or validation.get("link")
        # Production validations always carry an artifact ID. A synthetic test
        # may omit it; a filename alone cannot establish file identity.
        identity = identity or f"validation:{original_index}"
        if identity not in seen:
            seen.add(identity)
            unique.append((original_index, validation))
    assigned = {}

    def assign(requirement_index, visited):
        for artifact_index, (_original, validation) in enumerate(unique):
            if artifact_index in visited or not artifact_matches_requirement(validation, requirements[requirement_index]):
                continue
            visited.add(artifact_index)
            previous = assigned.get(artifact_index)
            if previous is None or assign(previous, visited):
                assigned[artifact_index] = requirement_index
                return True
        return False

    for index in sorted(range(len(requirements)), key=lambda i: sum(
            artifact_matches_requirement(v, requirements[i]) for _, v in unique)):
        assign(index, set())
    return {requirement: unique[artifact][0] for artifact, requirement in assigned.items()}


def deliverables_satisfied(validations: list[dict[str, Any]],
                           requirements: list[dict[str, list[str]]]) -> bool:
    return bool(requirements) and len(
        deliverable_match_map(validations, requirements)) == len(requirements)


def deliverable_tool_groups(
    requirements: list[dict[str, list[str]]],
) -> list[set[str]]:
    """Map each requested artifact contract to its declared producer tools."""
    from src.graph.tool_availability import declarations

    declared = declarations()
    groups: list[set[str]] = []
    for requirement in requirements:
        kinds = set(requirement.get("kinds") or ())
        formats = set(requirement.get("formats") or ())
        if not kinds:
            kinds = ({kind for kind, allowed in _KIND_FORMATS.items()
                      if not formats or formats & allowed})
        names = set()
        for name, (_requires, produces, _authorization) in declared.items():
            for kind in set(produces) & kinds:
                allowed_formats = _KIND_FORMATS.get(kind, set())
                if formats and not (formats & allowed_formats):
                    continue
                names.add(name)
        if names:
            groups.append(names)
    return groups


def svg_from_answer(text: str) -> str:
    """Return the SVG source the answer already contains, never synthesize one."""
    fenced = re.search(
        r"```(?:svg|xml)?\s*(<svg\b[\s\S]*?</svg>)\s*```", str(text or ""), re.I)
    if fenced:
        return fenced.group(1).strip()
    raw = re.search(r"<svg\b[\s\S]*?</svg>", str(text or ""), re.I)
    return raw.group(0).strip() if raw else ""


def observed_gap_rows(query: str, messages) -> list[dict[str, str]]:
    """Build gap rows only from retrieval failures already observed this turn."""
    rows: list[dict[str, str]] = []
    for message in messages or ():
        if not isinstance(message, ToolMessage):
            continue
        name = str(getattr(message, "name", "") or "")
        if name not in _retrieval_tools():
            continue
        artifact = getattr(message, "artifact", None) or {}
        if not isinstance(artifact, dict):
            artifact = {}
        errors = artifact.get("errors")
        if isinstance(errors, dict) and errors:
            for source, failure in errors.items():
                rows.append({
                    "topic": str(query or "")[:400],
                    "doi_or_title": "",
                    "failure": f"{source}: {str(failure)[:400]}",
                    "local_result": name,
                    "source_url": "",
                })
            continue
        execution = artifact.get("_citrus_execution")
        status = str(execution.get("status") or "") if isinstance(execution, dict) else ""
        reason = str(execution.get("reason_code") or "") if isinstance(execution, dict) else ""
        if status in {"failed", "partial", "no_results"}:
            rows.append({
                "topic": str(query or "")[:400],
                "doi_or_title": "",
                "failure": reason or status,
                "local_result": name,
                "source_url": "",
            })
    return rows


def markdown_table(answer: str):
    """Use one complete table already written in the answer, without guessing."""
    lines = str(answer or "").splitlines()
    for index in range(len(lines) - 2):
        if "|" not in lines[index] or not re.fullmatch(r"[\s|:\-]+", lines[index + 1]):
            continue
        columns = [c.strip() for c in lines[index].strip().strip("|").split("|")]
        rows = []
        for line in lines[index + 2:]:
            if "|" not in line or not line.strip():
                break
            row = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(row) != len(columns):
                return None
            rows.append(row)
        if rows and all(columns) and len(set(columns)) == len(columns):
            return columns, rows
    return None


def literature_rows(messages):
    rows = []
    seen = set()
    for message in messages or ():
        if not isinstance(message, ToolMessage) or message.name != "search_literature":
            continue
        data = getattr(message, "artifact", None) or {}
        if "candidates" not in data:
            try:
                data = json.loads(str(message.content))
            except (ValueError, TypeError):
                continue
        if not isinstance(data, dict):
            continue
        for row in data.get("candidates", []):
            if not isinstance(row, dict):
                continue
            key = str(row.get("doi") or row.get("url") or row.get("title") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append({**row, "citation": row.get("title") or key,
                         "evidence": "检索候选；尚未核验科学结论支持关系。"})
    return rows


def forced_export_plan(requirement: dict[str, list[str]], query: str, answer: str,
                       messages, tool_by_name: dict) -> tuple[str, dict[str, Any], str]:
    """Choose a registered exporter only when the requested format is supported."""
    kinds = set(requirement.get("kinds") or ())
    formats = set(requirement.get("formats") or ())
    label = str(requirement.get("label") or query)
    title = forced_export_title(label, limit=60)
    if "report_docx" in kinds and (not formats or formats == {"docx"}):
        return "", {}, "incremental_word_required"
    if "table_file" in kinds or (not kinds and formats & {"csv", "tsv", "xlsx"}):
        table = markdown_table(answer)
        if not table:
            return "", {}, "missing_tabular_data"
        fmt = next((f for f in ("csv", "tsv", "xlsx") if f in formats), "xlsx")
        return "export_table", {"title": title, "columns_json": table[0], "rows_json": table[1], "output_format": fmt}, ""
    if not kinds and not formats:
        return "export_paper_analysis", {"title": title, "analysis_markdown": answer}, ""
    if kinds == {"generic_output"}:
        return "export_paper_analysis", {"title": title, "analysis_markdown": answer}, ""
    if "report_html" in kinds and (not formats or "html" in formats):
        if re.search(r"实验设计|实验方案|experiment design", label, re.I):
            return "export_experiment_design", {"title": title, "design_markdown": answer}, ""
        return "export_paper_analysis", {"title": title, "analysis_markdown": answer}, ""
    if "annotation_table" in kinds and (not formats or formats & {"xlsx"}):
        return "audit_uploaded_data", {}, ""
    if "verification_sheet" in kinds and (not formats or formats & {"xlsx"}):
        return "verify_uploaded_references", {"file_index": 0}, ""
    if "bibliography" in kinds and (not formats or formats & {"xlsx", "ris", "bibtex"}):
        fmt = next((f for f in ("xlsx", "ris", "bibtex") if f in formats), "xlsx")
        if any(isinstance(m, ToolMessage) and m.name == "verify_uploaded_references" for m in messages or ()):
            return "export_literature_evidence", {"title": title, "source": "last_verification", "output_format": fmt}, ""
        rows = literature_rows(messages)
        if not rows:
            return "", {}, "missing_bibliography_evidence"
        return "export_literature_evidence", {"title": title, "evidence_rows_json": rows, "output_format": fmt}, ""
    if kinds & {"supplement_archive", "supplement_report"}:
        supported = {"tar.gz"} if "supplement_archive" in kinds else {"xlsx"}
        if formats and not (formats & supported):
            return "", {}, "unsupported_format"
        dois = list(dict.fromkeys(
            match.group(0).rstrip(".,;:)")
            for match in _DOI_RE.finditer(f"{query} {answer}")))
        if not dois:
            return "", {}, "missing_doi"
        return "fetch_and_audit_open_supplements", {"doi": dois[0]}, ""
    if "gap_csv" in kinds and (not formats or formats & {"csv"}):
        rows = observed_gap_rows(query, messages)
        if not rows:
            return "", {}, "no_observed_gap_evidence"
        return "export_corpus_gap_report", {
            "title": title, "gaps_json": json.dumps(rows, ensure_ascii=False),
        }, ""
    if "figure" in kinds and (not formats or formats & {"svg"}):
        svg = svg_from_answer(answer)
        if not svg:
            return "", {}, "no_svg_source_in_answer"
        return "draw_svg", {"svg": svg, "alt_text": str(query or "requested figure")[:240]}, ""
    return "", {}, "unsupported_or_ambiguous_contract"


def forced_export_title(query: str, limit: int = 18) -> str:
    text = re.sub(r"\s+", " ", str(query or "")).strip()
    text = re.sub(r"^(请|帮我|麻烦|请你|能否|能不能)+", "", text)
    text = text[:limit].strip(" ,.:;")
    return (text or "research-output") + "_report"

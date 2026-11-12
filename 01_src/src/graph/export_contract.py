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
    negated_before,
)

_EXPORT_FORMAT_RE = re.compile(
    r"(?i)(?<![a-z])(?:html?|xlsx|xlsm?|excel|word|docx|pdf|csv|tsv|ris|bibtex|svg|zip|tar\.gz|"
    r"report|brief|document|file|workbook|spreadsheet|figure|chart|diagram|table|sheet|"
    r"bibliography|archive|报告|简报|文档|文件|表格|清单|核验表|审查表|标注|书目|压缩包)(?![a-z])")
_EXPORT_ACTION_RE = re.compile(
    r"(?i)(导出|生成|下载|输出|整理成|保存为|存为|写成|撰写|编写|制作|绘制|画|"
    r"做成|形成|交付|提供|export|download|create|make|write|prepare|produce|deliver|save|draw|plot)")
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
_DELIVERABLE_SPLIT_RE = re.compile(r"(?i)\s+(?:and|&|plus|also)\s+|以及|和|与|并且|同时")
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
_RETRIEVAL_TOOLS = frozenset({
    "local_hybrid_search", "search_literature", "deepseek_search",
    "deepseek_fetch", "deepseek_fetch_paper", "fetch_and_audit_open_supplements",
})


def deliverable_segment_has_output(text: str) -> bool:
    return (_DELIVERABLE_OUTPUT_NOUN_RE.search(text) is not None
            or _FORMAT_TOKEN_RE.search(text) is not None
            or any(pattern.search(text) for _kind, pattern in _KIND_INTENTS))


def deliverable_segments(clause: str) -> list[str]:
    """Split only conjunctions that join two output nouns/types."""
    pieces = [piece.strip() for piece in _DELIVERABLE_SPLIT_RE.split(clause) if piece.strip()]
    segments: list[str] = []
    for piece in pieces:
        if segments and deliverable_segment_has_output(segments[-1]) and \
                deliverable_segment_has_output(piece) and not re.match(r"(?i)(?:save\b|保存为|存为)", piece):
            segments.append(piece)
        elif segments:
            segments[-1] += " and " + piece
        else:
            segments.append(piece)
    return segments


def _output_tail(clause: str) -> str:
    if (_EXPORT_DISCUSSION_RE.search(clause)
            and not _EXPORT_DIRECT_COMMAND_RE.search(clause)):
        return ""
    for match in _EXPORT_ACTION_RE.finditer(clause):
        if negated_before(clause, match.start()):
            continue
        # Uploaded/provided inputs are provenance, not requested products.
        if re.match(r"\s*的", clause[match.end():]):
            continue
        return clause[match.end():].strip()
    return ""


def deliverable_contract(query: str) -> list[dict[str, Any]]:
    """Positive output objects only, with stable identity for each product.

    Format suffixes of input files and negated output clauses never become
    requirements. Conjoined outputs retain identity even when kinds coincide.
    """
    requirements = []
    active = False
    for clause_index, clause in enumerate(intent_clauses(str(query or ""))):
        for segment_index, segment in enumerate(deliverable_segments(clause)):
            tail = _output_tail(segment)
            has_action = bool(_EXPORT_ACTION_RE.search(segment))
            if not tail:
                if has_action or not active or re.search(
                        r"不要|无需|不用|请勿|禁止|读取|上传|附件|输入|原始|read\b|input\b", segment, re.I):
                    continue
                if not (_FORMAT_TOKEN_RE.search(segment) or deliverable_segment_has_output(segment)):
                    continue
                tail = segment.strip()
            if not (_EXPORT_FORMAT_RE.search(tail) or deliverable_segment_has_output(tail)):
                continue
            active = True
            formats = list(dict.fromkeys(_FORMAT_ALIASES[m.group(0).casefold()]
                                         for m in _FORMAT_TOKEN_RE.finditer(tail)))
            kinds = list(dict.fromkeys(kind for kind, pattern in _KIND_INTENTS if pattern.search(tail)))
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
            # "save it as HTML" is a format continuation, not a second report.
            if (requirements and re.match(r"(?i)(?:it\s+as|为|成|as\b)", tail)
                    and not deliverable_segment_has_output(tail)):
                requirements[-1]["formats"] = formats
            else:
                requirements.append(normalized)
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
        if name not in _RETRIEVAL_TOOLS:
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

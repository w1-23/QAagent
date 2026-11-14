"""One model-facing delivery boundary; renderers and publication stay in tools.py.

Legacy names remain callable internally. This module does not register tools or
rewrite historical messages; callers may adapt newly executed calls explicitly.
"""
from __future__ import annotations

from contextlib import closing
import inspect
import json
import re
import sqlite3
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.core.tool_errors import ToolArgumentError

Kind = Literal["report", "table", "bibliography", "research_notes", "corpus_gap"]
Purpose = Literal["general", "paper_analysis", "experiment_design"]
Cell = str | int | float | bool | None


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Reference(StrictInput):
    citation: Cell = ""
    title: Cell = ""
    authors: str | list[str] | None = ""
    journal: Cell = ""
    year: Cell = ""
    volume: Cell = ""
    issue: Cell = ""
    pages: Cell = ""
    claim: Cell = ""
    evidence: Cell = ""
    source: Cell = ""
    doi: Cell = ""
    url: Cell = ""


class CorpusGap(StrictInput):
    topic: Cell = ""
    doi_or_title: Cell = ""
    failure: Cell = ""
    local_result: Cell = ""
    source_url: Cell = ""


class AnalysisReference(StrictInput):
    kind: Literal["analysis_result"] = "analysis_result"
    id: str
    version: int = 1
    sha256: str


# This dispatch contract enumerates the formats the existing renderers actually
# implement (not file-conversion aspirations). SVG is a report companion, never
# a replacement for HTML. Real-renderer tests exercise every listed format.
_FORMATS = {
    "report": ("html", "svg"),
    "table": ("csv", "tsv", "xlsx"),
    "bibliography": ("xlsx", "ris", "bibtex"),
    "research_notes": ("html", "svg"),
    "corpus_gap": ("csv",),
}
_DEFAULT_FORMAT = {kind: formats[0] for kind, formats in _FORMATS.items()}
LEGACY_DELIVERY_NAMES = (
    "export_paper_analysis", "export_experiment_design", "export_research_notes",
    "export_table", "export_literature_evidence", "export_corpus_gap_report",
)


def supported_delivery_formats(kind: str) -> tuple[str, ...]:
    """Return actual renderer formats; svg always accompanies an HTML report."""
    if kind not in _FORMATS:
        raise ToolArgumentError(f"不支持的成果类型：{kind}")
    return _FORMATS[kind]


class DeliveryInput(StrictInput):
    kind: Kind
    title: str = ""
    output_format: str = Field(default="", description="留空用类型默认格式；report/research_notes 的 svg 为 HTML 附件。")
    purpose: Purpose = "general"
    content: str = Field(default="", description="report 正文 Markdown；不补造研究内容。")
    svg_code: str = ""
    append: bool = False
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Cell] | dict[str, Cell]] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)
    source: Literal["", "last_verification"] = ""
    analysis_result_ref: str | AnalysisReference = ""
    result_section: Literal["outputs", "descriptive", "grouped", "test", "tests", "coverage", "exclusions", "design"] = "outputs"
    project_name: str = ""
    limit: int = 100
    gaps: list[CorpusGap] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_kind_fields(self):
        fmt = self.output_format.strip().lower() or _DEFAULT_FORMAT[self.kind]
        if fmt not in supported_delivery_formats(self.kind):
            raise ValueError(f"{self.kind} 不支持 {fmt}；支持 {supported_delivery_formats(self.kind)}")
        self.output_format = fmt
        allowed = {
            "report": {"purpose", "content", "svg_code", "append"},
            "table": {"columns", "rows", "analysis_result_ref", "result_section", "append"},
            "bibliography": {"references", "source", "append"},
            "research_notes": {"project_name", "limit", "svg_code"},
            "corpus_gap": {"gaps", "append"},
        }[self.kind] | {"kind", "title", "output_format"}
        for name, field in type(self).model_fields.items():
            if name not in allowed and getattr(self, name) != field.get_default(call_default_factory=True):
                raise ValueError(f"{self.kind} 不接受参数 {name}")
        if self.kind == "research_notes" and self.title:
            raise ValueError("课题记录以 project_name 命名，不接受 title")
        if self.kind == "table" and self.analysis_result_ref and (self.columns or self.rows or self.append):
            raise ValueError("分析结果引用不能同时覆盖行列或追加")
        if self.kind == "table" and not self.analysis_result_ref and not self.rows:
            raise ValueError("需要表格数据行或分析结果引用，不发布空表")
        if fmt == "svg" and not self.svg_code and "<svg" not in self.content.lower():
            raise ValueError("SVG 导出需要 svg_code 或正文中的完整 SVG")
        if self.kind == "bibliography" and self.source and self.references:
            raise ValueError("请选择 references 或 source，不能同时提供")
        if self.kind == "corpus_gap" and (not self.gaps or len(self.gaps) > 5000):
            raise ValueError("需要 1–5000 条语料缺口，不发布空报告或截断批次")
        return self


def legacy_delivery_call(name: str, args: dict) -> tuple[str, dict]:
    """Adapt a pending legacy call without mutating it; unrelated calls pass through.

    Signature binding catches unknown arguments instead of silently losing them.
    JSON strings are decoded only here; the canonical schema uses typed arrays.
    """
    if name not in LEGACY_DELIVERY_NAMES:
        return name, dict(args)
    from src.research import tools
    bound = inspect.signature(getattr(tools, name).func).bind(**args)
    bound.apply_defaults()
    old = dict(bound.arguments)
    mapped: dict = {}
    if name in {"export_paper_analysis", "export_experiment_design"}:
        purpose = "paper_analysis" if name == "export_paper_analysis" else "experiment_design"
        content_key = "analysis_markdown" if purpose == "paper_analysis" else "design_markdown"
        mapped.update(kind="report", purpose=purpose, content=old.pop(content_key))
    elif name == "export_table":
        mapped.update(kind="table", columns=old.pop("columns_json"), rows=old.pop("rows_json"))
    elif name == "export_literature_evidence":
        mapped.update(kind="bibliography", references=old.pop("evidence_rows_json"))
        if old["source"].strip().lower() in {"verification", "verify", "last_verification"}:
            old["source"] = "last_verification"
    elif name == "export_research_notes":
        mapped["kind"] = "research_notes"
    else:
        mapped.update(kind="corpus_gap", gaps=old.pop("gaps_json"))
    mapped.update(old)
    for key in ("columns", "rows", "references", "gaps"):
        if key in mapped and isinstance(mapped[key], str):
            mapped[key] = json.loads(mapped[key]) if mapped[key].strip() else []
    # Old result loaders accept a JSON-encoded reference as well as its id.
    return "deliver_research_artifact", DeliveryInput.model_validate(mapped).model_dump()


def _dispatch(request: DeliveryInput) -> str:
    from src.research import tools
    common = {"title": request.title, "append": request.append}
    if request.kind == "report":
        if request.purpose == "general":
            return tools._export_markdown(request.title, request.content, "研究报告", request.svg_code, append=request.append)
        name, key = (("export_paper_analysis", "analysis_markdown") if request.purpose == "paper_analysis"
                     else ("export_experiment_design", "design_markdown"))
        return getattr(tools, name).func(**common, **{key: request.content}, svg_code=request.svg_code)
    if request.kind == "table":
        ref = request.analysis_result_ref
        return tools.export_table.func(**common, columns_json=request.columns, rows_json=request.rows,
            output_format=request.output_format, analysis_result_ref=ref.model_dump() if isinstance(ref, BaseModel) else ref,
            result_section=request.result_section)
    if request.kind == "bibliography":
        return tools.export_literature_evidence.func(**common,
            evidence_rows_json=[row.model_dump() for row in request.references],
            output_format=request.output_format, source=request.source)
    if request.kind == "research_notes":
        return tools.export_research_notes.func(project_name=request.project_name, limit=request.limit, svg_code=request.svg_code)
    return tools.export_corpus_gap_report.func(**common,
        gaps_json=json.dumps([row.model_dump() for row in request.gaps], ensure_ascii=False))


def _receipts(text: str) -> tuple[list[dict], bool]:
    """Verify disk, owner, request AND committed library entry, using existing checks."""
    from src.core.tracing import get_client_request_id, get_user_id
    from src.research import tools
    from src.session import files
    links = list(dict.fromkeys(re.findall(r"/api/v2/session/[A-Za-z0-9_-]+/artifacts/[a-f0-9]{32}", text)))
    if not links:
        return [], False
    verified = tools.validate_artifact_links(links, request_id=str(get_client_request_id() or ""))
    receipts = []
    with closing(sqlite3.connect(str(tools._db_path()))) as conn:
        conn.row_factory = sqlite3.Row
        for item in verified:
            if not item.get("valid"):
                # The legacy matcher compares bibliography MIME strings exactly,
                # while its renderer emits a charset parameter. Reuse that same
                # byte-format matcher with the media type (parameters removed).
                # All ownership/request/hash checks must have passed first.
                if not (item.get("reason_code") == "format_mismatch" and
                        all(item.get(key) for key in ("exists", "hash_matches", "owner_matches", "request_matches"))):
                    continue
                resolved = tools.resolve_artifact(str(tools.get_session_id() or ""), item["artifact_id"])
                if not resolved or not tools._artifact_format_matches(
                        resolved["path"], item["filename"], item["mime"].split(";", 1)[0].strip()):
                    continue
            row = conn.execute("SELECT file_id, artifact_sha256, size_bytes FROM publication_outbox "
                               "WHERE artifact_id=? AND owner_user_id=? AND state='committed'",
                               (item["artifact_id"], int(get_user_id() or 0))).fetchone()
            if not row or not row["file_id"] or int(row["size_bytes"]) <= 0:
                continue
            entry = files.get_account_file(row["file_id"], int(get_user_id() or 0))
            if not entry or entry.get("sha256") != row["artifact_sha256"]:
                continue
            receipts.append({"artifact_id": item["artifact_id"], "file_id": row["file_id"],
                             "filename": item["filename"], "mime": item["mime"], "url": item["link"],
                             "sha256": row["artifact_sha256"], "size_bytes": row["size_bytes"]})
    return receipts, len(receipts) == len(links)


@tool(args_schema=DeliveryInput, response_format="content_and_artifact")
def deliver_research_artifact(**kwargs) -> tuple[str, dict]:
    """将已具备的研究内容发布到资料库，由界面下载导出。

    kind=report：content Markdown，purpose=general/paper_analysis/experiment_design；HTML，可附 svg_code。
    kind=table：columns+rows 或 analysis_result_ref+result_section；CSV/TSV/XLSX。
    kind=bibliography：references 或 source=last_verification；XLSX/RIS/BibTeX。
    kind=research_notes：project_name、limit、可选 svg_code；HTML。
    kind=corpus_gap：gaps；CSV。append 仅用于报告、表格、文献、语料缺口分批追加。
    不生成缺失科学数据；Word 文档编辑请使用 word_document。
    """
    request = DeliveryInput.model_validate(kwargs)
    from src.core.tracing import get_current_tool_name, set_current_tool_name
    renderer = {"table": "export_table", "bibliography": "export_literature_evidence",
                "research_notes": "export_research_notes", "corpus_gap": "export_corpus_gap_report"}.get(request.kind)
    if request.kind == "report":
        renderer = {"general": "render_research_report", "paper_analysis": "export_paper_analysis",
                    "experiment_design": "export_experiment_design"}[request.purpose]
    previous_tool = get_current_tool_name()
    try:
        # Persist the concrete format producer, not the union of all possible
        # kinds of this model-facing entrypoint. Historical rows stay unchanged.
        set_current_tool_name(renderer)
        text = _dispatch(request)
    finally:
        set_current_tool_name(previous_tool)
    text = text if isinstance(text, str) else ""
    receipts, complete = _receipts(text)
    # Legacy renderers report validation/publication errors in-band, including
    # partial HTML success followed by a failed SVG companion publication.
    error = re.search(r"\[(DELIVERABLE_REJECTED(?::[a-z0-9_]+)?|ERR_[A-Z0-9_]+|SVG_FORMAT_ERROR)\]", text)
    expected_extension = ".bib" if request.output_format == "bibtex" else "." + request.output_format
    requested_present = any(r["filename"].lower().endswith(expected_extension) for r in receipts)
    success = bool(receipts) and complete and requested_present and not error
    status = "complete" if success else "partial" if receipts else "failed"
    reason = "" if success else error.group(1).split(":")[-1].lower() if error else "delivery_not_verified"
    if not success:
        text = f"[DELIVERY_{status.upper()}] 成果未全部确认入库（{reason}）。\n" + text
    artifact = {"kind": request.kind, "purpose": request.purpose, "format": request.output_format,
                "receipts": receipts, "file_refs": [r["file_id"] for r in receipts],
                "_tool_outcome": {"status": status, "reason_code": reason, "artifacts": receipts}}
    return text, artifact

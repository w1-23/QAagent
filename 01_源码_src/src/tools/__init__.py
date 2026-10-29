"""Active tool registry for the single General Agent architecture."""
from src.tools.registry import register_tool, get_tool_spec, PartitionedToolNode, init_tool_registry
from src.tools.registry import get_tools_by_category, cleanup_offload_files, get_offload_file_list
from src.tools.deepseek_search_fetch import (
    deepseek_search, deepseek_fetch, deepseek_fetch_paper, local_hybrid_search,
)
from src.tools.draw_svg import draw_svg
from src.research.tools import (
    inspect_uploaded_table, audit_uploaded_data, export_literature_evidence,
    verify_uploaded_references, fetch_and_audit_open_supplements,
    export_paper_analysis, export_experiment_design, save_research_note,
    recall_research_notes, export_research_notes, export_corpus_gap_report,
)

_TOOL_REGISTRY = [
    deepseek_search, deepseek_fetch, deepseek_fetch_paper, local_hybrid_search,
    draw_svg, inspect_uploaded_table, audit_uploaded_data,
    verify_uploaded_references, fetch_and_audit_open_supplements,
    export_literature_evidence, export_paper_analysis, export_experiment_design,
    save_research_note, recall_research_notes, export_research_notes,
    export_corpus_gap_report,
]
_TOOL_REGISTRY_BY_NAME: dict[str, object] = {t.name: t for t in _TOOL_REGISTRY}


def get_tool_names():
    return [t.name for t in _TOOL_REGISTRY]


# Register the code-owned tool set and retain categories for execution tracing.
init_tool_registry()

# Write semantics are graded, not binary.  `is_readonly` previously defaulted to
# True for every tool, including ones that create account records or files.  The
# registry uses this flag in the sandbox decision
# (registry.py:_check_tool_sandbox), so marking a writing tool read-only made the
# sandbox skip its own checks.  Three classes are now distinguished:
#
#   READ        no persistence, no filesystem effect        -> is_readonly=True
#   SESSION     writes a session-scoped artifact (file/HTML) -> readonly=False,
#               still allowed because the export route is explicitly authorised
#   PERSISTENT  writes account-level research notes          -> readonly=False
#
# Changing the flag alone is not enough; see the accompanying note in
# registry.py, which keeps the explicit-request path working while the sandbox
# now evaluates writing tools instead of skipping them.
_READ_ONLY_TOOLS = {
    "deepseek_search", "deepseek_fetch", "deepseek_fetch_paper",
    "local_hybrid_search", "inspect_uploaded_table", "audit_uploaded_data",
    "verify_uploaded_references", "fetch_and_audit_open_supplements",
    "recall_research_notes",
}
_SESSION_ARTIFACT_TOOLS = {
    "export_literature_evidence", "export_paper_analysis",
    "export_experiment_design", "export_research_notes",
    "export_corpus_gap_report", "draw_svg",
}
_PERSISTENT_TOOLS = {"save_research_note"}

for _tool, _category, _key in (
    (deepseek_search, "web", "deepseek_search"),
    (deepseek_fetch, "web", "deepseek_fetch"),
    (deepseek_fetch_paper, "web", "deepseek_fetch_paper"),
    (local_hybrid_search, "rag", "local_hybrid_search"),
    (inspect_uploaded_table, "research", "research_data"),
    (audit_uploaded_data, "research", "research_data"),
    (verify_uploaded_references, "research", "research_references"),
    (fetch_and_audit_open_supplements, "research", "research_web_fetch"),
    (export_literature_evidence, "research", "research_artifacts"),
    (export_paper_analysis, "research", "research_artifacts"),
    (export_experiment_design, "research", "research_artifacts"),
    (save_research_note, "research", "research_notes"),
    (recall_research_notes, "research", "research_notes"),
    (export_research_notes, "research", "research_artifacts"),
    (export_corpus_gap_report, "research", "research_artifacts"),
    # draw_svg was listed in _TOOL_REGISTRY but never entered this registration
    # loop, so get_tool_spec("draw_svg") returned None and any registry-based
    # capability check silently skipped it.
    (draw_svg, "analysis", "draw_svg"),
):
    if get_tool_spec(_tool.name) is None:
        readonly = _tool.name in _READ_ONLY_TOOLS
        register_tool(_tool, category=_category, is_readonly=readonly,
                      concurrency_key=_key)

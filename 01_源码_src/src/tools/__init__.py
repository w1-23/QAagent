"""Single source of truth for the active General Agent tool registry.

Each active tool is declared once with its route category, concurrency key,
and persistence scope. This avoids the old split between Python registration,
YAML name lists, and sandbox allowlists.
"""
from __future__ import annotations

from src.tools.registry import (
    PartitionedToolNode,
    clear_tool_registry,
    cleanup_offload_files,
    get_offload_file_list,
    get_tool_spec,
    get_tools_by_category,
    register_tool,
)
from src.tools.deepseek_search_fetch import (
    deepseek_search,
    deepseek_fetch,
    deepseek_fetch_paper,
    local_hybrid_search,
)
from src.tools.draw_svg import draw_svg
from src.tools.account_files import read_account_file
from src.research.tools import (
    inspect_uploaded_table,
    audit_uploaded_data,
    export_literature_evidence,
    verify_uploaded_references,
    fetch_and_audit_open_supplements,
    export_paper_analysis,
    export_experiment_design,
    save_research_note,
    recall_research_notes,
    export_research_notes,
    export_corpus_gap_report,
)

# (tool, category, concurrency key, write scope, requires, produces, authorization)
#
# write scope: None means no persistent write. Explicit capabilities are
# interpreted by registry.py.
# requires: environment preconditions that come from the tool implementation,
#   not from the tool's name (see src/graph/tool_availability.py). ``attachment``
#   is only used where the code really reads this turn's uploads; a tool that
#   accepts a DOI/title/rows from the model does not require an upload.
# produces: deliverable kinds the tool actually writes to storage.
# authorization: execution-time permission; visibility is never affected.
_TOOL_DECLARATIONS = (
    (deepseek_search, "web", "deepseek_search", None, ("network",), (), ""),
    (deepseek_fetch, "web", "deepseek_fetch", None, ("network",), (), ""),
    (deepseek_fetch_paper, "web", "deepseek_fetch_paper", None, ("network",), (), ""),
    (local_hybrid_search, "rag", "local_hybrid_search", None, ("local_corpus",), (), ""),
    (draw_svg, "analysis", "draw_svg", None, (), ("figure",), ""),
    (read_account_file, "files", "read_account_file", None, ("account_files",), (), ""),
    (inspect_uploaded_table, "research", "research_data", None,
     ("attachment", "research_tools"), (), ""),
    (audit_uploaded_data, "research", "research_data", "session_artifact",
     ("attachment", "research_tools", "session"), ("audit_report",), ""),
    (verify_uploaded_references, "research", "research_references", "session_artifact",
     ("research_tools", "session"), ("verification_sheet",), ""),
    (fetch_and_audit_open_supplements, "research", "research_web_fetch", "session_artifact",
     ("network", "research_tools", "session"),
     ("supplement_archive", "supplement_report"), ""),
    (export_literature_evidence, "research", "research_artifacts", "session_artifact",
     ("session", "research_tools"), ("bibliography",), ""),
    (export_paper_analysis, "research", "research_artifacts", "session_artifact",
     ("session", "research_tools"), ("report_html",), ""),
    (export_experiment_design, "research", "research_artifacts", "session_artifact",
     ("session", "research_tools"), ("report_html",), ""),
    (save_research_note, "research", "research_notes", "account_record",
     ("session", "research_tools"), ("note",), "explicit_user_request"),
    (recall_research_notes, "research", "research_notes", None, ("research_tools",), (), ""),
    (export_research_notes, "research", "research_artifacts", "session_artifact",
     ("session", "research_tools"), ("report_html",), ""),
    (export_corpus_gap_report, "research", "research_artifacts", "session_artifact",
     ("session", "research_tools"), ("gap_csv",), ""),
)

_names = [tool.name for tool, *_ in _TOOL_DECLARATIONS]
if len(_names) != len(set(_names)):
    raise RuntimeError("Duplicate active tool name in the General Agent registry")

_TOOL_REGISTRY = [tool for tool, *_ in _TOOL_DECLARATIONS]
_TOOL_REGISTRY_BY_NAME = {tool.name: tool for tool in _TOOL_REGISTRY}

# name -> (requires, produces, authorization), read by tool_availability and by
# the tests that keep the declaration honest.
_TOOL_BINDINGS = {
    tool.name: (tuple(requires), tuple(produces), authorization)
    for tool, _category, _key, _scope, requires, produces, authorization in _TOOL_DECLARATIONS
}


def get_tool_names() -> list[str]:
    return list(_TOOL_REGISTRY_BY_NAME)


# Rebuild only this code-owned registry on import/reload. No stale YAML name
# list or previously loaded registration can silently shadow these declarations.
clear_tool_registry()
for _tool, _category, _key, _write_scope, _requires, _produces, _authorization in _TOOL_DECLARATIONS:
    register_tool(
        _tool,
        category=_category,
        is_readonly=_write_scope is None,
        concurrency_key=_key,
        write_scope=_write_scope,
    )

__all__ = [
    "register_tool", "get_tool_spec", "PartitionedToolNode", "clear_tool_registry",
    "get_tools_by_category", "cleanup_offload_files", "get_offload_file_list",
    "get_tool_names", "_TOOL_REGISTRY", "_TOOL_REGISTRY_BY_NAME", "_TOOL_BINDINGS",
]

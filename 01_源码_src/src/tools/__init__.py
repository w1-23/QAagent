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

# (tool, category, concurrency key, write scope). None means no persistent
# write. Write scopes are explicit capabilities interpreted by registry.py.
_TOOL_DECLARATIONS = (
    (deepseek_search, "web", "deepseek_search", None),
    (deepseek_fetch, "web", "deepseek_fetch", None),
    (deepseek_fetch_paper, "web", "deepseek_fetch_paper", None),
    (local_hybrid_search, "rag", "local_hybrid_search", None),
    (draw_svg, "analysis", "draw_svg", None),
    (inspect_uploaded_table, "research", "research_data", None),
    (audit_uploaded_data, "research", "research_data", "session_artifact"),
    (verify_uploaded_references, "research", "research_references", "session_artifact"),
    (fetch_and_audit_open_supplements, "research", "research_web_fetch", "session_artifact"),
    (export_literature_evidence, "research", "research_artifacts", "session_artifact"),
    (export_paper_analysis, "research", "research_artifacts", "session_artifact"),
    (export_experiment_design, "research", "research_artifacts", "session_artifact"),
    (save_research_note, "research", "research_notes", "account_record"),
    (recall_research_notes, "research", "research_notes", None),
    (export_research_notes, "research", "research_artifacts", "session_artifact"),
    (export_corpus_gap_report, "research", "research_artifacts", "session_artifact"),
)

_names = [tool.name for tool, *_ in _TOOL_DECLARATIONS]
if len(_names) != len(set(_names)):
    raise RuntimeError("Duplicate active tool name in the General Agent registry")

_TOOL_REGISTRY = [tool for tool, *_ in _TOOL_DECLARATIONS]
_TOOL_REGISTRY_BY_NAME = {tool.name: tool for tool in _TOOL_REGISTRY}


def get_tool_names() -> list[str]:
    return list(_TOOL_REGISTRY_BY_NAME)


# Rebuild only this code-owned registry on import/reload. No stale YAML name
# list or previously loaded registration can silently shadow these declarations.
clear_tool_registry()
for _tool, _category, _key, _write_scope in _TOOL_DECLARATIONS:
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
    "get_tool_names", "_TOOL_REGISTRY", "_TOOL_REGISTRY_BY_NAME",
]

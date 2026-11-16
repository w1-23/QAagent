"""Installed tool contracts; implementations are imported only on resolution.

The adjacent JSON is a code-owned package resource, generated at development
time from the installed tools. It is never supplied by a request, skill, model,
or workspace document. Import targets and policy metadata live in Python below;
the manifest cannot introduce executable targets or grant permissions.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from importlib import import_module
from importlib.resources import files
import json
from types import MappingProxyType

from src.agent.capabilities import CapabilityCatalog, CapabilityDefinition, CapabilityLoadError

# One process uses one installed contract snapshot, including lazily loaded tools.
# Updating package files requires a new process; no request hot-reloads metadata.
_INSTALLED_MANIFEST = files(__package__).joinpath("preset_tool_schemas.json").read_text(encoding="utf-8")

__all__ = ["build_preset_catalog", "preset_bindings", "preset_metadata"]

# Original named imports in tools/legacy_registry.py. Never resolve a module or
# attribute from manifest/request text. Updating this whitelist is a code change.
_TARGETS = MappingProxyType({
    "request_user_input": "src.tools.interaction",
    "deepseek_search": "src.tools.deepseek_search_fetch",
    "deepseek_fetch": "src.tools.deepseek_search_fetch",
    "deepseek_fetch_paper": "src.tools.deepseek_search_fetch",
    "local_hybrid_search": "src.tools.deepseek_search_fetch",
    "inspect_candidates": "src.tools.candidate_tools",
    "read_candidate": "src.tools.candidate_tools",
    "search_candidates": "src.tools.candidate_tools",
    "draw_svg": "src.tools.draw_svg",
    "read_account_file": "src.tools.account_files",
    "read_tool_result": "src.research.result_tools",
    "research_workspace": "src.research.workspace_tools",
    "inspect_uploaded_table": "src.research.tools",
    "audit_uploaded_data": "src.research.tools",
    "search_literature": "src.research.tools",
    "verify_uploaded_references": "src.research.tools",
    "fetch_and_audit_open_supplements": "src.research.tools",
    "word_document": "src.research.word_tools",
    "deliver_research_artifact": "src.research.delivery_tools",
    "save_research_note": "src.research.tools",
    "recall_research_notes": "src.research.tools",
    "analyze_research_data": "src.research.statistics_tools",
    "audit_research_images": "src.research.image_integrity_tools",
    "create_research_figure": "src.research.figure_tools",
    "run_research_figure_script": "src.research.figure_tools",
})


@dataclass(frozen=True)
class _Preset:
    name: str
    category: str
    concurrency_key: str
    write_scope: str | None
    requires: tuple[str, ...]
    produces: tuple[str, ...]
    authorization: str = ""
    recovery_mode: str = "never"

    @property
    def execution_mode(self) -> str:
        if self.category == "interaction":
            return "interaction"
        return "sequential" if self.write_scope is not None else "parallel"


# Snapshot of _TOOL_DECLARATIONS, with registry.py's effective recovery policy.
# The development equivalence test checks these against the installed legacy
# declarations, but production discovery never imports that eager registry.
_PRESETS = (
    _Preset("request_user_input", "interaction", "user_question", None, ("session",), ()),
    _Preset("deepseek_search", "web", "deepseek_search", None, ("network",), ()),
    _Preset("deepseek_fetch", "web", "deepseek_fetch", None, ("network",), ()),
    _Preset("deepseek_fetch_paper", "web", "deepseek_fetch_paper", None, ("network",), ()),
    _Preset("local_hybrid_search", "rag", "local_hybrid_search", None, ("local_corpus",), ()),
    _Preset("inspect_candidates", "rag", "candidate_pool", None, ("local_corpus", "candidate_pool"), ()),
    _Preset("read_candidate", "rag", "candidate_pool", None, ("local_corpus", "candidate_pool"), ()),
    _Preset("search_candidates", "rag", "candidate_pool", None, ("local_corpus", "candidate_pool"), ()),
    _Preset("draw_svg", "analysis", "draw_svg", None, (), ("figure",)),
    _Preset("read_account_file", "files", "read_account_file", None, ("account_files",), (), recovery_mode="local_read"),
    _Preset("read_tool_result", "files", "read_tool_result", None, ("session",), ()),
    _Preset("research_workspace", "research", "research_workspace", "session_artifact", ("session", "research_tools"), ()),
    _Preset("inspect_uploaded_table", "research", "research_data", None, ("research_tools", "session"), ()),
    _Preset("audit_uploaded_data", "research", "research_data", "session_artifact", ("research_tools", "session"), ("annotation_table", "report_json")),
    _Preset("search_literature", "web", "literature_search", None, ("network",), ()),
    _Preset("verify_uploaded_references", "research", "research_references", "session_artifact", ("research_tools", "session"), ("verification_sheet",)),
    _Preset("fetch_and_audit_open_supplements", "research", "research_web_fetch", "session_artifact", ("network", "research_tools", "session"), ("supplement_archive", "supplement_report")),
    _Preset("word_document", "research", "word_documents", "session_artifact", ("session", "research_tools"), ("report_docx",)),
    _Preset("deliver_research_artifact", "research", "research_artifacts", "session_artifact", ("session", "research_tools"), ("table_file", "bibliography", "report_html", "gap_csv")),
    _Preset("save_research_note", "research", "research_notes", "account_record", ("session", "research_tools"), ("note",), "explicit_user_request"),
    _Preset("recall_research_notes", "research", "research_notes", None, ("research_tools",), ()),
    _Preset("analyze_research_data", "research", "research_statistics", "session_artifact", ("research_tools", "session"), ("statistics_report",)),
    _Preset("audit_research_images", "research", "research_image_integrity", "session_artifact", ("account_files", "research_tools", "session"), ("report_json",)),
    _Preset("create_research_figure", "research", "research_artifacts", "session_artifact", ("research_tools", "session"), ("figure_package",)),
    _Preset("run_research_figure_script", "research", "research_artifacts", "session_artifact", ("session", "research_tools", "script_sandbox"), ("figure_package",)),
)


def preset_bindings() -> dict[str, tuple[tuple[str, ...], tuple[str, ...], str]]:
    """Host readiness/authorization inputs; discovery itself grants no rights."""
    return {p.name: (p.requires, p.produces, p.authorization) for p in _PRESETS}


def preset_metadata() -> dict[str, dict]:
    """Return independent policy records without loading implementations."""
    return {p.name: {
        "category": p.category, "concurrency_key": p.concurrency_key,
        "write_scope": p.write_scope, "is_readonly": p.write_scope is None,
        "authorization": p.authorization, "recovery_mode": p.recovery_mode,
        "execution_mode": p.execution_mode,
    } for p in _PRESETS}


def _read_manifest() -> dict[str, dict]:
    manifest = json.loads(_INSTALLED_MANIFEST)
    if set(manifest) != {"version", "tools"} or manifest["version"] != 1:
        raise ValueError("invalid_preset_manifest")
    if not isinstance(manifest["tools"], list):
        raise ValueError("invalid_preset_schemas")
    schemas = {}
    for schema in manifest["tools"]:
        if not isinstance(schema, dict) or set(schema) != {"type", "function"} or schema["type"] != "function":
            raise ValueError("invalid_preset_schema")
        function = schema["function"]
        if not isinstance(function, dict) or set(function) != {"name", "description", "parameters"}:
            raise ValueError("invalid_preset_function")
        name = function["name"]
        if not isinstance(name, str) or name not in _TARGETS or name in schemas:
            raise ValueError("unknown_or_duplicate_preset")
        if not isinstance(function["description"], str) or not function["description"].strip():
            raise ValueError("invalid_preset_description")
        if not isinstance(function["parameters"], dict) or function["parameters"].get("type") != "object":
            raise ValueError("invalid_preset_parameters")
        schemas[name] = schema
    if set(schemas) != set(_TARGETS) or set(schemas) != {p.name for p in _PRESETS}:
        raise ValueError("incomplete_preset_manifest")
    return schemas


def _load(preset: _Preset, expected: dict):
    # Both names are closed-over code-owned values, never manifest targets.
    tool = getattr(import_module(_TARGETS[preset.name]), preset.name)
    from langchain_core.utils.function_calling import convert_to_openai_tool

    actual = convert_to_openai_tool(tool)
    if actual != expected:
        def difference(left, right, path=""):
            if isinstance(left, dict) and isinstance(right, dict):
                for key in sorted(set(left) | set(right)):
                    child = path + "/" + str(key)
                    if key not in left or key not in right:
                        return child.strip("/")
                    if left[key] != right[key]:
                        return difference(left[key], right[key], child)
            return path.strip("/") or "schema"
        raise CapabilityLoadError("preset_schema_drift", difference(actual, expected))
    # Register only the validated selected tool; preserve all existing specs.
    from src.tools.registry import register_tool

    # Extension validators are server code, outside the provider schema and
    # Skill payload. The runner invokes them before marking execution dispatched.
    validator = {"audit_uploaded_data": "validate_audit_request",
                 "inspect_uploaded_table": "validate_inspection_request",
                 "audit_research_images": "validate_image_audit_request"}.get(preset.name)
    preflight = getattr(import_module(_TARGETS[preset.name]), validator) if validator else None
    register_tool(tool, category=preset.category,
                  is_readonly=preset.write_scope is None,
                  concurrency_key=preset.concurrency_key,
                  write_scope=preset.write_scope,
                  authorization=preset.authorization,
                  recovery_mode=preset.recovery_mode, preflight=preflight)
    return tool


def build_preset_catalog() -> CapabilityCatalog:
    """Describe all installed tools without importing any implementation.

    The host must still supply its authorized/ready name set to the catalogue.
    A missing dependency or changed implementation fails closed on resolution,
    before registration/dispatch, and affects only the selected capability.
    """
    schemas = _read_manifest()
    catalog = CapabilityCatalog()
    for preset in _PRESETS:
        schema = schemas[preset.name]
        function = schema["function"]
        catalog.register(CapabilityDefinition(
            name=preset.name, description=function["description"],
            parameters=function["parameters"], load=partial(_load, preset, schema),
            provider=_TARGETS[preset.name], mutates=preset.write_scope is not None,
            execution_mode=preset.execution_mode,
        ))
    return catalog

"""Check tool arguments against the declared schema *before* dispatch.

``content_and_artifact`` tools are executed through ``raw_func(**args)`` so the
artifact survives (``ainvoke`` drops it). That bypasses pydantic, so a wrong
keyword arrives as a bare ``TypeError`` and used to reach the model as
"[ERR_PARSE] 工具执行异常 … 建议: 检查参数是否合理" — nothing it could act on.
The audited run contains exactly that for ``deepseek_fetch(doi=...)``.

One checker, used by both the executor and the DSML recovery path, so the two
cannot drift apart. It only reads the schema; it never coerces or invents a
value, and it never decides whether a tool is allowed to run.
"""
from __future__ import annotations

from typing import Any

# Identifier-shaped arguments are a routing mistake, not a typo. The alternative
# tool is discovered from the live registry/schema, not maintained as a second
# name list: any registered tool that accepts the same field is a candidate, and
# a paper/full-text label wins over a generic reader.
_IDENTIFIER_FIELDS = frozenset({"doi", "pmid", "arxiv_id", "title"})
_ALTERNATIVE_HINTS = ("paper", "fulltext", "full-text", "doi", "论文", "全文", "文献")


def _alternative_tool(tool_name: str, arg_name: str) -> str:
    try:
        from src.tools.registry import _TOOL_SPECS
        from src.core.tool_catalog import TOOL_METADATA
    except Exception:
        return ""
    best: tuple[int, str] = (0, "")
    for name, spec in _TOOL_SPECS.items():
        if name == tool_name:
            continue
        if arg_name not in schema_fields(getattr(spec, "func", None)):
            continue
        metadata = TOOL_METADATA.get(name)
        haystack = " ".join((
            name,
            getattr(metadata, "display_label", "") if metadata else "",
            getattr(metadata, "link_label", "") if metadata else "",
        )).casefold()
        score = sum(2 for hint in _ALTERNATIVE_HINTS if hint in haystack)
        if score > best[0]:
            best = (score, name)
    return best[1]


def _parameter_hint(tool_name: str, arg_name: str, fields: dict) -> str:
    """A concrete alternative, derived from the tool that actually accepts it."""
    if arg_name in {"path", "file_path"} and "file_id" in fields:
        return "该工具使用 file_id=…，不是 path。"
    if arg_name not in _IDENTIFIER_FIELDS:
        return ""
    alternative = _alternative_tool(tool_name, arg_name)
    if alternative:
        return f"若要按标识符取全文，请改用 {alternative}({arg_name}=…)。"
    return ""


def schema_fields(tool: Any) -> dict:
    model = getattr(tool, "args_schema", None)
    fields = getattr(model, "model_fields", None)
    if fields is not None:
        return fields
    return getattr(model, "__fields__", None) or {}


def _is_required(field: Any) -> bool:
    probe = getattr(field, "is_required", None)
    if callable(probe):
        return bool(probe())
    return bool(getattr(field, "required", False))


def argument_problem(tool_name: str, args: dict, fields: dict) -> str:
    """Return an actionable message, or "" when the arguments fit the schema."""
    if not fields:
        return ""
    args = args if isinstance(args, dict) else {}
    allowed = set(fields)
    unknown = [name for name in args if name not in allowed]
    if unknown:
        listed = "、".join(sorted(allowed))
        message = (f"[ERR_ARG] 工具 {tool_name} 不接受参数 "
                   f"{'、'.join(sorted(unknown))}；本次未执行。"
                   f"该工具的合法参数：{listed}。")
        for name in sorted(unknown):
            hint = _parameter_hint(tool_name, name, fields)
            if hint:
                message += hint
                break
        return message + "请改用上述参数名后重试，其余步骤照常继续。"
    missing = sorted(name for name, field in fields.items()
                     if _is_required(field) and name not in args)
    if missing:
        return (f"[ERR_ARG] 工具 {tool_name} 缺少必填参数 "
                f"{'、'.join(missing)}；本次未执行。请补齐后重试。")
    return ""


def check_tool_arguments(tool: Any, tool_name: str, args: dict) -> str:
    return argument_problem(tool_name, args, schema_fields(tool))
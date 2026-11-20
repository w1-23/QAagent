"""Shared, data-only contract for sandbox script outputs.

Names are identifiers, never paths. Receipts are untrusted container data:
validation binds them to host-declared slots, not to claims printed by scripts.
"""
from __future__ import annotations

import ast
import json
import re
from typing import Any

MAX_V2_FILES = 64
MAX_RECEIPT_BYTES = 64 * 1024
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z", re.ASCII)
_FORMATS = ("svg", "png", "pdf", "tiff")
# The separated renderer is handed frozen tables only; it is not given the
# host-owned ``emit`` object. The tag is stable across preflight and runtime.
RENDER_SCRIPT_EMIT_NOT_AVAILABLE = "render_script_emit_not_available"


class RenderScriptEmitUnavailable(ValueError):
    """A separated render_script referenced the host-only ``emit`` API."""

    reason_code = RENDER_SCRIPT_EMIT_NOT_AVAILABLE

    def __init__(self, message: str, *, line: int | None = None) -> None:
        super().__init__(message)
        self.line = line


def _bound_names(tree: ast.AST) -> set[str]:
    """Names the renderer binds anywhere; a bound ``emit`` is not host emit.

    Conservative on purpose: when the source defines its own ``emit`` (a
    local, parameter, import alias, loop target or function name), the static
    check declines and the runtime worker boundary stays in charge.
    """
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
            arguments = getattr(node, "args", None)
            if arguments is not None:
                bound.update(argument.arg for argument in (
                    *arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs))
                if arguments.vararg is not None:
                    bound.add(arguments.vararg.arg)
                if arguments.kwarg is not None:
                    bound.add(arguments.kwarg.arg)
        elif isinstance(node, ast.Lambda):
            arguments = node.args
            bound.update(argument.arg for argument in (
                *arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs))
            if arguments.vararg is not None:
                bound.add(arguments.vararg.arg)
            if arguments.kwarg is not None:
                bound.add(arguments.kwarg.arg)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    bound.add(alias.asname or alias.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    return bound


def _unbound_emit_line(tree: ast.AST) -> int | None:
    """First line with a provably free ``emit`` reference, else ``None``.

    This walks AST names, never text: comments and string literals are
    invisible, and a locally bound ``emit`` is left to the runtime path.
    """
    if "emit" in _bound_names(tree):
        return None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Name) and node.id == "emit"
                and isinstance(node.ctx, ast.Load)):
            return node.lineno
    return None


def render_script_emit_line(source: str) -> int | None:
    """Static helper for callers that hold renderer source text."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    return _unbound_emit_line(tree)


def render_script_emit_message(line: int) -> str:
    """Shortest correction for a renderer that referenced host ``emit``."""
    return (
        f"render_script 第 {line} 行使用了 emit，但独立 renderer 只接收冻结的 data 表，"
        "没有 emit/emit.table/emit.figure。最短改法：在主 script 用 "
        "emit.table('表名', frame, parents=[...]) 冻结数据；render_script 只读 "
        "data['表名'] 并构造一个 Figure。同一 outputs_json 原样重试会得到同样拒绝，"
        "不会启动 Worker。"
    )


def render_script_emit_runtime_message() -> str:
    """Stable diagnosis for a dynamically constructed missing-emit call."""
    return (
        "render_script 运行到未定义的 emit（独立 renderer 只接收冻结的 data 表，"
        "没有 emit/emit.table/emit.figure）。请把 emit.table(...) 放回主 script 冻结数据，"
        "render_script 只读 data['表名'] 并构造一个 Figure；原样重试仍会失败。"
    )


_MISSING_EMIT = re.compile(r"NameError: name ['\"]emit['\"] is not defined\Z")


def classify_render_script_error(error: str) -> str | None:
    """Return the stable tag when a caught error is a missing host ``emit``."""
    if _MISSING_EMIT.fullmatch(str(error or "").strip()):
        return RENDER_SCRIPT_EMIT_NOT_AVAILABLE
    return None


def output_name(value: Any) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError("Output names must be ASCII identifiers of at most 64 characters.")
    return value


def normalize_output_contract(value: Any) -> dict:
    """Normalize v2 only; an absent/empty contract preserves the legacy path."""
    if value is None or value == {}:
        return {}
    if not isinstance(value, dict) or set(value) - {"version", "tables", "figures"}:
        raise ValueError("Invalid output contract fields.")
    if type(value.get("version")) is not int or value["version"] != 2:
        raise ValueError("Output contract version must be 2.")
    result = {"version": 2, "tables": [], "figures": []}
    names = set()
    for kind in ("tables", "figures"):
        entries = value.get(kind, [])
        if not isinstance(entries, list) or len(entries) > 8:
            raise ValueError("At most eight tables and eight figures are allowed.")
        for entry in entries:
            allowed = {"name", "parents"} if kind == "tables" else {
                "name", "title", "formats", "parents", "render_script"}
            if not isinstance(entry, dict) or set(entry) - allowed:
                raise ValueError("Invalid output entry fields.")
            name = output_name(entry.get("name"))
            if name.casefold() in names:
                raise ValueError("Output names must be unique, including across kinds and case.")
            names.add(name.casefold())
            item = {"name": name}
            if kind == "tables" or "parents" in entry:
                parents = entry.get("parents", [])
                if not isinstance(parents, list) or len(parents) > 16:
                    raise ValueError("Invalid output parents.")
                item["parents"] = [output_name(parent) for parent in parents]
                if len(set(item["parents"])) != len(parents) or name in parents:
                    raise ValueError("Duplicate or self output parent.")
            if kind == "figures":
                title = entry.get("title", "")
                if not isinstance(title, str) or len(title) > 1000:
                    raise ValueError("Invalid figure title.")
                formats = entry.get("formats", ["svg"])
                if (not isinstance(formats, list) or not formats or len(formats) > 4
                        or any(not isinstance(fmt, str) or fmt not in _FORMATS for fmt in formats)
                        or len(set(formats)) != len(formats)):
                    raise ValueError("Invalid figure formats.")
                item.update(title=title, formats=list(formats))
                # Optional fields may arrive as null from strict tool schemas.
                # Null means no separate renderer; non-null source stays strict.
                if entry.get("render_script") is not None:
                    source = entry["render_script"]
                    if (not isinstance(source, str) or not source.strip()
                            or len(source.encode("utf-8")) > 256 * 1024):
                        raise ValueError(f"render_script for figure '{name}' must be nonempty bounded Python source.")
                    try:
                        tree = ast.parse(source)
                    except SyntaxError as exc:
                        raise ValueError(f"Invalid render_script syntax for figure '{name}' at line {exc.lineno}, "
                                         f"column {exc.offset}: {exc.msg}") from exc
                    except ValueError as exc:
                        raise ValueError(f"Invalid render_script syntax for figure '{name}': {exc}") from exc
                    emit_line = _unbound_emit_line(tree)
                    if emit_line is not None:
                        raise RenderScriptEmitUnavailable(
                            render_script_emit_message(emit_line), line=emit_line)
                    declared_tables = {table["name"] for table in result["tables"]}
                    if not item.get("parents") or not set(item["parents"]).issubset(declared_tables):
                        raise ValueError("render_script requires explicit nonempty declared output table parents.")
                    item["render_script"] = source
            result[kind].append(item)
    return result


def output_slots(contract: Any, *, output_key: str = "") -> dict[str, str]:
    """Return logical key -> flat filename. Replay keeps original figure indices."""
    normalized = normalize_output_contract(contract)
    if not isinstance(output_key, str):
        raise ValueError("Replay output_key must be a figure name string.")
    if not normalized:
        return {}
    if output_key and output_key not in {item["name"] for item in normalized["figures"]}:
        raise ValueError("Replay target is not a declared figure name.")
    slots = {"result": "result.json", "report": "report.json"}
    if not output_key:
        for index, table in enumerate(normalized["tables"]):
            slots[f"table:{table['name']}"] = f"table_{index:02d}.json"
    for index, figure in enumerate(normalized["figures"]):
        name = figure["name"]
        if output_key and name != output_key:
            continue
        for fmt in figure["formats"]:
            slots[f"figure:{name}:{fmt}"] = f"figure_{index:02d}.{fmt}"
        slots[f"figure:{name}:manifest"] = f"figure_{index:02d}.objects.json"
        slots[f"figure:{name}:overrides"] = f"figure_{index:02d}.overrides.json"
    if len(slots) > MAX_V2_FILES:
        raise ValueError("Too many physical output files.")
    return slots


def strict_json(data: bytes) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key.")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("Non-finite JSON value: " + value)

    return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)


def validate_receipt(data: bytes, contract: Any, *, job_id: str = "",
                     output_key: str = "") -> dict:
    """Validate a bounded receipt before any declared payload is collected."""
    if len(data) > MAX_RECEIPT_BYTES:
        raise ValueError("Output receipt exceeds its size budget.")
    receipt = strict_json(data)
    fields = {"version", "ok", "status", "job_id", "output_key", "slots", "stdout",
              "figure_count", "applied_count"}
    if (not isinstance(receipt, dict) or set(receipt) != fields
            or type(receipt["version"]) is not int or receipt["version"] != 2
            or receipt["ok"] is not True or receipt["status"] not in ("ok", "partial")
            or receipt["job_id"] != job_id or receipt["output_key"] != output_key
            or not isinstance(receipt["stdout"], str) or len(receipt["stdout"]) > 4000
            or type(receipt["figure_count"]) is not int or not 0 <= receipt["figure_count"] <= 8
            or type(receipt["applied_count"]) is not int or not 0 <= receipt["applied_count"] <= 10000):
        raise ValueError("Invalid output receipt identity or fields.")
    slots = output_slots(contract, output_key=output_key)
    states = receipt["slots"]
    if not isinstance(states, dict) or set(states) != set(slots) - {"result"}:
        raise ValueError("Receipt slots differ from host allocation.")
    total = len(data)
    partial = False
    for key, entry in states.items():
        if not isinstance(entry, dict):
            raise ValueError("Invalid output slot state.")
        status = entry.get("status")
        if status == "ok":
            if (set(entry) != {"status", "size", "sha256"}
                    or type(entry["size"]) is not int or not 0 <= entry["size"] <= 24 * 1024 * 1024
                    or not isinstance(entry["sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
                raise ValueError("Invalid output slot size or digest.")
            total += entry["size"]
        elif status in ("missing", "error"):
            if (set(entry) != {"status", "error"} or not isinstance(entry["error"], str)
                    or len(entry["error"]) > 500):
                raise ValueError("Invalid output slot error.")
            partial |= status == "error" or (key != "report" and not key.endswith(":overrides"))
        else:
            raise ValueError("Unknown output slot status.")
    emitted = 0
    for figure in normalize_output_contract(contract)["figures"]:
        name = figure["name"]
        if output_key and name != output_key:
            continue
        prefix = f"figure:{name}:"
        group = {key: state for key, state in states.items() if key.startswith(prefix)}
        emitted += any(state["status"] != "missing" for state in group.values())
        if (any(states[prefix + fmt]["status"] == "ok" for fmt in figure["formats"])
                and states[prefix + "manifest"]["status"] != "ok"):
            raise ValueError("A successful figure format requires its manifest and QA receipt slot.")
    if (total > 64 * 1024 * 1024 or receipt["status"] != ("partial" if partial else "ok")
            or receipt["figure_count"] != emitted):
        raise ValueError("Invalid output receipt aggregate status, figure count or budget.")
    return receipt

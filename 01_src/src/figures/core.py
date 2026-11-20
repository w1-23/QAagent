"""Host figure adapter; shared rendering lives in citrus_figures.

Output contracts and sandbox emission remain host-owned. Generated replay
source keeps src.figures imports for the legacy worker and archived plot.py.
"""
from __future__ import annotations

import io
import sys
from dataclasses import dataclass, field
from typing import Any

from citrus_figures.spec import ResolvedFigure, Series, SpecError
from citrus_figures.styling import apply_base_rcparams, font_stack, palette_colors
from citrus_figures.core import (
    RenderedFigure,
    ScatterPoint,
    _gid,
    _series_gid,
    _register,
    _numeric,
    _labels,
    _apply_series_colour,
    _set_axis_labels,
    _plot_data,
    _draw_errorbars,
    _tick_label_problems,
    _fit_declarative_labels,
    build_resolved_figure as _package_build_resolved_figure,
    _build_resolved_figure,
    _build_panels,
    _last_data_artist,
    compile_spec as _package_compile_spec,
    ScriptResult,
    capture_figure,
)


def build_resolved_figure(resolved: ResolvedFigure) -> RenderedFigure:
    """Coordinate package rendering with the host's existing render lock."""
    from src.figures.runtime import render_context
    with render_context():
        return _package_build_resolved_figure(resolved)


def compile_spec(resolved: ResolvedFigure) -> str:
    """Adapt package replay source to the host's archived-script contract."""
    source = _package_compile_spec(resolved)
    # The standalone package adds CLI export-on-main. Host workers collect
    # artifacts themselves; an archived script historically only builds fig.
    source = source.partition("if __name__ == '__main__':\n")[0]
    return source.replace(
        "from citrus_figures.core import build_resolved_figure",
        "from src.figures.core import build_resolved_figure",
    ).replace(
        "from citrus_figures.spec import ResolvedFigure, Series, StyleSpec",
        "from src.figures.spec import ResolvedFigure, Series, StyleSpec",
    )


@dataclass
class ScriptOutputsResult:
    """In-container objects, released only after normal script completion."""

    tables: dict[str, Any] = field(default_factory=dict)
    figures: dict[str, Any] = field(default_factory=dict)
    frozen_render_receipts: dict[str, dict] = field(default_factory=dict)
    report: Any = None
    report_emitted: bool = False
    stdout: str = ""
    error: str = ""
    error_type: str = ""


class _OutputEmitter:
    def __init__(self, contract: dict, result: ScriptOutputsResult, output_key: str):
        self.contract = contract
        self.result = result
        self.output_key = output_key
        self.seen: set[str] = set()

    def _entry(self, name: str, kind: str) -> dict:
        from src.figures.script_outputs import output_name
        output_name(name)
        entry = next((item for item in self.contract[kind] if item["name"] == name), None)
        if entry is None or name in self.seen:
            raise ValueError("Undeclared or duplicate emitted output: " + name)
        self.seen.add(name)
        return entry

    def table(self, name: str, frame: Any, *, parents=None) -> None:
        entry = self._entry(name, "tables")
        if parents is not None and (not isinstance(parents, (list, tuple))
                                    or list(parents) != entry["parents"]):
            raise ValueError("Emitted table parents differ from the output contract.")
        if not self.output_key:
            if any(name in figure.get("parents", []) and "render_script" in figure
                   for figure in self.contract["figures"]):
                from src.figures.dataset_codec import encode_frame, decode_frame
                # Snapshot at emission, before analysis can mutate its frame again.
                frame = decode_frame(encode_frame(frame))
            self.result.tables[name] = frame

    def figure(self, name: str, fig: Any) -> None:
        entry = self._entry(name, "figures")
        if "render_script" in entry:
            raise ValueError("emit.figure cannot emit a separated render_script target.")
        if not self.output_key or self.output_key == name:
            self.result.figures[name] = fig

    def report(self, value: Any) -> None:
        if self.result.report_emitted:
            raise ValueError("emit.report may only be called once.")
        self.result.report_emitted = True
        if not self.output_key:
            self.result.report = value


def capture_outputs(script: str, *, output_contract: dict,
                    namespace: dict[str, Any] | None = None,
                    output_key: str = "",
                    frozen_tables: dict[str, Any] | None = None) -> ScriptOutputsResult:
    """Execute only in the sandbox worker; this function provides no isolation.

    Unlike legacy capture, explicit v2 emission accepts zero pyplot figures,
    closed figures and Figure objects created without pyplot. A failed script
    publishes no objects, even if it called emit before raising/SystemExit.
    """
    import matplotlib
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt
    from src.figures.script_outputs import (RenderScriptEmitUnavailable,
                                            classify_render_script_error,
                                            normalize_output_contract, output_slots,
                                            render_script_emit_runtime_message)

    contract = normalize_output_contract(output_contract)
    if not contract:
        raise ValueError("capture_outputs requires an explicit v2 contract.")
    output_slots(contract, output_key=output_key)
    render_entries = [entry for entry in contract["figures"] if "render_script" in entry
                      and (not output_key or entry["name"] == output_key)]
    separated_replay = bool(output_key and render_entries)
    if separated_replay:
        required = set(render_entries[0]["parents"])
        if not isinstance(frozen_tables, dict) or set(frozen_tables) != required:
            raise ValueError("Frozen replay inputs must exactly match the target table parents.")
    elif frozen_tables is not None:
        raise ValueError("Frozen inputs are only accepted for a separated target replay.")
    apply_base_rcparams(font_size=7.0, font_stack_values=font_stack(script))
    plt.close("all")
    result = ScriptOutputsResult()
    scope = {"__name__": "__citrusfig__", **(namespace or {})}
    scope.update(plt=plt, matplotlib=matplotlib, show=lambda *a, **k: None,
                 emit=_OutputEmitter(contract, result, output_key))
    previous_show, original_stdout = plt.show, sys.stdout
    stdout = io.StringIO()
    try:
        plt.show = lambda *a, **k: None
        sys.stdout = stdout
        if not separated_replay:
            if render_entries:
                with matplotlib.rc_context():
                    exec(compile(script, "<citrusfig>", "exec"), scope)  # noqa: S102 - sandbox only
            else:
                exec(compile(script, "<citrusfig>", "exec"), scope)  # noqa: S102 - sandbox only
        if render_entries:
            from src.figures.dataset_codec import encode_frame, decode_frame
            from src.figures.frozen_render import render_receipt
            from src.figures.worker import _guard_imports
            tables = frozen_tables if separated_replay else result.tables
            for entry in render_entries:
                source = entry["render_script"]
                problem = _guard_imports(source)
                if problem:
                    raise ValueError(problem)
                missing = set(entry["parents"]) - set(tables)
                if missing:
                    raise ValueError("Missing frozen output tables: " + ", ".join(sorted(missing)))
                payloads = {name: encode_frame(tables[name]) for name in entry["parents"]}
                data = {name: decode_frame(payload) for name, payload in payloads.items()}
                # Fresh globals and default rcParams: neither analysis locals nor
                # its filesystem bindings/style state are supplied to rendering.
                with matplotlib.rc_context(rc=matplotlib.rcParamsDefault):
                    captured = capture_figure(source, namespace={"data": data}, check_layout=False)
                    stdout.write(captured.stdout)
                    if captured.error and classify_render_script_error(captured.error):
                        # Dynamic code (eval/exec/format) can hide an emit call
                        # from the static precheck; classify the real missing
                        # name instead of forwarding a bare NameError.
                        raise RenderScriptEmitUnavailable(
                            render_script_emit_runtime_message())
                    if captured.error or captured.figures_seen != 1:
                        raise ValueError(captured.error or "render_script must leave exactly one Figure.")
                    result.frozen_render_receipts[entry["name"]] = render_receipt(
                        source, payloads, captured.figure)
                    result.figures[entry["name"]] = captured.figure
    except BaseException as exc:
        result.tables.clear()
        result.figures.clear()
        result.frozen_render_receipts.clear()
        result.report = None
        result.report_emitted = False
        if isinstance(exc, RenderScriptEmitUnavailable):
            result.error = f"[RENDER_SCRIPT_EMIT_NOT_AVAILABLE] {exc}"
            result.error_type = exc.reason_code
        else:
            result.error = f"{type(exc).__name__}: {exc}"
            result.error_type = "script_error"
    finally:
        sys.stdout = original_stdout
        plt.show = previous_show
        result.stdout = stdout.getvalue()
        plt.close("all")
    return result


__all__ = [
    "RenderedFigure", "ScriptResult", "ScriptOutputsResult", "build_resolved_figure",
    "capture_figure", "capture_outputs", "compile_spec",
]

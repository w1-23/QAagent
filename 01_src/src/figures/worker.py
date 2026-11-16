"""The sandbox worker: the only place that touches an untrusted script.

This file runs **inside** the container. It is the boundary: the script's
process, its filesystem view and its network access are all confined to the
container that runs this file.

What it does
------------
1. Reads the job description (which script, which inputs, which outputs).
2. Runs the script with Matplotlib in the Agg backend, capturing a legacy
   single figure or explicitly emitted v2 tables, figures and report.
3. Writes host-allocated outputs, with per-item/per-format v2 statuses.
4. Commits a bounded v2 result.json receipt; stdout is diagnostic only.

What it refuses to do
---------------------
* Run a script that asks for the network, a subprocess, or a file outside
  the job's own directory. Those are blocked at the container level (no
  network, no extra processes) and re-checked here, because a defence that
  exists in only one place is a defence that can be misconfigured away.
* Accept a legacy single-figure job that made none (v2 explicitly permits zero).
* Emit output larger than the budget.

The worker depends on the figure engine, Matplotlib and the pinned nature-figure
QA modules (including PyMuPDF for final-PDF collision checks). It must not import
the service, because the service is not in this image and must not be reachable
from here.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_SCRIPT_ERROR = 10
EXIT_NO_FIGURE = 11
EXIT_MULTIPLE_FIGURES = 12
EXIT_OUTPUT_TOO_LARGE = 14
EXIT_FORBIDDEN = 15

# Modules a figure script has no business importing. The container has no
# network and no privileges, but the *intent* to escape is worth reporting
# rather than merely surviving.
_FORBIDDEN_MODULES = (
    "socket", "http", "urllib", "requests", "ftplib", "smtplib", "telnetlib",
    "subprocess", "multiprocessing", "ctypes", "pty", "webbrowser", "asyncio",
)

_MAX_OUTPUT_BYTES = 24 * 1024 * 1024


def _bootstrap_engine_path() -> None:
    """Make the figure engine importable however this file was laid out.

    In the service the engine lives at ``src/figures`` and the service root
    is already on the path. In the sandbox image it lives beside this file
    under ``figures/``. The local backend runs a *copy* of this file, so the
    copy cannot find the engine by looking around itself; it passes the
    location in the environment instead.
    """
    candidates = []
    root = os.environ.get("CITRUS_FIGURES_ROOT", "").strip()
    if root:
        candidates.append(Path(root))
    here = Path(__file__).resolve().parent
    candidates.extend([here.parent, here])
    for candidate in candidates:
        if (candidate / "src" / "figures" / "core.py").is_file() \
                or (candidate / "figures" / "core.py").is_file():
            text = str(candidate)
            if text not in sys.path:
                sys.path.insert(0, text)
            return


def _engine():
    """Import the figure engine under whichever name is available.

    In the service the engine is ``src.figures``; in the sandbox image it is
    ``figures``. Both are handled so the worker's body is identical in the
    two places.
    """
    try:
        from src.figures import core, exporter, manifest  # noqa: PLC0415
        return core, exporter, manifest
    except ImportError:
        from figures import core, exporter, manifest  # noqa: PLC0415
        return core, exporter, manifest


def _emit(payload: dict) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, default=str))
    sys.stdout.flush()
    return EXIT_OK


# The failure messages the model reads. They name the problem and the fix,
# because a model that knows what went wrong can write a working script on
# the next attempt — and a model that only sees "exit code 11" cannot.
FAILURE_TEXT = {
    EXIT_NO_FIGURE: "[FIGURE_NO_FIGURE] 脚本没有生成任何图。请确保脚本最后留下一个 Figure（例如调用 plt.subplots() 并不调用 plt.close()）。",
    EXIT_MULTIPLE_FIGURES: "[FIGURE_MULTIPLE_FIGURES] 脚本生成了多张图，无法判断该用哪一张。请只保留一张图（用 plt.close('all') 关闭其余的）。",
    EXIT_FORBIDDEN: "[FIGURE_SCRIPT_FORBIDDEN] 脚本尝试了被禁止的操作（联网、创建子进程或导入敏感模块）。绘图脚本只能使用本地数据与绘图库。",
    EXIT_OUTPUT_TOO_LARGE: "[FIGURE_SCRIPT_OUTPUT] 脚本的输出超过体积上限。",
}


def _report(code: int, detail: str = "", script_stdout: str = "",
            nature_skill_receipt: dict | None = None) -> int:
    """Report a failure and return the exit code.

    The message goes to stdout as JSON as well as to stderr as text. The
    parent reads the JSON, because stderr also carries warnings and
    tracebacks, and "the first line of stderr" is not a reliable way to
    find the explanation. Whatever the script printed is carried along, so
    a script that printed its own diagnosis is not silenced by the failure.
    """
    text = FAILURE_TEXT.get(code, "[FIGURE_SCRIPT_ERROR] 绘图脚本执行失败。")
    if detail:
        text = f"{text}\n{detail}"
    try:
        sys.stdout.write(json.dumps({"ok": False, "code": code, "message": text,
                                     "script_stdout": str(script_stdout or "")[:4000],
                                     **({"nature_skill_receipt": nature_skill_receipt}
                                        if nature_skill_receipt is not None else {})},
                                    ensure_ascii=False))
        sys.stdout.flush()
    except Exception:
        pass
    sys.stderr.write(text + "\n")
    return code


def _guard_imports(script: str) -> str | None:
    """Refuse a script that imports a forbidden module.

    A plain textual check: it does not pretend to be a security boundary
    (the container is that), but it turns an obvious escape attempt into a
    clear refusal instead of an opaque traceback.
    """
    try:
        tree = ast.parse(script)
    except SyntaxError as exc:
        return f"脚本语法错误：{exc}"
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for name in names:
            root = str(name).split(".", 1)[0]
            if root in _FORBIDDEN_MODULES:
                return f"脚本导入了被禁止的模块「{root}」。"
    return None


def _resolve(base: Path, relative: str) -> Path:
    """Resolve a job path, refusing anything outside the job directory."""
    candidate = (base / relative).resolve()
    root = base.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"路径越界：{relative}")
    return candidate


def run(job_path: Path) -> int:
    _bootstrap_engine_path()
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"job.json 无法读取：{exc}\n")
        return EXIT_SCRIPT_ERROR

    # /work/in and /work/out are siblings: the input directory holds the job
    # and the inputs, the output directory holds what the job produces.
    in_dir = job_path.parent
    out_dir = in_dir.parent / "out"

    script_text = str(job.get("script") or "")
    if not script_text.strip():
        sys.stderr.write("作业没有提供脚本。\n")
        return EXIT_SCRIPT_ERROR

    problem = _guard_imports(script_text)
    if problem:
        return _report(EXIT_FORBIDDEN, problem)

    # The inputs the script may read, bound to names the script chooses.
    namespace: dict = {"__name__": "__citrusfig__"}
    for name, relative in (job.get("inputs") or {}).items():
        try:
            path = _resolve(in_dir, str(relative))
        except ValueError as exc:
            sys.stderr.write(str(exc) + "\n")
            return EXIT_FORBIDDEN
        if not path.is_file():
            return _report(EXIT_SCRIPT_ERROR, "脚本原始输入文件缺失，不能重放。")
        expected = (job.get("expected_input_sha256") or {}).get(str(relative))
        if expected:
            import hashlib
            with path.open("rb") as handle:
                actual = hashlib.file_digest(handle, "sha256").hexdigest()
            if actual != expected:
                return _report(EXIT_FORBIDDEN, "脚本输入快照摘要不匹配，拒绝重放。")
        namespace[str(name)] = path

    core, exporter, manifest_module = _engine()
    try:
        if core.__name__.startswith("src."):
            from src.figures.render_policy import render_mode
        else:
            from figures.render_policy import render_mode
        mode = render_mode(job.get("render_mode", "initial"))
        if mode != "initial" and not job.get("replay_overrides"):
            raise ValueError("manual render mode requires host replay")
    except ValueError as exc:
        return _report(EXIT_FORBIDDEN, str(exc))

    if job.get("output_contract"):
        namespace["load_dataset"] = _dataset_loader(tuple(namespace.values()))

    if job.get("output_contract"):
        return _run_outputs(job, script_text, namespace, out_dir, core, exporter, manifest_module)

    result = core.capture_figure(script_text, namespace=namespace, check_layout=mode == "initial")
    if result.error:
        if result.error_type == "no_figure":
            return _report(EXIT_NO_FIGURE, script_stdout=result.stdout)
        return _report(EXIT_SCRIPT_ERROR,
                       result.stdout[-2000:] + "\n" + result.error,
                       script_stdout=result.stdout)
    if result.figures_seen > 1:
        return _report(EXIT_MULTIPLE_FIGURES,
                       f"脚本生成了 {result.figures_seen} 张图。")

    figure = result.figure
    axes = list(getattr(figure, "axes", []) or []) if figure is not None else []
    rendered = manifest_module.assign_locators(
        core.RenderedFigure(figure=figure, axes=axes, qa_profile=job.get("qa_profile") or {}))
    manifest = manifest_module.build_manifest(rendered, source="script", include_qa=False)
    applied = []
    accepted = []
    if job.get("replay_overrides"):
        try:
            # The host never receives Python objects. Artist mutation happens
            # here, after capture and against this run's actual object list.
            if core.__name__.startswith("src."):
                from src.figures.overrides import validate_overrides, apply_overrides, merge_overrides
            else:
                from figures.overrides import validate_overrides, apply_overrides, merge_overrides
            incoming = validate_overrides(job.get("overrides") or [], manifest)
            base = validate_overrides(job.get("base_overrides") or [], manifest)
            accepted = validate_overrides(merge_overrides(base, incoming), manifest)
            applied = apply_overrides(rendered, manifest, accepted)
            manifest = manifest_module.build_manifest(rendered, source="script", include_qa=False)
        except Exception as exc:
            return _report(EXIT_SCRIPT_ERROR, f"样式修改被拒绝：{exc}")

    outputs = job.get("outputs") or {}
    formats = []
    for relative in outputs.values():
        suffix = Path(str(relative)).suffix.lower().lstrip(".")
        if suffix in {"svg", "png", "pdf", "tiff"} and suffix not in formats:
            formats.append(suffix)
    if not formats:
        formats = ["svg"]

    try:
        if core.__name__.startswith("src."):
            from src.figures.render_policy import export_figure
        else:
            from figures.render_policy import export_figure
        blobs, nature_receipt, render_receipt = export_figure(rendered, mode=mode, exporter=exporter,
            formats=formats, dpi=int(job.get("dpi") or 300), source=script_text)
    except Exception as exc:
        receipt = getattr(exc, "receipt", None)
        if receipt is not None:
            manifest["nature_skill_receipt"] = receipt
            out_dir.mkdir(parents=True, exist_ok=True)
            data = json.dumps(manifest, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(data) <= 4 * 1024 * 1024:
                with (out_dir / "objects.json").open("xb") as handle:
                    handle.write(data)
        return _report(EXIT_SCRIPT_ERROR, f"导出或图件审计失败：{exc}",
                       script_stdout=result.stdout, nature_skill_receipt=receipt)

    total = 0
    written: dict[str, int] = {}
    out_dir.mkdir(parents=True, exist_ok=True)
    for logical, relative in outputs.items():
        suffix = Path(str(relative)).suffix.lower().lstrip(".")
        blob = blobs.get(suffix)
        if blob is None:
            continue
        try:
            path = _resolve(out_dir, str(relative))
        except ValueError as exc:
            sys.stderr.write(str(exc) + "\n")
            return EXIT_FORBIDDEN
        if total + len(blob) > _MAX_OUTPUT_BYTES:
            return _report(EXIT_OUTPUT_TOO_LARGE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
        total += len(blob)
        written[str(logical)] = len(blob)

    manifest = manifest_module.build_manifest(rendered, source="script", include_qa=mode != "preview")
    manifest["render_receipt"] = render_receipt
    if nature_receipt is not None:
        manifest["nature_skill_receipt"] = nature_receipt
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, allow_nan=False).encode("utf-8")
    override_bytes = json.dumps(accepted, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if (len(manifest_bytes) > 4 * 1024 * 1024 or len(override_bytes) > 256 * 1024
            or total + len(manifest_bytes) + len(override_bytes) > _MAX_OUTPUT_BYTES):
        return _report(EXIT_OUTPUT_TOO_LARGE)
    (out_dir / "objects.json").write_bytes(manifest_bytes)
    if job.get("replay_overrides"):
        (out_dir / "overrides.json").write_bytes(override_bytes)

    report = {
        "ok": True,
        "stdout": result.stdout[-4000:],
        "outputs": written,
        "figure_count": result.figures_seen,
    }
    if outputs.get("manifest") == "objects.json":
        report["manifest_file"] = "objects.json"
    else:
        report["manifest"] = manifest
    if job.get("replay_overrides"):
        report.update(override_protocol=1, applied_count=len(applied))
    return _emit(report)


def _audit_final_figure(rendered, script_text, exporter):
    # Lazy import: table/report-only jobs never load upstream figure code.
    if __package__ == "figures" or exporter.__name__.startswith("figures."):
        from figures.nature_runtime import audit_fig
    else:
        from src.figures.nature_runtime import audit_fig
    return audit_fig(rendered.figure, script_text,
                     pdf_factory=lambda: exporter.export_all(rendered, formats=("pdf",))["pdf"])


def _dataset_loader(inputs):
    """Container-only helper: decode a bounded, declared read-only input."""
    allowed = {path.resolve() for path in inputs if isinstance(path, Path)}

    def load_dataset(path):
        import stat
        candidate = Path(path)
        if candidate.resolve() not in allowed:
            raise ValueError("load_dataset only accepts a declared input Path.")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        before = candidate.lstat()
        with os.fdopen(os.open(candidate, flags), "rb") as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or not os.path.samestat(before, info) or info.st_size > 64 * 1024 * 1024):
                raise ValueError("Dataset input is not a bounded regular file.")
            data = handle.read(64 * 1024 * 1024 + 1)
            after = os.fstat(handle.fileno())
            if (len(data) != info.st_size or after.st_size != info.st_size
                    or after.st_mtime_ns != info.st_mtime_ns or after.st_ctime_ns != info.st_ctime_ns):
                raise ValueError("Dataset input changed during reading.")
        try:
            from src.figures.dataset_codec import decode_frame
            from src.figures.script_outputs import strict_json
        except ImportError:
            from figures.dataset_codec import decode_frame
            from figures.script_outputs import strict_json
        return decode_frame(strict_json(data))

    return load_dataset


def _run_outputs(job, script_text, namespace, out_dir, core, exporter, manifest_module) -> int:
    """v2: execute to completion, then serialize each host-declared slot."""
    import hashlib
    if core.__name__.startswith("src."):
        from src.figures.script_outputs import (normalize_output_contract, output_slots,
                                               validate_receipt, MAX_RECEIPT_BYTES)
    else:
        from figures.script_outputs import (normalize_output_contract, output_slots,
                                           validate_receipt, MAX_RECEIPT_BYTES)
    try:
        contract = normalize_output_contract(job["output_contract"])
        target = job.get("output_key", "")
        slots = output_slots(contract, output_key=target)
        if slots != job.get("outputs") or (job.get("replay_overrides") and not target):
            raise ValueError("Output slots or replay target do not match the host contract.")
        separated = [entry for entry in contract["figures"] if "render_script" in entry
                     and (not target or entry["name"] == target)]
        for entry in separated:
            problem = _guard_imports(entry["render_script"])
            if problem:
                raise ValueError(problem)
        frozen_tables = None
        if target and separated:
            required = set(separated[0]["parents"])
            supplied = set(namespace) - {"__name__", "load_dataset"}
            if supplied != required or any(not isinstance(namespace[name], Path) for name in required):
                raise ValueError("Frozen replay requires exactly the declared parent table Paths.")
            load = _dataset_loader(tuple(namespace[name] for name in required))
            frozen_tables = {name: load(namespace[name]) for name in required}
    except (ValueError, TypeError, OSError) as exc:
        return _report(EXIT_FORBIDDEN, str(exc))
    result = core.capture_outputs(script_text, namespace=namespace,
                                  output_contract=contract, output_key=target,
                                  frozen_tables=frozen_tables)
    if result.error:
        return _report(EXIT_SCRIPT_ERROR, result.error, script_stdout=result.stdout)

    states = {key: {"status": "missing", "error": "Output was not emitted."}
              for key in slots if key != "result"}
    total = 0
    applied_count = 0
    out_dir.mkdir(parents=True, exist_ok=True)

    def error(key, exc):
        # Bound worst-case UTF-8 across all 57 states plus script stdout.
        states[key] = {"status": "error", "error": str(exc)[:160]}

    def write(key, blob, limit=_MAX_OUTPUT_BYTES):
        nonlocal total
        if len(blob) > limit or total + len(blob) + MAX_RECEIPT_BYTES > _MAX_OUTPUT_BYTES:
            raise ValueError("Output exceeds the worker byte budget.")
        # Exclusive create refuses files/symlinks planted by the script. The
        # host independently validates file handles and content digests.
        with (out_dir / slots[key]).open("xb") as handle:
            handle.write(blob)
        total += len(blob)
        states[key] = {"status": "ok", "size": len(blob),
                       "sha256": hashlib.sha256(blob).hexdigest()}

    def write_json(key, value, limit=_MAX_OUTPUT_BYTES):
        blob = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        write(key, blob, limit)

    if not target:
        for name, frame in result.tables.items():
            key = f"table:{name}"
            try:
                if core.__name__.startswith("src."):
                    from src.figures.dataset_codec import encode_frame
                else:
                    from figures.dataset_codec import encode_frame
                write_json(key, encode_frame(frame))
            except Exception as exc:
                error(key, exc)
        if result.report_emitted:
            try:
                write_json("report", result.report, 256 * 1024)
            except Exception as exc:
                error("report", exc)

    for entry in contract["figures"]:
        name = entry["name"]
        if target and target != name:
            continue
        if name not in result.figures:
            continue
        prefix = f"figure:{name}:"
        try:
            figure = result.figures[name]
            from matplotlib.figure import Figure
            if not isinstance(figure, Figure):
                raise ValueError("emit.figure requires a Matplotlib Figure.")
            # Object-oriented Figure() does not acquire an Agg canvas until
            # explicitly bound; pyplot-managed figures already have one.
            if not callable(getattr(figure.canvas, "get_renderer", None)):
                from matplotlib.backends.backend_agg import FigureCanvasAgg
                FigureCanvasAgg(figure)
            mode = job.get("render_mode", "initial")
            if mode == "initial":
                if core.__name__.startswith("src."):
                    from src.figures.styling import prepare_figure_fonts
                else:
                    from figures.styling import prepare_figure_fonts
                prepare_figure_fonts(figure)
            problems = core._tick_label_problems(figure, strict_boxes=False) if mode == "initial" else []
            if problems:
                raise ValueError("完整刻度标签重叠或超出固定画布：" + ", ".join(problems)
                                 + "；请沿用 figure-export/nature-figure 工作流修正布局，保留完整数据。")
            rendered = manifest_module.assign_locators(core.RenderedFigure(
                figure=figure, axes=list(figure.axes), qa_profile=job.get("qa_profile") or {}))
            manifest = manifest_module.build_manifest(rendered, source="script", include_qa=False)
            accepted = []
            if job.get("replay_overrides"):
                if core.__name__.startswith("src."):
                    from src.figures.overrides import validate_overrides, apply_overrides, merge_overrides
                else:
                    from figures.overrides import validate_overrides, apply_overrides, merge_overrides
                incoming = validate_overrides(job.get("overrides") or [], manifest)
                base = validate_overrides(job.get("base_overrides") or [], manifest)
                accepted = validate_overrides(merge_overrides(base, incoming), manifest)
                applied = apply_overrides(rendered, manifest, accepted)
                applied_count += len(applied)
                # Manual edits are not an aesthetic acceptance gate.
                if mode == "initial" and core._tick_label_problems(figure, strict_boxes=False):
                    raise ValueError("初始图件完整刻度标签重叠或超出固定画布。")
            manifest = manifest_module.build_manifest(rendered, source="script", include_qa=False)
            frozen_receipt = result.frozen_render_receipts.get(name)
            if frozen_receipt is not None:
                manifest["frozen_render_receipt"] = frozen_receipt
            try:
                if core.__name__.startswith("src."):
                    from src.figures.render_policy import export_figure
                else:
                    from figures.render_policy import export_figure
                blobs, nature_receipt, render_receipt = export_figure(rendered, mode=mode, exporter=exporter,
                    formats=entry["formats"], dpi=int(job.get("dpi") or 300),
                    source=entry.get("render_script", script_text), allow_format_failure=True)
            except Exception as exc:
                receipt = getattr(exc, "receipt", None)
                if receipt is not None:
                    manifest["nature_skill_receipt"] = receipt
                    write_json(prefix + "manifest", manifest, 4 * 1024 * 1024)
                    if job.get("replay_overrides"):
                        write_json(prefix + "overrides", accepted, 256 * 1024)
                raise
            manifest = manifest_module.build_manifest(rendered, source="script", include_qa=mode != "preview")
            if frozen_receipt is not None:
                manifest["frozen_render_receipt"] = frozen_receipt
            manifest["render_receipt"] = render_receipt
            if nature_receipt is not None:
                manifest["nature_skill_receipt"] = nature_receipt
            write_json(prefix + "manifest", manifest, 4 * 1024 * 1024)
            if job.get("replay_overrides"):
                write_json(prefix + "overrides", accepted, 256 * 1024)
        except Exception as exc:
            for key in slots:
                if key.startswith(prefix) and states[key]["status"] != "ok":
                    error(key, exc)
            continue
        for fmt in entry["formats"]:
            key = prefix + fmt
            try:
                if fmt not in blobs:
                    detail = render_receipt.get("export_errors", {}).get(fmt, {})
                    raise ValueError(detail.get("error") or "Requested export was not produced.")
                write(key, blobs[fmt])
            except Exception as exc:
                error(key, exc)

    partial = any(state["status"] == "error" or
                  (state["status"] == "missing" and key != "report" and not key.endswith(":overrides"))
                  for key, state in states.items())
    receipt = {"version": 2, "ok": True, "status": "partial" if partial else "ok",
               "job_id": job.get("job_id", ""), "output_key": target, "slots": states,
               "stdout": result.stdout[-4000:], "figure_count": len(result.figures),
               "applied_count": applied_count}
    data = json.dumps(receipt, ensure_ascii=False, allow_nan=False).encode("utf-8")
    try:
        validate_receipt(data, contract, job_id=job.get("job_id", ""), output_key=target)
        with (out_dir / "result.json").open("xb") as handle:
            handle.write(data)
    except (OSError, ValueError, TypeError) as exc:
        return _report(EXIT_FORBIDDEN, "Output receipt could not be committed: " + str(exc))
    # stdout is diagnostic only. The host collects the bounded fixed receipt.
    return _emit({"ok": True, "version": 2, "status": receipt["status"], "result_file": "result.json"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CitrusScalor figure sandbox worker")
    parser.add_argument("--job", required=True, help="path to job.json")
    args = parser.parse_args(argv)
    os.environ.setdefault("MPLBACKEND", "Agg")
    try:
        return run(Path(args.job))
    except Exception as exc:  # noqa: BLE001 - the worker reports, never crashes
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        return EXIT_SCRIPT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())

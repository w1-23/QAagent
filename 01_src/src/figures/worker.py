"""The sandbox worker: the only place that touches an untrusted script.

This file runs **inside** the container. It is the boundary: the script's
process, its filesystem view and its network access are all confined to the
container that runs this file.

What it does
------------
1. Reads the job description (which script, which inputs, which outputs).
2. Runs the script with Matplotlib in the Agg backend, capturing the figure
   it produces.
3. Writes the figure out in the requested formats.
4. Writes a small JSON result to stdout.

What it refuses to do
---------------------
* Run a script that asks for the network, a subprocess, or a file outside
  the job's own directory. Those are blocked at the container level (no
  network, no extra processes) and re-checked here, because a defence that
  exists in only one place is a defence that can be misconfigured away.
* Produce a figure from a script that made none.
* Emit output larger than the budget.

The worker depends only on the figure engine and on Matplotlib. It must not
import the service, because the service is not in this image and must not be
reachable from here.
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


def _report(code: int, detail: str = "", script_stdout: str = "") -> int:
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
                                     "script_stdout": str(script_stdout or "")[:4000]},
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

    result = core.capture_figure(script_text, namespace=namespace)
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
    manifest = manifest_module.build_manifest(rendered, source="script")
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
            manifest = manifest_module.build_manifest(rendered, source="script")
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
        blobs = exporter.export_all(rendered, formats=tuple(formats),
                                    dpi=int(job.get("dpi") or 300))
    except Exception as exc:
        sys.stderr.write(f"导出失败：{exc}\n")
        return EXIT_SCRIPT_ERROR

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

    manifest = manifest_module.build_manifest(rendered, source="script")
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

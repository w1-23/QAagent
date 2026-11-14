"""One trusted declarative render, in a disposable resource-limited process.

The only job input is resolved JSON. This worker never resolves account files,
opens a database, accepts Python source, unpickles objects, or runs a sandbox.
The script sandbox worker remains a separate execution boundary.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys

MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_OBSERVATION_BYTES = 16 * 1024 * 1024
MAX_RENDER_BYTES = 48 * 1024 * 1024
MAX_POINTS = 200_000


def _limits(memory_mb: int, cpu_sec: int) -> None:
    if os.name == "posix":
        import resource
        for kind, amount in ((resource.RLIMIT_AS, memory_mb * 1024 * 1024),
                             (resource.RLIMIT_CPU, cpu_sec),
                             (resource.RLIMIT_FSIZE, MAX_RENDER_BYTES),
                             (resource.RLIMIT_CORE, 0)):
            resource.setrlimit(kind, (amount, amount))
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        maximum = 128 if hard == resource.RLIM_INFINITY else min(128, hard)
        resource.setrlimit(resource.RLIMIT_NOFILE, (maximum, maximum))


def decode_resolved(raw: dict):
    """Reconstruct only the engine's closed, resolved data vocabulary."""
    from dataclasses import fields
    from src.figures.spec import ResolvedFigure, Series, StyleSpec, CHART_TYPES, SpecError, parse_qa_profile, _parse_layout
    budget = [0]

    def decode(value, depth=0):
        if (not isinstance(value, dict) or set(value) - {item.name for item in fields(ResolvedFigure)}
                or depth > 1 or value.get("chart", "bar") not in CHART_TYPES):
            raise SpecError("invalid_resolved", "已解析的图件合同无效。")
        value = dict(value)
        children = value.get("panels") or []
        if not isinstance(children, list) or len(children) > 12:
            raise SpecError("invalid_resolved", "子图数量无效。")
        if children:
            if depth or any(not isinstance(child, dict) or not child.get("panel_id") for child in children):
                raise SpecError("invalid_resolved", "子图身份无效。")
            if len({child["panel_id"] for child in children}) != len(children):
                raise SpecError("invalid_resolved", "子图身份不能重复。")
            value["layout"] = _parse_layout(value.get("layout"), len(children))
        elif value.get("layout"):
            raise SpecError("invalid_resolved", "空的子图布局无效。")
        series = value.get("series") or []
        if not isinstance(series, list) or len(series) > 60:
            raise SpecError("invalid_resolved", "系列数量无效。")
        decoded = []
        for item in series:
            if not isinstance(item, dict) or set(item) - {f.name for f in fields(Series)}:
                raise SpecError("invalid_resolved", "系列合同无效。")
            item = dict(item)
            for key in ("values", "errors", "x_values", "row_indices"):
                values = item.get(key) or []
                if not isinstance(values, list):
                    raise SpecError("invalid_resolved", "系列数据必须是数组。")
                budget[0] += len(values)
                item[key] = tuple(tuple(row) for row in values) if key == "row_indices" else tuple(values)
            decoded.append(Series(**item))
        for key in ("x_values", "y_values"):
            values = value.get(key) or []
            if not isinstance(values, list):
                raise SpecError("invalid_resolved", "数据必须是数组。")
            budget[0] += len(values)
            value[key] = tuple(values)
        if budget[0] > MAX_POINTS * 8:
            raise SpecError("resolved_too_large", "已解析的数据超过绘图预算。")
        style = value.get("style") or {}
        if not isinstance(style, dict) or set(style) - {f.name for f in fields(StyleSpec)}:
            raise SpecError("invalid_resolved", "样式合同无效。")
        value["style"] = StyleSpec(**style)
        value["series"] = tuple(decoded)
        value["panels"] = tuple(decode(child, depth + 1) for child in children)
        value["qa_profile"] = parse_qa_profile(value.get("qa_profile")) if value.get("qa_profile") else {}
        return ResolvedFigure(**value)

    return decode(raw)


def _observations(rendered):
    """JSON observations of actual drawn data; never transport Artist objects."""
    from matplotlib.collections import PathCollection
    from matplotlib.lines import Line2D
    import numpy as np
    series = []
    for gid, entry in rendered.locators.items():
        if entry.get("role") != "series":
            continue
        artist = entry.get("artist")
        record = {"gid": gid, "axes_index": entry.get("axes_index", 0)}
        if isinstance(artist, PathCollection):
            record.update(offsets=np.asarray(artist.get_offsets(), dtype=float).tolist(),
                          linewidths=np.asarray(artist.get_linewidths(), dtype=float).tolist(),
                          sizes=np.asarray(artist.get_sizes(), dtype=float).tolist(),
                          facecolors=np.asarray(artist.get_facecolors(), dtype=float).tolist())
            legend = artist.axes.get_legend()
            if legend is not None:
                matches = [index for index, text in enumerate(legend.get_texts()) if text.get_text() == artist.get_label()]
                if len(matches) == 1:
                    index = matches[0]
                    handle = legend.legend_handles[index]
                    record["legend"] = {"label": legend.get_texts()[index].get_text(),
                                        "sizes": np.asarray(getattr(handle, "get_sizes", lambda: [])(), dtype=float).tolist()}
        elif isinstance(artist, Line2D):
            record.update(x=np.asarray(artist.get_xdata(orig=False), dtype=float).tolist(),
                          y=np.asarray(artist.get_ydata(orig=False), dtype=float).tolist())
        elif entry.get("members") and all(hasattr(member, "get_height") for member in entry["members"]):
            record["heights"] = [float(member.get_height()) for member in entry["members"]]
        series.append(record)
    execution = {"pid": os.getpid(), "service_modules": [name for name in sys.modules
                 if name == "src.config" or name.startswith(("src.session", "src.api", "src.research", "src.auth"))]}
    if os.name == "posix":
        import resource
        execution["limits"] = {name: list(resource.getrlimit(kind)) for name, kind in (
            ("address_space", resource.RLIMIT_AS), ("cpu", resource.RLIMIT_CPU), ("file_size", resource.RLIMIT_FSIZE))}
    elif os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        kernel.IsProcessInJob.restype = wintypes.BOOL
        inside = wintypes.BOOL()
        if not kernel.IsProcessInJob(wintypes.HANDLE(-1), None, ctypes.byref(inside)):
            raise OSError("cannot verify owned job membership")
        execution["windows_job_bound"] = bool(inside.value)
    return {"series": series, "execution": execution}


def _write_json(path: Path, payload, limit=MAX_JSON_BYTES):
    blob = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(blob) > limit:
        raise ValueError("render JSON budget exceeded")
    path.write_bytes(blob)
    return len(blob)


def run(job_path: Path) -> int:
    # -I ignores PYTHONPATH and the working directory. Only the installed,
    # trusted engine next to this file is added; never add the job directory.
    engine_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(engine_root))
    raw = job_path.read_bytes()
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("render input budget exceeded")
    job = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    if (not isinstance(job, dict) or set(job) != {"version", "resolved", "formats", "dpi", "overrides", "base_overrides", "memory_mb", "cpu_sec"}
            or job["version"] != 1):
        raise ValueError("unknown trusted render job")
    _limits(int(job["memory_mb"]), int(job["cpu_sec"]))
    from src.figures.core import build_resolved_figure
    from src.figures.manifest import build_manifest
    from src.figures.exporter import export_all
    from src.figures.overrides import validate_overrides, merge_overrides, apply_overrides

    rendered = build_resolved_figure(decode_resolved(job["resolved"]))
    manifest = build_manifest(rendered, source="spec")
    incoming = validate_overrides(job["overrides"], manifest)
    base = validate_overrides(job["base_overrides"], manifest)
    accepted = validate_overrides(merge_overrides(base, incoming), manifest)
    changed = apply_overrides(rendered, manifest, accepted)
    formats = job["formats"]
    if (not isinstance(formats, list) or not formats or len(formats) > 3
            or len(set(formats)) != len(formats) or any(fmt not in {"svg", "png", "pdf"} for fmt in formats)
            or type(job["dpi"]) is not int or not 72 <= job["dpi"] <= 1200):
        raise ValueError("invalid trusted output contract")
    blobs = export_all(rendered, formats=tuple(formats), dpi=job["dpi"])
    manifest = build_manifest(rendered, source="spec")
    out = job_path.parent / "out"
    out.mkdir()
    total = 0
    for fmt, blob in blobs.items():
        if not isinstance(blob, bytes) or not blob or len(blob) > 24 * 1024 * 1024:
            raise ValueError("render format budget exceeded")
        total += len(blob)
        if total > MAX_RENDER_BYTES:
            raise ValueError("render budget exceeded")
        (out / f"figure.{fmt}").write_bytes(blob)
    total += _write_json(out / "objects.json", manifest)
    total += _write_json(out / "edits.json", {"accepted": accepted, "changed": changed})
    total += _write_json(out / "observations.json", _observations(rendered), MAX_OBSERVATION_BYTES)
    if total > 64 * 1024 * 1024:
        raise ValueError("total render budget exceeded")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    args = parser.parse_args()
    try:
        return run(args.job)
    except Exception as exc:
        # Failure receipts are bounded and contain no filesystem traceback.
        path = args.job.parent / "out" / "error.json"
        try:
            path.parent.mkdir(exist_ok=True)
            _write_json(path, {"code": str(getattr(exc, "code", "render_failed")),
                              "message": str(exc)[:1200]})
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Short-lived, process-local preview memoization; validation belongs before hits.

No cancellation promise is made for a running sandbox. Concurrent equal previews
share a bounded striped lock, then re-check authorization, source integrity and
version. Saves invalidate entries and never use preview bytes as export bytes.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable

PROTOCOL_VERSION = 2
_TTL = 30.0
_MAX_ENTRIES = 24
_MAX_BYTES = 48 * 1024 * 1024
_CACHE: OrderedDict[str, tuple[float, int, tuple[int, str], dict]] = OrderedDict()
_LOCK = threading.RLock()
_STRIPES = [threading.Lock() for _ in range(16)]


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def environment_key() -> dict:
    from src.config import settings
    from importlib.metadata import PackageNotFoundError, version
    names = ("FIGURES_SANDBOX_BACKEND", "FIGURES_SANDBOX_IMAGE", "FIGURES_SANDBOX_IMAGE_SHA256",
             "FIGURES_SANDBOX_RUNTIME", "FIGURES_SANDBOX_ALLOW_LOCAL", "FIGURES_SANDBOX_WSL_DISTRO",
             "FIGURES_DPI", "FIGURES_SANDBOX_MEMORY_MB", "FIGURES_SANDBOX_CPUS")
    values = {name: getattr(settings, name, None) for name in names}
    # Mutable container tags do not identify an environment. Disable reuse for
    # those script backends rather than assuming the tag still names one image.
    image = str(values.get("FIGURES_SANDBOX_IMAGE") or "")
    import re
    values["immutable_image"] = (str(values.get("FIGURES_SANDBOX_BACKEND") or "").lower()
                                  in {"docker", "wsl-docker"} and bool(re.fullmatch(
                                      r"(?:[A-Za-z0-9./:_-]+@)?sha256:[a-f0-9]{64}", image)))
    values["python"] = sys.version
    values["executable"] = sys.executable
    packages = {}
    for name in ("matplotlib", "numpy", "pandas"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    values["packages"] = packages
    from matplotlib import font_manager
    values["fonts"] = [(entry.fname, Path(entry.fname).stat().st_mtime_ns, Path(entry.fname).stat().st_size)
                       for entry in font_manager.fontManager.ttflist if Path(entry.fname).is_file()]
    # A rolling source update invalidates even an already-running service.
    values["engine"] = [(path.name, path.stat().st_mtime_ns, path.stat().st_size)
                        for path in sorted(Path(__file__).parent.glob("*.py"))]
    return values


def invalidate(owner: int, figure_id: str) -> None:
    with _LOCK:
        for key in [key for key, entry in _CACHE.items() if entry[2] == (int(owner), str(figure_id))]:
            _CACHE.pop(key, None)


def render_preview(project: dict, owner: int, edits: Any, *, validate: Callable[[], None],
                   render: Callable[[], dict], is_script: bool) -> dict:
    environment = environment_key()
    identity = (int(owner), str(project["figure_id"]))
    key = _digest({"protocol": PROTOCOL_VERSION, "owner": owner, "project": identity[1],
                   "version": project.get("render_version"), "source": project.get("source_json"),
                   "refs": project.get("data_refs_json"), "saved": project.get("overrides_json"),
                   "environment": environment, "edits": edits})
    reusable = not is_script or environment["immutable_image"]
    # Script execution can be random; this is only the same disposable draft,
    # never a promise that a future save will reproduce these bytes exactly.
    with _STRIPES[int(key[:8], 16) % len(_STRIPES)]:
        try:
            validate()
        except Exception:
            invalidate(*identity)
            raise
        now = time.monotonic()
        with _LOCK:
            for stale in [k for k, v in _CACHE.items() if now - v[0] >= _TTL]:
                _CACHE.pop(stale, None)
            found = _CACHE.get(key) if reusable else None
            if found is not None:
                _CACHE.move_to_end(key)
                return deepcopy(found[3])
        result = render()
        validate()
        if reusable:
            size = len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            if size <= _MAX_BYTES:
                with _LOCK:
                    _CACHE[key] = (time.monotonic(), size, identity, deepcopy(result))
                    _CACHE.move_to_end(key)
                    while len(_CACHE) > _MAX_ENTRIES or sum(item[1] for item in _CACHE.values()) > _MAX_BYTES:
                        _CACHE.popitem(last=False)
        return result

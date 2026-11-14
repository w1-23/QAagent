"""Bounded byte-only facade for trusted declarative production renders.

Every accepted job gets a fresh child, so Matplotlib state and native memory
are recycled after one render. This is not the untrusted script sandbox. No
Python source, account paths, provenance, credentials or databases enter its
JSON contract, and there is no in-process fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from src.core.cancel import raise_if_cancelled

DEFAULT_TIMEOUT_SEC = 30.0
MEMORY_MB = 2048
MAX_JOB_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
# Two executing children and at most six queued requests, per service process.
_SLOTS = threading.BoundedSemaphore(2)
_ADMISSION = threading.BoundedSemaphore(8)


class TrustedRenderError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


@dataclass(frozen=True)
class RenderedBytes:
    blobs: dict[str, bytes]
    manifest: dict[str, Any]
    overrides: list[dict[str, Any]]
    changed: list[str]
    worker_pid: int
    observations: dict[str, Any]


class _WindowsJob:
    """Owned JobObject: commit/CPU/active-process limits and kill-on-close."""
    def __init__(self, memory_mb, cpu_sec):
        import ctypes
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]
        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                             "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Counters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel, self.handle = kernel, kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "cannot create render job object")
        limits = Extended()
        # PROCESS_TIME | ACTIVE_PROCESS | PROCESS_MEMORY | JOB_MEMORY | KILL_ON_JOB_CLOSE
        limits.BasicLimitInformation.LimitFlags = 0x2 | 0x8 | 0x100 | 0x200 | 0x2000
        limits.BasicLimitInformation.PerProcessUserTimeLimit = int(cpu_sec * 10_000_000)
        limits.BasicLimitInformation.ActiveProcessLimit = 1
        limits.ProcessMemoryLimit = limits.JobMemoryLimit = memory_mb * 1024 * 1024
        if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            self.close()
            raise OSError(error, "cannot limit render job object")

    def attach_and_resume(self, process):
        import ctypes
        from ctypes import wintypes
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise OSError(ctypes.get_last_error(), "cannot assign owned render process")
        resume = ctypes.WinDLL("ntdll").NtResumeProcess
        resume.argtypes, resume.restype = [wintypes.HANDLE], ctypes.c_long
        if resume(int(process._handle)) != 0:
            raise OSError("cannot resume owned render process")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _environment(root: Path) -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(root), "USERPROFILE": str(root),
           "TEMP": str(root), "TMP": str(root), "MPLCONFIGDIR": str(root / "mpl"),
           "MPLBACKEND": "Agg", "LANG": "C.UTF-8", "OPENBLAS_NUM_THREADS": "1",
           "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"}
    if os.name == "nt":
        for key in ("SYSTEMROOT", "WINDIR"):
            if os.environ.get(key):
                env[key] = os.environ[key]
    return env


def _spawn(command, *, root, stdout, stderr):
    return subprocess.Popen(command, cwd=str(root), env=_environment(root), stdin=subprocess.DEVNULL,
                            stdout=stdout, stderr=stderr, close_fds=True,
                            start_new_session=os.name == "posix",
                            creationflags=(0x4 | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0)  # CREATE_SUSPENDED


def _stop(process):
    try:
        if os.name == "posix":
            # A resource limit can kill the leader while a font-discovery
            # descendant is still alive. Always terminate our private session's
            # group, even when poll() already reaped the leader.
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def _collect(root, outputs):
    # Reuse the same bounded no-link, no-reparse, immutable-file collector as
    # the script path. No script backend, source path or execution is involved.
    from src.figures.sandbox import _read_outputs, SandboxJob, SandboxError
    try:
        return _read_outputs(root, SandboxJob(outputs=outputs))
    except SandboxError as exc:
        raise TrustedRenderError("worker_protocol_invalid", str(exc)) from exc


def _decode_json(blob, *, limit=MAX_MANIFEST_BYTES):
    if len(blob) > limit:
        raise TrustedRenderError("render_too_large", "绘图工作进程的 JSON 输出超过预算。")
    try:
        return json.loads(blob, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeError, ValueError) as exc:
        raise TrustedRenderError("worker_protocol_invalid", "绘图工作进程返回了无效 JSON。") from exc


def render_resolved(resolved, *, overrides=None, existing=None, formats=("svg", "png", "pdf"),
                    dpi=None, timeout_sec=DEFAULT_TIMEOUT_SEC) -> RenderedBytes:
    """Render numeric ResolvedFigure in a fresh owned child; return only bytes.

    Timeout includes bounded admission waiting and process startup. Cancellation
    is the actual request ContextVar token, checked while waiting and executing.
    """
    if not math.isfinite(timeout_sec) or not 0 < timeout_sec <= 120:
        raise TrustedRenderError("invalid_budget", "绘图时间预算无效。")
    raise_if_cancelled("figure_render_admission")
    if not _ADMISSION.acquire(blocking=False):
        raise TrustedRenderError("render_busy", "绘图队列已满，请稍后重试。")
    acquired = False
    deadline = time.monotonic() + timeout_sec
    try:
        formats = tuple(dict.fromkeys(formats))
        if not formats or any(fmt not in {"svg", "png", "pdf"} for fmt in formats):
            raise TrustedRenderError("unsupported_format", "绘图输出格式无效。")
        job = {"version": 1, "resolved": resolved.as_dict(), "formats": list(formats),
               "dpi": int(dpi if dpi is not None else resolved.style.dpi), "overrides": overrides if overrides is not None else [],
               "base_overrides": existing if existing is not None else [], "memory_mb": MEMORY_MB, "cpu_sec": max(1, math.ceil(timeout_sec))}
        try:
            blob = json.dumps(job, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError) as exc:
            raise TrustedRenderError("invalid_resolved", "已解析的数据不能安全传入绘图工作进程。") from exc
        if len(blob) > MAX_JOB_BYTES:
            raise TrustedRenderError("render_too_large", "已解析的绘图数据超过输入预算。")
        while not acquired:
            raise_if_cancelled("figure_render_queue")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TrustedRenderError("render_timeout", "绘图工作进程等待超时。")
            acquired = _SLOTS.acquire(timeout=min(.05, remaining))
        with tempfile.TemporaryDirectory(prefix="citrus-declarative-") as temporary:
            root = Path(temporary)
            job_path = root / "job.json"
            job_path.write_bytes(blob)
            process, owned_job = None, None
            try:
                if os.name == "nt":
                    owned_job = _WindowsJob(MEMORY_MB, max(1, math.ceil(timeout_sec)))
                # Diagnostics use the bounded error receipt, never a PIPE or
                # an unbounded log file (including on Windows).
                process = _spawn([sys.executable, "-I", "-X", "utf8", str(Path(__file__).with_name("trusted_worker.py")),
                                  "--job", str(job_path)], root=root,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if owned_job:
                    owned_job.attach_and_resume(process)
                while process.poll() is None:
                    raise_if_cancelled("figure_render_worker")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TrustedRenderError("render_timeout", "绘图工作进程超过时间上限，已终止。")
                    try:
                        process.wait(timeout=min(.05, remaining))
                    except subprocess.TimeoutExpired:
                        pass
                raise_if_cancelled("figure_render_collect")
                if time.monotonic() >= deadline:
                    raise TrustedRenderError("render_timeout", "绘图工作进程超过时间上限，已终止。")
                if process.returncode != 0:
                    try:
                        error = _decode_json(_collect(root, {"error": "error.json"})["error"])
                    except TrustedRenderError:
                        error = {"code": "render_failed", "message": "绘图工作进程异常退出或超出资源预算。"}
                    raise TrustedRenderError(str(error.get("code") or "render_failed"), str(error.get("message") or "绘图失败。"))
                outputs = {fmt: f"figure.{fmt}" for fmt in formats}
                outputs.update(manifest="objects.json", edits="edits.json", observations="observations.json")
                data = _collect(root, outputs)
                manifest, edits = _decode_json(data["manifest"]), _decode_json(data["edits"])
                observations = _decode_json(data["observations"], limit=16 * 1024 * 1024)
                if (not isinstance(manifest, dict) or not isinstance(manifest.get("objects"), list)
                        or any(not isinstance(item, dict) or not isinstance(item.get("gid"), str) for item in manifest["objects"])
                        or not isinstance(edits, dict) or set(edits) != {"accepted", "changed"}
                        or not isinstance(edits["accepted"], list) or not isinstance(edits["changed"], list)
                        or not isinstance(observations, dict) or not isinstance(observations.get("series"), list)):
                    raise TrustedRenderError("worker_protocol_invalid", "绘图工作进程返回了无效对象合同。")
                for fmt in formats:
                    signature = {"svg": b"<?xml", "png": b"\x89PNG\r\n\x1a\n", "pdf": b"%PDF-"}[fmt]
                    if not data[fmt].startswith(signature):
                        raise TrustedRenderError("worker_protocol_invalid", "绘图工作进程返回了无效格式文件。")
                raise_if_cancelled("figure_render_return")
                if time.monotonic() >= deadline:
                    raise TrustedRenderError("render_timeout", "绘图输出采集超过时间上限。")
                return RenderedBytes({fmt: data[fmt] for fmt in formats}, manifest, edits["accepted"],
                                     edits["changed"], process.pid, observations)
            except OSError as exc:
                raise TrustedRenderError("worker_unavailable", "无法启动受限的声明式绘图工作进程。") from exc
            finally:
                if owned_job:
                    owned_job.close()
                if process is not None:
                    _stop(process)
    finally:
        if acquired:
            _SLOTS.release()
        _ADMISSION.release()

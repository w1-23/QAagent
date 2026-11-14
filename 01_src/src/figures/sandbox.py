"""Running an untrusted Python script: the sandbox abstraction.

The problem
-----------
A figure script is code. Code written by a model, in service of a user's
request, on a server that serves many accounts. If that code runs in the
application process it can read the API keys, read another account's files,
open a socket, and take the machine with it. None of that is acceptable on a
shared server.

The rule
--------
A script runs in a **throwaway container** with no network, no privileges, a
read-only root filesystem, a non-root user, and hard limits on memory, CPU,
processes and wall-clock time. The container is destroyed when the job ends.

Fail-closed
-----------
When no isolated backend is configured, the script path is **not available**. It does
not silently fall back to running the script in a local subprocess, because
"it worked on my machine" is exactly the mistake that turns a lab server
into an open door. The declarative path — the one where the *service* writes
the plotting code from a validated specification — has no such problem and
remains available everywhere.

LocalBackend
------------
A local subprocess exists for tests and for a developer who explicitly asks
for it. It is **not a security boundary** and the module says so wherever it
is used. It runs with a scrubbed environment, its own temporary directory,
and a wall-clock kill; that is hygiene, not isolation.

Why the service does not simply sandbox in-process
--------------------------------------------------
Because in-process means "the app process's own filesystem, network and
memory". A container is the smallest boundary that actually separates one
account's script from every other account's data and from the server itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

# Exit codes. The worker's contract mirrors these, so the same failure reads
# the same way whether it happened in the container or on the developer's
# machine.
EXIT_OK = 0
EXIT_SCRIPT_ERROR = 10
EXIT_NO_FIGURE = 11
EXIT_MULTIPLE_FIGURES = 12
EXIT_OUTPUT_TOO_LARGE = 14
EXIT_FORBIDDEN = 15
EXIT_TIMEOUT = 124
EXIT_OOM = 137

# How long a script gets between the polite TERM and the unavoidable KILL.
# Without it, a script that installs a SIGTERM handler would outlive its
# budget; with it, such a script is still stopped, and the 137 that follows
# is attributed to the wall clock rather than to memory (see ``_interpret``).
TERM_GRACE_SEC = 5

# Failure kinds the caller can act on.
FAILURE_TIMEOUT = "timeout"
FAILURE_OOM = "oom"
FAILURE_FORBIDDEN = "forbidden"
FAILURE_POLICY = "policy"
FAILURE_RUNTIME = "runtime"
FAILURE_OUTPUT = "output"

# The message the model sees for each failure. Kept short and in the shape
# the repo's other tools use (``[TAG] explanation``), because the model reads
# these strings and acts on them.
FAILURE_MESSAGES = {
    FAILURE_TIMEOUT: "[FIGURE_SCRIPT_TIMEOUT] 绘图脚本超过时间上限仍未完成，已终止。",
    FAILURE_OOM: "[FIGURE_SCRIPT_OOM] 绘图脚本超出内存上限，已终止。",
    FAILURE_FORBIDDEN: "[FIGURE_SCRIPT_FORBIDDEN] 绘图脚本尝试了被禁止的操作（联网、创建子进程或越界访问文件）。",
    FAILURE_POLICY: "[FIGURE_SANDBOX_UNAVAILABLE] 当前环境没有配置绘图脚本沙箱，无法执行自定义绘图脚本；请改用声明式图件工具。",
    FAILURE_OUTPUT: "[FIGURE_SCRIPT_OUTPUT] 绘图脚本的输出超出体积上限。",
    FAILURE_RUNTIME: "[FIGURE_SCRIPT_ERROR] 绘图脚本执行失败。",
}

# The default budget for one job.
DEFAULT_TIMEOUT_SEC = 120
DEFAULT_MEMORY_MB = 1024
DEFAULT_CPUS = "1.0"
DEFAULT_PIDS = 128
MAX_SCRIPT_BYTES = 200_000
MAX_OUTPUT_BYTES = 24 * 1024 * 1024
MAX_TOTAL_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_FILES = 8


class SandboxUnavailable(RuntimeError):
    """The sandbox is not configured, so scripts cannot be run."""


class SandboxError(RuntimeError):
    """The sandbox refused or failed the job."""


@dataclass(frozen=True)
class SandboxJob:
    """One unit of work for the sandbox.

    ``script`` is the code to run. ``inputs`` maps a *variable name* to the
    file name the script will see it under; ``sources`` maps that file name to
    the host path it is copied from. ``outputs`` maps a logical name to the
    file name the job is expected to produce.

    Paths are always given as *container* file names (``data.csv``,
    ``figure.svg``), never as host paths, so the job description means the
    same thing to the worker inside the container and to the host that
    launched it.
    """
    script: str = ""
    job_id: str = ""
    inputs: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    memory_mb: int = DEFAULT_MEMORY_MB
    cpus: str = DEFAULT_CPUS
    pids: int = DEFAULT_PIDS
    image: str = ""
    runtime: str = ""
    extra_env: dict[str, str] = field(default_factory=dict)
    overrides: list[dict[str, Any]] = field(default_factory=list)
    base_overrides: list[dict[str, Any]] = field(default_factory=list)
    replay_overrides: bool = False
    dpi: int = 300
    qa_profile: dict[str, Any] = field(default_factory=dict)
    expected_input_sha256: dict[str, str] = field(default_factory=dict)
    image_sha256: str = ""

    def as_json(self) -> str:
        return json.dumps({
            "job_id": self.job_id,
            "script": self.script,
            "inputs": dict(self.inputs),
            "outputs": dict(self.outputs),
            "timeout_sec": int(self.timeout_sec),
            "overrides": self.overrides,
            "base_overrides": self.base_overrides,
            "replay_overrides": self.replay_overrides,
            "dpi": int(self.dpi),
            "qa_profile": self.qa_profile,
            "expected_input_sha256": self.expected_input_sha256,
        }, ensure_ascii=False)


@dataclass(frozen=True)
class SandboxResult:
    """What a job produced."""
    ok: bool = False
    exit_code: int = EXIT_SCRIPT_ERROR
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    failure_kind: str = ""
    message: str = ""
    outputs: dict[str, bytes] = field(default_factory=dict)
    backend: str = ""

    def describe(self) -> str:
        """A short, actionable sentence for the model."""
        if self.ok:
            return ""
        if self.message:
            return self.message
        return FAILURE_MESSAGES.get(self.failure_kind, FAILURE_MESSAGES[FAILURE_RUNTIME])


class SandboxRunner(Protocol):
    """What the service expects of a sandbox."""

    name: str

    def available(self) -> bool:
        """Whether this runner can execute a job right now."""

    def run(self, job: SandboxJob) -> SandboxResult:
        """Execute one job and report what happened."""


# ── The Docker backend ────────────────────────────────────────────────────

class DockerBackend:
    """Run a job as a one-shot container.

    The container is created with every restriction the job needs and is
    removed when it exits (``--rm``). Nothing is shared between jobs: no
    writable layer, no volume except the two job directories, no network, no
    credentials.
    """

    name = "docker"

    def __init__(self, *, image: str = "", docker_binary: str = "docker",
                 runtime: str = "", network: str = "none") -> None:
        self.image = str(image or "")
        self.docker_binary = str(docker_binary or "docker")
        self.runtime = str(runtime or "")
        self.network = str(network or "none")

    def available(self) -> bool:
        if not self.image:
            return False
        if shutil.which(self.docker_binary) is None:
            return False
        try:
            completed = subprocess.run(
                [self.docker_binary, "version", "--format", "{{.Server.Version}}"],
                capture_output=True, timeout=20,
                env=_scrubbed_env())
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0

    def _container_name(self, job: SandboxJob) -> str:
        """The name the container is given, so it can be killed if it hangs."""
        return f"citrusqa-fig-{job.job_id or uuid.uuid4().hex[:12]}"

    def build_command(self, job: SandboxJob, *, workdir: Path,
                      name: str = "") -> list[str]:
        """The exact ``docker run`` argument list.

        Exposed separately from :meth:`run` so the flags can be asserted in a
        test without a Docker daemon: the flags *are* the security boundary,
        and they should be checked, not assumed.
        """
        command = [
            self.docker_binary, "run", "--rm",
            "--name", name or self._container_name(job),
            "--network", self.network,
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--pids-limit", str(int(job.pids or DEFAULT_PIDS)),
            "--memory", f"{int(job.memory_mb or DEFAULT_MEMORY_MB)}m",
            "--cpus", str(job.cpus or DEFAULT_CPUS),
            "--user", "65534:65534",
            "--workdir", "/work",
            "--mount", f"type=bind,src={workdir / 'in'},dst=/work/in,readonly",
            "--mount", f"type=bind,src={workdir / 'out'},dst=/work/out",
            "--env", "MPLBACKEND=Agg",
            "--label", "citrusqa.figure.sandbox=1",
        ]
        if self.runtime:
            command.extend(["--runtime", self.runtime])
        # The image's ENTRYPOINT already runs the worker, and Docker appends a
        # command to the entrypoint. Naming the worker again would execute
        # ``python worker.py python worker.py --job ...`` and argparse would
        # exit 2 on the stray positionals — every job failing for a reason
        # that never appears in the script's own output.
        #
        # So the entrypoint is overridden instead, by ``timeout``: the wall
        # clock budget has to be enforced *inside* the container. Killing the
        # docker CLI (see ``run``) is not enough; the client is not the
        # server, and the container would keep running until it was killed by
        # something else.
        #
        # The signal is TERM, not KILL. ``timeout --signal=KILL`` does not
        # exit 124: it dies of the signal itself, so the container reports
        # 137 — which is also how an out-of-memory kill looks, and the job
        # was just described as running out of memory. TERM gives the
        # documented 124. ``--kill-after`` still guarantees an end for a
        # script that traps or ignores TERM; that fallback does report 137,
        # and ``_interpret`` separates the two by the clock.
        budget = int(job.timeout_sec or DEFAULT_TIMEOUT_SEC)
        command.extend([
            "--entrypoint", "/usr/bin/timeout",
            job.image or self.image,
            "--signal=TERM", "--kill-after", f"{TERM_GRACE_SEC}s", f"{budget}s",
            "python", "-I", "/opt/citrusfig/worker.py", "--job", "/work/in/job.json",
        ])
        return command

    def _kill(self, name: str) -> None:
        """Stop a container whose client we already gave up on.

        ``subprocess.run`` timing out kills the CLI, not the container: the
        daemon keeps that container running with the CPU and memory this job
        was granted, until somebody removes it. The proxy allows this call
        (``CONTAINERS=1``), so it is made as a backstop under the
        container-side ``timeout``.
        """
        try:
            subprocess.run([self.docker_binary, "kill", name],
                           capture_output=True, timeout=15, env=_scrubbed_env())
        except (OSError, subprocess.SubprocessError):
            pass

    def run(self, job: SandboxJob) -> SandboxResult:
        if not self.image:
            raise SandboxUnavailable("未配置沙箱镜像。")
        name = self._container_name(job)
        with _job_directory() as workdir:
            _write_job_files(workdir, job)
            command = self.build_command(job, workdir=workdir, name=name)
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    command, capture_output=True, timeout=int(job.timeout_sec) + 30,
                    env=_scrubbed_env())
            except subprocess.TimeoutExpired:
                self._kill(name)
                return SandboxResult(
                    ok=False, exit_code=EXIT_TIMEOUT, timed_out=True,
                    failure_kind=FAILURE_TIMEOUT,
                    message=FAILURE_MESSAGES[FAILURE_TIMEOUT],
                    duration_ms=int((time.monotonic() - started) * 1000),
                    backend=self.name)
            duration = int((time.monotonic() - started) * 1000)
            stdout = completed.stdout.decode("utf-8", "replace")
            stderr = completed.stderr.decode("utf-8", "replace")
            outputs: dict[str, bytes] = {}
            if completed.returncode == EXIT_OK:
                try:
                    outputs = _read_outputs(workdir, job)
                except SandboxError as exc:
                    return SandboxResult(
                        ok=False, exit_code=EXIT_OUTPUT_TOO_LARGE,
                        failure_kind=FAILURE_OUTPUT, message=str(exc),
                        duration_ms=duration, backend=self.name)
            return _interpret(completed.returncode, stdout, stderr, outputs,
                              duration, self.name,
                              timeout_sec=int(job.timeout_sec or DEFAULT_TIMEOUT_SEC))


# ── The local backend (development and tests only) ────────────────────────

class LocalBackend:
    """Run a job as a local subprocess.

    **This is not a security boundary.** The subprocess can reach the
    network, read the machine's files and consume the machine's memory. It
    exists so the worker protocol can be tested without a Docker daemon, and
    it is reachable only when the operator asks for it by name.
    """
    name = "local"

    def available(self) -> bool:
        return True

    def build_command(self, job: SandboxJob, *, workdir: Path) -> list[str]:
        return [
            sys.executable, "-I", "-X", "utf8", str(workdir / "worker.py"),
            "--job", str(workdir / "in" / "job.json"),
        ]

    def run(self, job: SandboxJob) -> SandboxResult:
        with _job_directory() as workdir:
            _write_job_files(workdir, job)
            command = self.build_command(job, workdir=workdir)
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    command, capture_output=True,
                    timeout=int(job.timeout_sec or DEFAULT_TIMEOUT_SEC),
                    cwd=str(workdir), env=_local_env())
            except subprocess.TimeoutExpired:
                return SandboxResult(
                    ok=False, exit_code=EXIT_TIMEOUT, timed_out=True,
                    failure_kind=FAILURE_TIMEOUT,
                    message=FAILURE_MESSAGES[FAILURE_TIMEOUT],
                    duration_ms=int((time.monotonic() - started) * 1000),
                    backend=self.name)
            duration = int((time.monotonic() - started) * 1000)
            stdout = completed.stdout.decode("utf-8", "replace")
            stderr = completed.stderr.decode("utf-8", "replace")
            outputs: dict[str, bytes] = {}
            if completed.returncode == EXIT_OK:
                try:
                    outputs = _read_outputs(workdir, job)
                except SandboxError as exc:
                    return SandboxResult(
                        ok=False, exit_code=EXIT_OUTPUT_TOO_LARGE,
                        failure_kind=FAILURE_OUTPUT, message=str(exc),
                        duration_ms=duration, backend=self.name)
            return _interpret(completed.returncode, stdout, stderr, outputs,
                              duration, self.name,
                              timeout_sec=int(job.timeout_sec or DEFAULT_TIMEOUT_SEC))


# ── Choosing a backend ────────────────────────────────────────────────────
def select_backend(*, backend: str = "", image: str = "", runtime: str = "",
                   allow_local: bool = False, image_sha256: str = "") -> SandboxRunner | None:
    """Pick the sandbox to use, or ``None`` when scripts cannot be run.

    The mapping is deliberate:

    ``off`` / ``""``
        Scripts cannot run. The tool that runs them is not offered.
    ``docker``
        Scripts run in a container. If Docker is not actually usable, this
        returns ``None`` rather than degrading.
    ``apptainer``
        Scripts run in a pinned local SIF inside a systemd user cgroup. The
        actual namespace and resource policies must pass a restricted probe.
    ``local``
        Scripts run in a local subprocess. Only when the operator asked for
        it explicitly, and only as a development convenience.
    """
    name = str(backend or "").strip().lower()
    if name in {"", "off", "none", "disabled"}:
        return None
    if name == "docker":
        runner = DockerBackend(image=image, runtime=runtime)
        return runner if runner.available() else None
    if name == "apptainer":
        from .apptainer import ApptainerBackend
        runner = ApptainerBackend(image=image, runtime=runtime, image_sha256=image_sha256)
        return runner if runner.available() else None
    if name == "local":
        return LocalBackend() if allow_local else None
    return None


# ── Shared plumbing ───────────────────────────────────────────────────────

def _scrubbed_env() -> dict[str, str]:
    """A minimal environment for a child process.

    The child must not inherit the service's API keys, its database paths,
    or anything else that turns a script into a way to read the server. Only
    the few variables a Python process needs to start are passed.

    ``DOCKER_HOST`` is the one exception, and it is passed through because
    the service reaches the Docker daemon through a socket proxy that listens
    on TCP. Without it the daemon lookup would fail and the script path
    would silently close. It names a daemon, not a credential.
    """
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp",
        "LANG": "C.UTF-8",
    }
    docker_host = os.environ.get("DOCKER_HOST", "").strip()
    if docker_host:
        env["DOCKER_HOST"] = docker_host
    return env


def _local_env() -> dict[str, str]:
    """The environment for the local (development-only) backend.

    The same scrubbed environment, plus the one fact the worker needs to find
    the figure engine: where the source tree is. That path names the
    developer's own checkout, which is exactly why this backend is not a
    security boundary and is never used in production.
    """
    env = _scrubbed_env()
    if sys.platform == "win32":
        # Winsock (also initialized by SciPy/Seaborn imports) needs the OS
        # installation directory. These are runtime paths, not credentials.
        for name in ("SYSTEMROOT", "WINDIR"):
            if os.environ.get(name):
                env[name] = os.environ[name]
        # pathlib.home() on Windows uses USERPROFILE rather than HOME.
        env["USERPROFILE"] = env["HOME"]
    env["MPLBACKEND"] = "Agg"
    env["MPLCONFIGDIR"] = env.get("HOME", "/tmp")
    env["CITRUS_FIGURES_ROOT"] = str(Path(__file__).resolve().parents[2])
    # The daemon address is not for the script: it names a way to reach the
    # Docker daemon, and a script has no business knowing where it is.
    env.pop("DOCKER_HOST", None)
    # The child's stderr is read back as UTF-8, so it must be written as
    # UTF-8 — otherwise a Chinese error message arrives as mojibake.
    # ``python -I`` ignores PYTHONIOENCODING, so the flag is passed on the
    # command line instead.
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


class _job_directory:
    """A scratch directory for one job, removed when the job ends.

    The job's inputs and outputs live here and nowhere else. The directory is
    created under the system temp area — never inside the source tree — and
    it is removed even when the job fails.

    Where the directory is created matters when the Docker backend is used.
    The daemon resolves a bind mount's source path on the *host*, so the job
    directory has to sit on a path the host can also see.
    ``CITRUSQA_FIGURE_JOB_DIR`` names such a path; the deployment mounts one
    volume at the same place inside and outside the container. When it is not
    set the system temp area is used, which is correct for a native
    deployment where the daemon and the service share a filesystem.
    """

    def __init__(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="citrusqa-figure-job-",
                                          dir=_job_root()))

    def __enter__(self) -> Path:
        (self.path / "in").mkdir(parents=True, exist_ok=True)
        (self.path / "out").mkdir(parents=True, exist_ok=True)
        # The sandbox container runs as uid 65534 while the service runs as
        # its own user. The output directory therefore has to be writable by
        # that other uid, or every job fails on the last step with a
        # permission error that says nothing about the real cause.
        _make_world_writable(self.path / "out")
        return self.path

    def __exit__(self, *exc_info: Any) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def _job_root() -> str | None:
    """The directory the job scratch space is created in, if one is named."""
    root = os.environ.get("CITRUSQA_FIGURE_JOB_DIR", "").strip()
    if not root:
        return None
    try:
        path = Path(root)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)
    except OSError:
        return None


def _make_world_writable(path: Path) -> None:
    """Give the sandbox's unprivileged uid write access to one directory."""
    try:
        os.chmod(path, 0o777)
    except OSError:
        pass


def _write_job_files(workdir: Path, job: SandboxJob) -> None:
    """Write the job description, the script and the worker into the job area.

    The worker is copied next to the job directory because the local backend
    runs it as a plain script; the Docker backend runs the copy inside the
    image instead.
    """
    incoming = workdir / "in"
    payload = job.as_json()
    if len(str(job.script).encode("utf-8")) > MAX_SCRIPT_BYTES or len(payload.encode("utf-8")) > 1024 * 1024:
        raise SandboxError("脚本或修改请求超过体积上限。")
    (incoming / "job.json").write_text(payload, encoding="utf-8")
    (incoming / "script.py").write_text(str(job.script or ""), encoding="utf-8")
    for name, relative in (job.inputs or {}).items():
        target = incoming / Path(relative).name
        source = Path((job.sources or {}).get(name, ""))
        if source.is_file():
            _copy_job_input(source, target, expected_sha256=str(
                job.expected_input_sha256.get(str(relative), "")))
        elif str(relative) in job.expected_input_sha256:
            raise SandboxError("原始输入文件缺失。")
    worker = Path(__file__).resolve().parent / "worker.py"
    if worker.is_file():
        shutil.copyfile(worker, workdir / "worker.py")


def _copy_job_input(source: Path, target: Path, *, expected_sha256: str = "") -> None:
    """Pin the opened regular file and validate the copied bytes, not its name."""
    limit = 64 * 1024 * 1024
    try:
        before = source.lstat()
        flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
                 | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        descriptor = os.open(source, flags)
        with os.fdopen(descriptor, "rb") as incoming:
            opened = os.fstat(incoming.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                    or not os.path.samestat(before, opened) or opened.st_size > limit):
                raise SandboxError("输入必须是体积受限且无链接的普通文件。")
            digest = hashlib.sha256()
            total = 0
            with target.open("xb") as outgoing:
                while chunk := incoming.read(min(1024 * 1024, limit - total + 1)):
                    total += len(chunk)
                    if total > limit:
                        raise SandboxError("输入文件超过体积上限。")
                    digest.update(chunk)
                    outgoing.write(chunk)
            after = os.fstat(incoming.fileno())
            if (total != opened.st_size or after.st_size != opened.st_size
                    or after.st_mtime_ns != opened.st_mtime_ns
                    or after.st_ctime_ns != opened.st_ctime_ns):
                raise SandboxError("输入文件在复制期间被修改。")
            if expected_sha256 and digest.hexdigest() != expected_sha256.lower():
                raise SandboxError("原始输入快照 SHA-256 不匹配。")
    except OSError as exc:
        raise SandboxError("无法安全复制输入文件。") from exc


def _read_outputs(workdir: Path, job: SandboxJob) -> dict[str, bytes]:
    """Collect a bounded set of regular files after the worker has stopped.

    POSIX opens relative to a pinned directory with O_NOFOLLOW. On platforms
    without it, validate the opened handle against lstat before reading any
    bytes. Reparse points and multiply linked files are never accepted.
    These checks limit collection; they are not a writable-volume disk quota.
    """
    outgoing = workdir / "out"
    requested = job.outputs or {}
    if len(requested) > MAX_OUTPUT_FILES:
        raise SandboxError("输出文件数量超过上限。")
    outputs: dict[str, bytes] = {}
    total = 0
    directory_fd = None

    def unsafe(info: os.stat_result) -> bool:
        return (stat.S_ISLNK(info.st_mode)
                or bool(getattr(info, "st_file_attributes", 0)
                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)))

    try:
        directory_info = outgoing.lstat()
        if unsafe(directory_info) or not stat.S_ISDIR(directory_info.st_mode):
            raise SandboxError("输出目录不是受管普通目录。")
        if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
            directory_fd = os.open(outgoing, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            if not os.path.samestat(directory_info, os.fstat(directory_fd)):
                raise SandboxError("输出目录在采集期间被替换。")
        seen: set[str] = set()
        for logical, relative in requested.items():
            name = str(relative)
            if (not name or name in {".", ".."} or any(c in name for c in "/\\:")
                    or name.casefold() in seen):
                raise SandboxError("输出文件名无效或重复。")
            seen.add(name.casefold())
            path = outgoing / name
            before = (os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                      if directory_fd is not None else path.lstat())
            if unsafe(before) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise SandboxError("输出必须是无链接的普通文件。")
            limit = min(MAX_OUTPUT_BYTES, MAX_TOTAL_OUTPUT_BYTES - total)
            if before.st_size > limit:
                raise SandboxError("输出文件体积超过上限。")
            flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
                     | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            descriptor = (os.open(name, flags, dir_fd=directory_fd)
                          if directory_fd is not None else os.open(path, flags))
            with os.fdopen(descriptor, "rb") as stream:
                opened = os.fstat(stream.fileno())
                if (unsafe(opened) or not stat.S_ISREG(opened.st_mode)
                        or opened.st_nlink != 1 or not os.path.samestat(before, opened)):
                    raise SandboxError("输出文件在采集期间被替换。")
                if directory_fd is None and not os.path.samestat(directory_info, outgoing.lstat()):
                    raise SandboxError("输出目录在采集期间被替换。")
                data = stream.read(limit + 1)
                after = os.fstat(stream.fileno())
                if (len(data) > limit or len(data) != opened.st_size
                        or after.st_size != opened.st_size or after.st_mtime_ns != opened.st_mtime_ns):
                    raise SandboxError("输出超过上限或在采集期间仍被修改。")
            total += len(data)
            outputs[logical] = data
    except OSError as exc:
        raise SandboxError("输出文件缺失或无法安全读取。") from exc
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
    return outputs


def _interpret(exit_code: int, stdout: str, stderr: str,
               outputs: dict[str, bytes], duration_ms: int,
               backend: str, *, timeout_sec: int = 0) -> SandboxResult:
    """Turn a finished process into a result the caller can act on.

    The worker's own stderr already carries a message written for the model
    ("the script produced no figure" and what to do about it). That text is
    preserved as the result's message, so the model sees the worker's
    explanation rather than a generic failure label.
    """
    if exit_code == EXIT_OK:
        return SandboxResult(ok=True, exit_code=EXIT_OK, stdout=stdout[:8000],
                             stderr=stderr[:4000], duration_ms=duration_ms,
                             outputs=outputs, backend=backend)
    kind = FAILURE_RUNTIME
    if exit_code == EXIT_TIMEOUT:
        kind = FAILURE_TIMEOUT
    elif exit_code == EXIT_OOM:
        # 137 is SIGKILL, and two different parties send it: the kernel, when
        # the job hit its memory limit, and ``timeout --kill-after``, when a
        # script ignored the TERM that ended its budget. The clock separates
        # them — the fallback kill cannot happen before the budget is spent.
        if timeout_sec and duration_ms >= timeout_sec * 1000:
            kind = FAILURE_TIMEOUT
        else:
            kind = FAILURE_OOM
    elif exit_code == EXIT_FORBIDDEN:
        kind = FAILURE_FORBIDDEN
    elif exit_code == EXIT_OUTPUT_TOO_LARGE:
        kind = FAILURE_OUTPUT
    # The worker reports its own explanation as JSON on stdout; stderr also
    # carries warnings and tracebacks, so the JSON is the reliable source.
    message = ""
    script_stdout = ""
    try:
        payload = json.loads(stdout)
        if isinstance(payload, dict):
            message = str(payload.get("message") or "")
            script_stdout = str(payload.get("script_stdout") or "")
    except (TypeError, ValueError):
        message = ""
    if not message:
        detail = stderr.strip()
        message = detail.splitlines()[0][:400] if detail else ""
    if not message:
        message = FAILURE_MESSAGES.get(kind, FAILURE_MESSAGES[FAILURE_RUNTIME])
    if script_stdout:
        message = f"{message}\n{script_stdout[-1500:]}"
    return SandboxResult(
        ok=False, exit_code=exit_code, stdout=stdout[:8000], stderr=stderr[:4000],
        duration_ms=duration_ms, failure_kind=kind, message=message,
        outputs=outputs, backend=backend, timed_out=(kind == FAILURE_TIMEOUT))


__all__ = [
    "DEFAULT_CPUS", "DEFAULT_MEMORY_MB", "DEFAULT_TIMEOUT_SEC", "DockerBackend",
    "EXIT_FORBIDDEN", "EXIT_MULTIPLE_FIGURES", "EXIT_NO_FIGURE", "EXIT_OOM",
    "EXIT_OK", "EXIT_OUTPUT_TOO_LARGE", "EXIT_SCRIPT_ERROR", "EXIT_TIMEOUT",
    "FAILURE_FORBIDDEN", "FAILURE_OOM", "FAILURE_OUTPUT", "FAILURE_POLICY",
    "FAILURE_RUNTIME", "FAILURE_TIMEOUT", "LocalBackend", "MAX_SCRIPT_BYTES",
    "SandboxError", "SandboxJob", "SandboxResult", "SandboxRunner",
    "SandboxUnavailable", "select_backend",
]

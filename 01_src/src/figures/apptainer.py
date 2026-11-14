"""Fail-closed Apptainer execution under a bounded systemd user service.

The service owns the whole cgroup, including processes that change session or
process group. The guard verifies the kernel limits before starting Apptainer.
No application tree, credentials or home directory is mounted into the image.
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid

from src.core.cancel import raise_if_cancelled

from .sandbox import (
    EXIT_OUTPUT_TOO_LARGE, EXIT_TIMEOUT, FAILURE_OUTPUT, FAILURE_MESSAGES,
    MAX_OUTPUT_FILES, MAX_SCRIPT_BYTES, SandboxError, SandboxJob, SandboxResult,
    SandboxUnavailable, _interpret, _job_directory, _read_outputs, _write_job_files,
)

MAX_LOG_BYTES = 64 * 1024
MAX_INPUT_BYTES = 64 * 1024 * 1024
ENGINE_FILES = (
    "__init__.py", "spec.py", "styling.py", "core.py", "runtime.py",
    "manifest.py", "overrides.py", "exporter.py",
)
SAFE_PATH = "/usr/local/bin:/usr/bin:/bin"

# This runs on the host, before untrusted code exists. Verifying the actual
# controller files avoids systemd's best-effort behavior on undelegated hosts.
_CGROUP_GUARD = r'''
import math, os, pathlib, sys
memory, pids, cpus = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])
if os.getuid() == 0:
    raise SystemExit("sandbox requires an unprivileged service user")
status = pathlib.Path('/proc/self/status').read_text()
fields = dict(line.split(':', 1) for line in status.splitlines() if ':' in line)
if fields.get('NoNewPrivs', '').strip() != '1' or int(fields['CapEff'].strip(), 16):
    raise SystemExit("privilege restrictions are not enforced")
entries = pathlib.Path('/proc/self/cgroup').read_text().splitlines()
relative = next((line[3:] for line in entries if line.startswith('0::')), None)
if relative is None or '..' in pathlib.PurePosixPath(relative).parts:
    raise SystemExit("cgroup v2 is required")
root = pathlib.Path('/sys/fs/cgroup') / relative.lstrip('/')
def limit(name):
    value = (root / name).read_text().strip()
    if value == 'max':
        raise SystemExit(name + " is unlimited")
    return int(value)
if limit('memory.max') > memory or limit('memory.swap.max') != 0 or limit('pids.max') > pids:
    raise SystemExit("memory, swap or PID limits are not enforced")
quota, period = (root / 'cpu.max').read_text().split()
if quota == 'max' or int(quota) / int(period) > cpus + 0.00001:
    raise SystemExit("CPU quota is not enforced")
# Go's setuid runtime may scrub GOMAXPROCS. Affinity survives that stage and
# bounds thread initialization on hosts with hundreds of logical processors.
allowed = sorted(os.sched_getaffinity(0))
os.sched_setaffinity(0, allowed[:max(1, math.ceil(cpus))])
os.execv(sys.argv[4], sys.argv[4:])
'''

# available() exercises the same restricted launch as a real job. A missing
# namespace, runtime flag or denied network namespace must not look available.
_PROBE = r'''
import os, pathlib, socket
assert os.getuid() != 0
status = pathlib.Path('/proc/self/status').read_text()
f = dict(line.split(':', 1) for line in status.splitlines() if ':' in line)
assert f['NoNewPrivs'].strip() == '1' and int(f['CapEff'].strip(), 16) == 0
assert os.readlink('/proc/self/ns/net') != os.environ['CITRUS_HOST_NETNS']
assert os.readlink('/proc/self/ns/pid') != os.environ['CITRUS_HOST_PIDNS']
assert os.readlink('/proc/1/ns/pid') == os.readlink('/proc/self/ns/pid')
interfaces = socket.if_nameindex()
assert all(name == 'lo' for _, name in interfaces)
mounts = pathlib.Path('/proc/mounts').read_text().splitlines()
root = next(row.split() for row in mounts if row.split()[1] == '/')
assert 'ro' in root[3].split(',')
assert not pathlib.Path(os.environ['CITRUS_HOST_SOURCE']).exists()
assert not pathlib.Path('/sys/fs/cgroup/cgroup.controllers').exists()
print('CITRUS_APPTAINER_ISOLATED')
'''


def _host_env() -> dict[str, str]:
    uid = os.getuid()
    return {
        "PATH": SAFE_PATH, "HOME": "/tmp", "LANG": "C.UTF-8",
        "GOMAXPROCS": "2", "GOMEMLIMIT": "128MiB",
        "XDG_RUNTIME_DIR": f"/run/user/{uid}",
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus",
    }


def _filename(value: str) -> bool:
    return bool(value and value not in {".", ".."}
                and not any(c in value for c in "/\\:\x00\n\r"))


class ApptainerBackend:
    name = "apptainer"

    def __init__(self, *, image: str = "", image_sha256: str = "",
                 runtime: str = "") -> None:
        self.image = str(image or "")
        self.image_sha256 = str(image_sha256 or "").lower()
        # For this backend runtime names an executable, never arbitrary flags.
        self.runtime = str(runtime or "/usr/bin/apptainer")

    def _validate_image(self) -> Path:
        path = Path(self.image)
        if (not path.is_absolute() or path.suffix != ".sif"
                or any(c in self.image for c in ":,\n\r\x00")
                or not re.fullmatch(r"[0-9a-f]{64}", self.image_sha256)):
            raise SandboxUnavailable("Apptainer requires an absolute local .sif and its SHA-256 pin.")
        try:
            # Reject symlinks in every component, not just the final name.
            if any(part.is_symlink() for part in (path, *path.parents)):
                raise SandboxUnavailable("The SIF path must not contain symlinks.")
            before = path.stat()
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_mode & 0o022):
                raise SandboxUnavailable("The SIF must be a regular file, not group/world writable.")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                if not os.path.samestat(before, os.fstat(stream.fileno())):
                    raise SandboxUnavailable("SIF changed during validation.")
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                after = os.fstat(stream.fileno())
            if (digest.hexdigest() != self.image_sha256
                    or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    or not os.path.samestat(after, path.stat())):
                raise SandboxUnavailable("SIF hash does not match the configured pin.")
        except OSError as exc:
            raise SandboxUnavailable("Cannot safely read the pinned SIF.") from exc
        return path

    def _validate_host(self) -> None:
        if sys.platform != "linux" or os.getuid() == 0:
            raise SandboxUnavailable("Apptainer requires Linux and an unprivileged service user.")
        if (not Path(self.runtime).is_absolute()
                or shutil.which(self.runtime, path=SAFE_PATH) is None
                or any(shutil.which(tool, path=SAFE_PATH) is None
                       for tool in ("systemd-run", "systemctl", "env"))):
            raise SandboxUnavailable("Apptainer and the systemd user service tools are required.")

    def _validate_job(self, job: SandboxJob) -> None:
        try:
            cpus = float(job.cpus)
            if (not math.isfinite(cpus) or not 0.01 <= cpus <= 64
                    or not 1 <= int(job.timeout_sec) <= 3600
                    or not 64 <= int(job.memory_mb) <= 65536
                    or not 8 <= int(job.pids) <= 1024):
                raise ValueError
        except (ValueError, TypeError, OverflowError) as exc:
            raise SandboxError("Invalid sandbox resource budget.") from exc
        if ((job.image and job.image != self.image)
                or (job.runtime and job.runtime != self.runtime)
                or (job.image_sha256 and job.image_sha256.lower() != self.image_sha256)
                or job.extra_env):
            raise SandboxError("A job cannot override the pinned image, runtime or environment.")
        if (not job.script.strip() or len(job.script.encode("utf-8")) > MAX_SCRIPT_BYTES
                or len(job.outputs) > MAX_OUTPUT_FILES):
            raise SandboxError("Invalid script or output count.")
        names = list(job.inputs.values())
        output_names = list(job.outputs.values())
        if (any(not isinstance(value, str) or not _filename(value) for value in names + output_names)
                or len(set(name.casefold() for name in names)) != len(names)
                or len(set(name.casefold() for name in output_names)) != len(output_names)
                or any(name.casefold() in {"job.json", "script.py"} for name in names)):
            raise SandboxError("Invalid or duplicate job filename.")
        total = 0
        for name in job.inputs:
            source = Path(job.sources.get(name, ""))
            try:
                info = source.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise SandboxError("Inputs must be regular files without links.")
                total += info.st_size
            except OSError as exc:
                raise SandboxError("Missing job input.") from exc
        if total > MAX_INPUT_BYTES:
            raise SandboxError("Job inputs exceed the size budget.")

    def build_command(self, job: SandboxJob, *, workdir: Path,
                      unit: str, probe: bool = False) -> list[str]:
        """Exact argv: systemd owns the lifetime; Apptainer owns namespaces."""
        self._validate_job(job)
        memory = int(job.memory_mb) * 1024 * 1024
        env = _host_env()
        # env -i also removes variables held by the user manager, including
        # APPTAINER_BIND*, APPTAINERENV_*, SINGULARITY* and loader variables.
        command = [
            "/usr/bin/systemd-run", "--user", "--quiet", "--wait", "--pipe",
            "--collect", "--unit", unit,
            "--property", "Type=exec", "--property", "KillMode=control-group",
            "--property", "SendSIGKILL=yes", "--property", "TimeoutStopSec=2s",
            "--property", f"RuntimeMaxSec={int(job.timeout_sec)}s",
            "--property", f"MemoryMax={memory}", "--property", "MemorySwapMax=0",
            "--property", f"TasksMax={int(job.pids)}",
            "--property", f"CPUQuota={float(job.cpus) * 100:g}%",
            "--property", "NoNewPrivileges=yes",
            "--property", "LimitCORE=0", "--property", "UMask=0077",
            "--", "/usr/bin/env", "-i",
            *(f"{key}={value}" for key, value in env.items()),
            sys.executable, "-I", "-c", _CGROUP_GUARD,
            str(memory), str(int(job.pids)), str(float(job.cpus)),
            self.runtime, "exec", "--containall", "--cleanenv", "--no-home",
            "--no-mount", "hostfs,cwd,home,bind-paths,sys", "--net", "--network", "none",
            "--pid", "--ipc", "--no-privs", "--drop-caps", "ALL",
            "--cwd", "/work/out",
            "--bind", f"{workdir / 'engine'}:/opt/citrusfig:ro",
            "--bind", f"{workdir / 'in'}:/work/in:ro",
            "--bind", f"{workdir / 'out'}:/work/out:rw",
            "--env", "MPLBACKEND=Agg", "--env", "MPLCONFIGDIR=/tmp/mpl",
            "--env", "HOME=/tmp", "--env", "OMP_NUM_THREADS=1",
            "--env", "OPENBLAS_NUM_THREADS=1", "--env", "MKL_NUM_THREADS=1",
        ]
        if probe:
            command += ["--env", f"CITRUS_HOST_NETNS={os.readlink('/proc/self/ns/net')}",
                        "--env", f"CITRUS_HOST_PIDNS={os.readlink('/proc/self/ns/pid')}",
                        "--env", f"CITRUS_HOST_SOURCE={Path(__file__).resolve()}"]
        command += [self.image, "python", "-I", "-X", "utf8"]
        command += (["-c", _PROBE] if probe else [
            "/opt/citrusfig/worker.py", "--job", "/work/in/job.json"])
        return command

    @staticmethod
    def _stage_engine(workdir: Path) -> None:
        target = workdir / "engine" / "src" / "figures"
        target.mkdir(parents=True)
        source = Path(__file__).resolve().parent
        for name in ENGINE_FILES:
            shutil.copyfile(source / name, target / name)
        shutil.copyfile(source / "worker.py", workdir / "engine" / "worker.py")
        # Same user as the service, with a private scratch tree.
        (workdir / "out").chmod(0o700)
        for path in (workdir, workdir / "engine", workdir / "in", workdir / "out"):
            if sys.platform == "linux" and any(c in str(path) for c in ":,\n\r\x00"):
                raise SandboxError("Scratch path cannot be represented as a safe bind mount.")

    @staticmethod
    def _stop_unit(unit: str) -> None:
        for action in (["kill", "--kill-whom=all", "--signal=KILL", unit],
                       ["stop", unit]):
            try:
                subprocess.run(["/usr/bin/systemctl", "--user", *action],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=5, env=_host_env())
            except (OSError, subprocess.SubprocessError):
                pass

    def _execute(self, command: list[str], *, workdir: Path,
                 unit: str, timeout: int) -> tuple[int, str, str, int]:
        started = time.monotonic()
        streams = [bytearray(), bytearray()]
        overflow = threading.Event()
        process = None
        readers: list[threading.Thread] = []

        def drain(stream, index):
            try:
                while chunk := stream.read(4096):
                    remaining = MAX_LOG_BYTES - len(streams[index])
                    streams[index].extend(chunk[:max(0, remaining)])
                    if len(chunk) > remaining:
                        overflow.set()
            finally:
                stream.close()

        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       cwd=workdir, env=_host_env(), start_new_session=True)
            for index, stream in enumerate((process.stdout, process.stderr)):
                thread = threading.Thread(target=drain, args=(stream, index), daemon=True)
                thread.start()
                readers.append(thread)
            while process.poll() is None:
                raise_if_cancelled("apptainer_wait")
                if overflow.wait(0.05):
                    returncode = EXIT_OUTPUT_TOO_LARGE
                    break
                if time.monotonic() - started > timeout:
                    returncode = EXIT_TIMEOUT
                    break
            else:
                returncode = process.returncode
        finally:
            # Stop the client first so it cannot submit a new unit after the
            # cleanup request, then kill the entire service cgroup. This also
            # runs for KeyboardInterrupt/cancellation and runtime exceptions.
            try:
                if process is not None:
                    if process.returncode is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    process.wait(timeout=5)
            finally:
                self._stop_unit(unit)
                for reader in readers:
                    reader.join(timeout=5)
        if overflow.is_set():
            returncode = EXIT_OUTPUT_TOO_LARGE
        return (returncode, *(bytes(value).decode("utf-8", "replace") for value in streams),
                int((time.monotonic() - started) * 1000))

    def available(self) -> bool:
        try:
            self._validate_host()
            self._validate_image()
            with _job_directory() as workdir:
                self._stage_engine(workdir)
                job = SandboxJob(script="pass", timeout_sec=20, image_sha256=self.image_sha256)
                unit = f"citrusqa-figure-probe-{uuid.uuid4().hex}.service"
                command = self.build_command(job, workdir=workdir, unit=unit, probe=True)
                code, stdout, _, _ = self._execute(command, workdir=workdir, unit=unit, timeout=25)
                return code == 0 and stdout.strip() == "CITRUS_APPTAINER_ISOLATED"
        except (OSError, ValueError, SandboxError, SandboxUnavailable, subprocess.SubprocessError):
            return False

    def run(self, job: SandboxJob) -> SandboxResult:
        raise_if_cancelled("apptainer_start")
        self._validate_host()
        self._validate_image()
        self._validate_job(job)
        with _job_directory() as workdir:
            _write_job_files(workdir, job)
            self._stage_engine(workdir)
            unit = f"citrusqa-figure-{uuid.uuid4().hex}.service"
            command = self.build_command(job, workdir=workdir, unit=unit)
            code, stdout, stderr, duration = self._execute(
                command, workdir=workdir, unit=unit, timeout=int(job.timeout_sec) + 5)
            raise_if_cancelled("apptainer_collect")
            outputs = {}
            if code == 0:
                try:
                    outputs = _read_outputs(workdir, job)
                except SandboxError as exc:
                    return SandboxResult(exit_code=EXIT_OUTPUT_TOO_LARGE,
                                         failure_kind=FAILURE_OUTPUT, message=str(exc),
                                         duration_ms=duration, backend=self.name)
            # systemd-run can report RuntimeMaxSec as 1 or a signal status;
            # the unit clock establishes that this failure spent its budget.
            if code != 0 and code != EXIT_OUTPUT_TOO_LARGE and duration >= int(job.timeout_sec) * 1000:
                code = EXIT_TIMEOUT
            result = _interpret(code, stdout, stderr, outputs, duration, self.name,
                                timeout_sec=int(job.timeout_sec))
            if code == EXIT_OUTPUT_TOO_LARGE:
                return SandboxResult(exit_code=code, failure_kind=FAILURE_OUTPUT,
                                     message=FAILURE_MESSAGES[FAILURE_OUTPUT],
                                     duration_ms=duration, backend=self.name)
            return result

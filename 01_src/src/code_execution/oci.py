"""Offline Python OCI adapter; only the controller grants execution authority.

``prepare`` generates a persistable handle WITHOUT creating a container. Persist
it before ``start``. The trusted caller provides an immutable, per-execution
``staging_root/execution_id`` directory containing exactly the request snapshots
at their logical paths with the leading slash removed, plus an ``input`` dir.
Keep that directory immutable and retained until stopped/removed is confirmed.

The supervisor stays alive after its child exits: collect BEFORE kill/remove,
otherwise tmpfs outputs are lost. ``retained`` is a child-process observation,
NOT proof the container or all descendants stopped. Only daemon inspection can
confirm ``stopped``. On unknown start/transport completion the controller must
retain its reservation and reconcile; this module never retries a launch or
executes user Python on the host. Concurrent calls need controller fencing.

Images are host-provisioned immutable Linux image IDs with Python at the fixed
configured path. No image build/pull, model options, network capability, GPU,
shell, figure AST policy, or host fallback is supported. A trusted loader may
materialize a published task-owned pure-Python overlay as an additional read-only
mount; arbitrary host environment directories and pip URLs are never accepted.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import time
from typing import Protocol
import uuid

from .protocol import ExecutionRequest, bounded_integer, identifier

_LABEL = "org.citrusscholar.code."
_IMAGE = re.compile(r"sha256:[a-f0-9]{64}")
_CONTROL_BYTES = 256 * 1024


class OCIError(RuntimeError):
    """Invalid host configuration, metadata, snapshots, or untrusted output."""


class UnsupportedExecution(OCIError):
    """This backend cannot enforce the requested execution policy."""


def safe_linux_path(value: str) -> str:
    """Validate the raw path BEFORE Docker's CSV mount parser sees it."""
    if (not isinstance(value, str) or not value.startswith("/") or value == "/"
            or len(value) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or any(c in value for c in ",\\\"':")
            or any(p in {"", ".", ".."} for p in value.split("/")[1:])):
        raise OCIError("unsafe_linux_mount_path")
    return value


def _host_directory(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise OCIError("absolute_staging_directory_required")
    # resolve alone would erase evidence of a link/reparse point.
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OCIError("linked_staging_directory")
    if not path.is_dir():
        raise OCIError("staging_directory_required")
    return path.resolve(strict=True)


@dataclass(frozen=True)
class OCIConfig:
    image_id: str
    staging_root: str
    mode: str = "native"
    docker_path: str = "/usr/bin/docker"
    python_path: str = "/usr/local/bin/python"
    wsl_executable: str = "C:/Windows/System32/wsl.exe"
    control_timeout_sec: int = 20
    tmp_bytes: int = 16 * 1024**2

    def __post_init__(self):
        if not isinstance(self.image_id, str) or not _IMAGE.fullmatch(self.image_id):
            raise OCIError("immutable_local_image_id_required")
        if self.mode not in {"native", "wsl"}:
            raise OCIError("unsupported_docker_transport")
        safe_linux_path(self.docker_path)
        safe_linux_path(self.python_path)
        bounded_integer(self.control_timeout_sec, 1, 120, "control_timeout")
        bounded_integer(self.tmp_bytes, 1024**2, 64 * 1024**2, "tmp_bytes")


@dataclass(frozen=True)
class CommandResult:
    returncode: int | None
    stdout: bytes = b""
    stderr: bytes = b""
    uncertain: bool = False
    truncated: bool = False


class DockerTransport(Protocol):
    def run(self, arguments: list[str], *, timeout: int, max_bytes: int) -> CommandResult: ...
    def linux_path(self, path: Path) -> str: ...


class DockerCLITransport:
    """Bounded subprocess pipes, scrubbed host environment, no shell or fallback.

    Killing a timed-out CLI only stops that CLI. Its result remains uncertain;
    it says nothing about whether the daemon completed the requested operation.
    """

    def __init__(self, config: OCIConfig):
        self.config = config

    def _environment(self) -> dict[str, str]:
        if self.config.mode == "wsl":
            return {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "TEMP", "TMP", "PATH")
                    if k in os.environ}
        return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}

    def _wsl(self, arguments: list[str]) -> list[str]:
        return [self.config.wsl_executable, "--distribution", "Ubuntu-24.04", "--exec",
                "/usr/bin/env", "-i", "PATH=/usr/bin:/bin", *arguments]

    def command(self, arguments: list[str]) -> list[str]:
        docker = [self.config.docker_path, "--config", "/nonexistent/citrus-oci",
                  "--host", "unix:///var/run/docker.sock", *arguments]
        return self._wsl(docker) if self.config.mode == "wsl" else docker

    def _run(self, command: list[str], *, timeout: int, max_bytes: int) -> CommandResult:
        buffers = [bytearray(), bytearray()]
        lock = threading.Lock()
        overflow = threading.Event()
        readers = []
        process = None
        uncertain = False

        def drain(stream, index):
            try:
                while block := stream.read(8192):
                    with lock:
                        remaining = max_bytes - sum(map(len, buffers))
                        buffers[index].extend(block[:max(0, remaining)])
                        if len(block) > remaining:
                            overflow.set()
            except (OSError, ValueError):
                overflow.set()
            finally:
                stream.close()

        try:
            # Leave Windows command-line headroom; never reissue through a shell.
            if self.config.mode == "wsl" and len(subprocess.list2cmdline(command)) > 30000:
                return CommandResult(None, stderr=b"docker_command_too_long", uncertain=True)
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       env=self._environment(), shell=False)
            for index, stream in enumerate((process.stdout, process.stderr)):
                thread = threading.Thread(target=drain, args=(stream, index), daemon=True)
                thread.start()
                readers.append(thread)
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if overflow.wait(0.02) or time.monotonic() >= deadline:
                    uncertain = True
                    process.kill()
                    break
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                uncertain = True
            for reader in readers:
                reader.join(timeout=1)
            if any(reader.is_alive() for reader in readers):
                uncertain = True
            with lock:
                return CommandResult(process.returncode, bytes(buffers[0]), bytes(buffers[1]),
                                     uncertain or overflow.is_set(), overflow.is_set())
        except (OSError, subprocess.SubprocessError):
            if process is not None and process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
            return CommandResult(None, uncertain=True)

    def run(self, arguments: list[str], *, timeout: int, max_bytes: int) -> CommandResult:
        return self._run(self.command(arguments), timeout=timeout, max_bytes=max_bytes)

    def linux_path(self, path: Path) -> str:
        if self.config.mode == "native":
            if os.name == "nt":
                raise OCIError("windows_requires_wsl_transport")
            return safe_linux_path(path.as_posix())
        result = self._run(self._wsl(["/usr/bin/wslpath", "-a", "-u", str(path)]),
                           timeout=self.config.control_timeout_sec, max_bytes=16384)
        if result.uncertain or result.returncode != 0:
            raise OCIError("wsl_mapping_unknown")
        mapped = safe_linux_path(result.stdout.decode("utf-8", "strict").strip())
        back = self._run(self._wsl(["/usr/bin/wslpath", "-a", "-w", mapped]),
                         timeout=self.config.control_timeout_sec, max_bytes=16384)
        if (back.uncertain or back.returncode != 0
                or os.path.normcase(os.path.abspath(back.stdout.decode("utf-8", "strict").strip()))
                != os.path.normcase(str(path))):
            raise OCIError("wsl_mapping_does_not_roundtrip")
        return mapped


@dataclass(frozen=True)
class OCIHandle:
    execution_id: str
    fence: int
    name: str
    image_id: str
    input_root: str
    linux_root: str
    request: ExecutionRequest
    environment_sha256: str = ""

    def as_dict(self) -> dict:
        """Host persistence only; a serialized handle is not user authority."""
        return {"backend": "oci-v1", "execution_id": self.execution_id, "fence": self.fence, "name": self.name,
                "image_id": self.image_id, "input_root": self.input_root,
                "linux_root": self.linux_root, "request": self.request.serialize(),
                "environment_sha256": self.environment_sha256}

    @classmethod
    def parse(cls, payload: dict) -> OCIHandle:
        if isinstance(payload, dict) and "backend" in payload:
            if payload["backend"] != "oci-v1":
                raise OCIError("foreign_oci_handle")
            payload = {k: v for k, v in payload.items() if k != "backend"}
        required = {"execution_id", "fence", "name", "image_id", "input_root", "linux_root", "request"}
        if not isinstance(payload, dict) or not required <= set(payload) or set(payload) - required - {"environment_sha256"}:
            raise OCIError("invalid_persisted_oci_handle")
        try:
            return cls(**{**payload, "request": ExecutionRequest.parse(payload["request"])})
        except (TypeError, ValueError) as exc:
            raise OCIError("invalid_persisted_oci_handle") from exc

    @property
    def labels(self) -> dict[str, str]:
        return {_LABEL + "backend": "offline-v1", _LABEL + "execution": self.execution_id,
                _LABEL + "fence": str(self.fence), _LABEL + "request": self.request.fingerprint,
                _LABEL + "name": self.name}


@dataclass(frozen=True)
class OCIStatus:
    state: str  # unknown / running / retained / stopped
    stopped: bool = False
    container_id: str = ""
    child_exit_code: int | None = None
    timed_out: bool = False
    detail: str = ""
    container_exit_code: int | None = None
    oom_killed: bool = False
    # Only the protected supervisor's bounded Docker log record, never user logs.
    summary: dict = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True)
class OCICollection:
    outputs: dict[str, bytes]
    stdout: bytes
    stderr: bytes
    child_exit_code: int | None
    timed_out: bool
    logs_truncated: bool


# Fixed trusted launcher. User code is ONLY passed as a verified snapshot file.
# Non-dumpable PID 1 prevents same-uid children opening its Docker log fd through
# /proc/1/fd or ptracing its summary buffer. Signals can kill it (fail closed).
_SUPERVISOR = r'''
import base64, ctypes, hashlib, json, os, signal, stat, subprocess, sys, threading, time
c = json.loads(sys.argv[1])
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(4, 0, 0, 0, 0) != 0 or libc.prctl(3, 0, 0, 0, 0) != 0:
    raise RuntimeError('supervisor_protection_unavailable')
os.environ.clear()
env = {'PATH':'/usr/local/bin:/usr/bin:/bin', 'HOME':'/workspace', 'LANG':'C.UTF-8',
       'TMPDIR':'/tmp', 'PYTHONDONTWRITEBYTECODE':'1'}
bufs = [bytearray(), bytearray()]
lock = threading.Lock()
truncated = [False]
def drain(stream, index):
    try:
        while True:
            block = stream.read1(8192)
            if not block: break
            with lock:
                left = c['log_bytes'] - sum(map(len, bufs))
                bufs[index].extend(block[:max(0, left)])
                if len(block) > left: truncated[0] = True
    finally: stream.close()
summary = {'phase':'complete', 'exit_code':125, 'timed_out':False, 'error':''}
try:
    for item in c['snapshots']:
        source = '/snapshot' + item['path']
        target = None
        if not item['path'].startswith('/input/'):
            os.makedirs(os.path.dirname(item['path']), exist_ok=True)
            target = open(item['path'], 'xb')
        digest = hashlib.sha256()
        size = 0
        try:
            with open(source, 'rb') as f:
                if not stat.S_ISREG(os.fstat(f.fileno()).st_mode): raise ValueError('invalid_snapshot')
                while True:
                    block = f.read(65536)
                    if not block: break
                    size += len(block)
                    if size > item['size_bytes']: raise ValueError('snapshot_byte_limit')
                    digest.update(block)
                    if target is not None: target.write(block)
        finally:
            if target is not None: target.close()
        if size != item['size_bytes'] or digest.hexdigest() != item['sha256']:
            raise RuntimeError('snapshot_hash_mismatch')
    command = [sys.executable, '-I', c['script'], *c['arguments']]
    if c.get('environment_sha256'):
        with open('/environment/site.zip', 'rb') as source:
            if hashlib.sha256(source.read(17 * 1024**2 + 1)).hexdigest() != c['environment_sha256']:
                raise RuntimeError('environment_archive_integrity_mismatch')
        launcher = "import runpy,sys;sys.path.insert(0,'/environment/site.zip');sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name='__main__')"
        command = [sys.executable, '-I', '-c', launcher, c['script'], *c['arguments']]
    child = subprocess.Popen(command,
        cwd='/workspace', env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=True, close_fds=True)
    threads = []
    for i, stream in enumerate((child.stdout, child.stderr)):
        t = threading.Thread(target=drain, args=(stream, i), daemon=True)
        t.start()
        threads.append(t)
    try: summary['exit_code'] = child.wait(timeout=c['timeout_sec'])
    except subprocess.TimeoutExpired:
        summary['timed_out'] = True
        os.killpg(child.pid, signal.SIGKILL)
        summary['exit_code'] = child.wait()
    finally:
        try: os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError: pass
    for t in threads: t.join(timeout=1)
except Exception:
    summary['error'] = 'supervisor_launch_or_snapshot_failure'
with lock:
    summary.update(stdout=base64.b64encode(bufs[0]).decode('ascii'),
                   stderr=base64.b64encode(bufs[1]).decode('ascii'), truncated=truncated[0])
sys.stdout.write(json.dumps(summary, separators=(',', ':')) + '\n')
sys.stdout.flush()
while True: time.sleep(3600)
'''

# Runs trusted code inside the existing container, never on the control host.
# Walk directory descriptors with NOFOLLOW; a symlink swap cannot escape a root.
_COLLECTOR = r'''
import base64, ctypes, json, os, stat, sys
c = json.loads(sys.argv[1])
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(4, 0, 0, 0, 0) != 0: raise RuntimeError('collector_protection_unavailable')
result = {}
remaining = c['bytes']
def metadata(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)
for path in c['paths']:
    parts = path.split('/')[1:]
    if parts[0] not in ('workspace', 'outputs'): raise ValueError('invalid_root')
    directory = os.open('/' + parts[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    directories = [(directory, metadata(os.fstat(directory)))]
    try:
        for component in parts[1:-1]:
            directory = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            directories.append((directory, metadata(os.fstat(directory))))
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            info = os.fstat(fd)
            before = metadata(info)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > remaining: raise ValueError('invalid_output')
            with os.fdopen(fd, 'rb', closefd=False) as source: data = source.read(remaining + 1)
            if len(data) > remaining: raise ValueError('output_limit')
            if (len(data) != info.st_size or before != metadata(os.fstat(fd))
                    or before != metadata(os.stat(parts[-1], dir_fd=directory, follow_symlinks=False))
                    or any(saved != metadata(os.fstat(d)) for d, saved in directories)):
                raise ValueError('output_changed_during_read')
            remaining -= len(data)
            result[path] = base64.b64encode(data).decode('ascii')
        finally: os.close(fd)
    finally:
        for d, _ in reversed(directories): os.close(d)
sys.stdout.write(json.dumps(result, separators=(',', ':')))
sys.stdout.flush()
'''


class OfflineOCIBackend:
    """Narrow Docker lifecycle adapter; callers own leases, receipts and staging."""

    def __init__(self, config: OCIConfig, transport: DockerTransport | None = None, *, environment_loader=None):
        self.config = config
        self.transport = transport if transport is not None else DockerCLITransport(config)
        # Trusted deployment wiring, never a model-selected path or callback.
        self.environment_loader = environment_loader

    def _run(self, args: list[str], *, max_bytes: int = _CONTROL_BYTES) -> CommandResult:
        try:
            return self.transport.run(args, timeout=self.config.control_timeout_sec, max_bytes=max_bytes)
        except (OSError, subprocess.SubprocessError):
            return CommandResult(None, uncertain=True)

    def prepare(self, request: ExecutionRequest, *, execution_id: str, fence: int,
                input_root: str | Path) -> OCIHandle:
        if type(request) is not ExecutionRequest:
            raise OCIError("execution_request_required")
        if request.network_policy != "offline" or request.environment_id == "base" and request.environment_revision != 1:
            raise UnsupportedExecution("only_offline_environments_supported")
        if request.environment_id != "base" and self.environment_loader is None:
            raise UnsupportedExecution("environment_loader_required")
        identifier(execution_id)
        bounded_integer(fence, 1, 2**63 - 1, "fence")
        staging = _host_directory(self.config.staging_root)
        root = _host_directory(input_root)
        if root.parent != staging or root.name != execution_id:
            raise OCIError("per_execution_staging_root_required")
        _host_directory(root / "input")
        snapshots = (request.script, *request.inputs)
        expected = {item.path.lstrip("/"): item for item in snapshots}
        seen = set()
        # No extra data, sockets, devices, symlinks/reparse points or nested mounts
        # are authorized just because the caller handed us a directory.
        count = 0
        for parent, dirs, files in os.walk(root, followlinks=False):
            for name in (*dirs, *files):
                count += 1
                # Reserve both the environment directory and archive BEFORE
                # admitting snapshots; cleanup's total tree budget stays 1024.
                if count > (1022 if request.environment_id != "base" else 1024):
                    raise OCIError("staging_entry_limit")
                path = Path(parent) / name
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise OCIError("linked_snapshot")
                if stat.S_ISDIR(info.st_mode):
                    continue
                relative = path.relative_to(root).as_posix()
                item = expected.get(relative)
                if not stat.S_ISREG(info.st_mode) or item is None or info.st_size != item.size_bytes:
                    raise OCIError("unexpected_snapshot")
                digest = hashlib.sha256()
                with path.open("rb") as source:
                    for block in iter(lambda: source.read(65536), b""):
                        digest.update(block)
                if digest.hexdigest() != item.sha256:
                    raise OCIError("snapshot_hash_mismatch")
                seen.add(relative)
        if seen != set(expected):
            raise OCIError("missing_snapshot")
        linux_root = safe_linux_path(self.transport.linux_path(root))
        linux_input = safe_linux_path(self.transport.linux_path(root / "input"))
        if linux_input != linux_root + "/input":
            raise OCIError("inconsistent_input_mapping")
        from .environments import installer_bytes, parse_lock, inspect_overlay
        if request.script.sha256 == hashlib.sha256(installer_bytes()).hexdigest():
            if len(request.arguments) != 2 or request.arguments[0] not in {p.path for p in request.inputs}:
                raise OCIError("environment_installer_arguments_invalid")
            lock = parse_lock((root / request.arguments[0].lstrip("/")).read_bytes())
            if lock["image_id"] != self.config.image_id:
                raise UnsupportedExecution("environment_base_image_mismatch")
        environment_hash = ""
        if request.environment_id != "base":
            manifest, data = self.environment_loader(request)
            if (manifest["lock"]["image_id"] != self.config.image_id
                    or (manifest["workspace_id"], manifest["task_id"], manifest["environment_id"], manifest["revision"])
                    != (request.workspace_id, request.task_id, request.environment_id, request.environment_revision)):
                raise UnsupportedExecution("environment_identity_mismatch")
            environment_hash = hashlib.sha256(data).hexdigest()
            if environment_hash != manifest["archive"]["sha256"]:
                raise OCIError("environment_archive_integrity_mismatch")
            inspect_overlay(data, manifest["lock"], manifest["lock_sha256"])
            directory = root / "environment"
            directory.mkdir(mode=0o755)
            with (directory / "site.zip").open("xb") as stream:
                stream.write(data)
            if os.name != "nt":
                (directory / "site.zip").chmod(0o444)
        return OCIHandle(execution_id, fence,
                         "citrus-code-" + execution_id + "-" + str(fence) + "-" + uuid.uuid4().hex[:12],
                         self.config.image_id, str(root), linux_root, request, environment_hash)

    def _validate_handle(self, handle: OCIHandle) -> None:
        if type(handle) is not OCIHandle or handle.image_id != self.config.image_id:
            raise OCIError("foreign_oci_handle")
        identifier(handle.execution_id)
        bounded_integer(handle.fence, 1, 2**63 - 1, "fence")
        pattern = "citrus-code-" + handle.execution_id + "-" + str(handle.fence) + r"-[a-f0-9]{12}"
        if not re.fullmatch(pattern, handle.name):
            raise OCIError("invalid_container_name")
        safe_linux_path(handle.linux_root)
        if (handle.request.network_policy != "offline"
                or handle.request.environment_id == "base" and handle.request.environment_revision != 1):
            raise UnsupportedExecution("only_offline_environments_supported")
        if handle.request.environment_id != "base":
            if not isinstance(handle.environment_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", handle.environment_sha256):
                raise OCIError("environment_archive_identity_required")
        elif handle.environment_sha256 != "":
            raise OCIError("unexpected_base_environment_archive")

    def build_create_command(self, handle: OCIHandle) -> list[str]:
        self._validate_handle(handle)
        request = handle.request
        limits = request.limits
        command = ["create", "--name", handle.name, "--pull=never", "--restart=no",
                   "--runtime=runc", "--no-healthcheck", "--cgroupns=private",
                   "--network=none", "--ipc=none", "--read-only", "--user=65534:65534",
                   "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
                   "--pids-limit=" + str(limits.pids), "--memory=" + str(limits.memory_mb) + "m",
                   "--memory-swap=" + str(limits.memory_mb) + "m",
                   "--cpus=" + format(limits.cpu_millis / 1000, ".3f"),
                   "--ulimit=nofile=256:256", "--ulimit=core=0:0",
                   "--log-driver=json-file", "--log-opt=max-size=8m", "--log-opt=max-file=1",
                   "--workdir=/workspace", "--entrypoint=" + self.config.python_path]
        for key, value in sorted(handle.labels.items()):
            command.extend(["--label", key + "=" + value])
        for source, target in ((handle.linux_root, "/snapshot"), (handle.linux_root + "/input", "/input")):
            command.extend(["--mount", "type=bind,source=" + source + ",target=" + target
                            + ",readonly,bind-recursive=disabled"])
        if handle.environment_sha256:
            command.extend(["--mount", "type=bind,source=" + handle.linux_root
                + "/environment,target=/environment,readonly,bind-recursive=disabled"])
        # Scratch is per mount. Writable quota is scratch + output + tmp, and
        # tmpfs pages also count against --memory. Output reception has its own
        # smaller aggregate byte/count limit independent of filesystem space.
        for target, size in (("/workspace", limits.scratch_bytes),
                             ("/outputs", limits.output_bytes), ("/tmp", self.config.tmp_bytes)):
            # The collector has UID0 without DAC capabilities. A read-only
            # group permits traversing ordinary outputs without granting it
            # write access; user-created unreadable files still fail closed.
            mode = "0700" if target == "/tmp" else "0750"
            command.extend(["--tmpfs", target + ":rw,noexec,nosuid,nodev,size=" + str(size)
                            + ",nr_inodes=4096,mode=" + mode + ",uid=65534,gid=65534"])
        settings = {"snapshots": [{"path": item.path, "sha256": item.sha256, "size_bytes": item.size_bytes}
                                  for item in (request.script, *request.inputs)],
                    "script": request.script.path, "arguments": request.arguments,
                    "timeout_sec": limits.timeout_sec, "log_bytes": limits.log_bytes,
                    "environment_sha256": handle.environment_sha256}
        command.extend([handle.image_id, "-I", "-c", _SUPERVISOR,
                        json.dumps(settings, separators=(",", ":"), ensure_ascii=True)])
        return command

    def start(self, handle: OCIHandle) -> OCIStatus:
        """One create then one detached start; never retry on ambiguous results."""
        command = self.build_create_command(handle)
        image = self._run(["image", "inspect", "--format={{json .}}", handle.image_id])
        if image.uncertain or image.truncated or image.returncode != 0:
            return OCIStatus("unknown", detail="local_image_inspection_unconfirmed")
        try:
            metadata = json.loads(image.stdout)
            if (metadata["Id"] != handle.image_id or metadata["Os"] != "linux"
                    or metadata["Config"].get("Volumes")):
                raise UnsupportedExecution("linux_image_without_declared_volumes_required")
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise OCIError("invalid_local_image_inspection") from exc
        created = self._run(command)
        if created.uncertain or created.truncated or created.returncode != 0:
            return OCIStatus("unknown", detail="container_create_unconfirmed")
        status = self.inspect(handle)
        if not status.stopped or not status.container_id:
            return OCIStatus("unknown", detail="created_container_metadata_unconfirmed")
        started = self._run(["start", status.container_id])
        if started.uncertain or started.truncated or started.returncode != 0:
            return OCIStatus("unknown", container_id=status.container_id, detail="container_start_unconfirmed")
        return self.inspect(handle)

    def _document(self, handle: OCIHandle) -> dict | None:
        self._validate_handle(handle)
        result = self._run(["inspect", "--type=container", "--format={{json .}}", handle.name])
        if result.uncertain or result.truncated or result.returncode != 0:
            return None
        try:
            doc = json.loads(result.stdout)
            config = doc["Config"]
            labels = config["Labels"]
            if (doc["Name"] != "/" + handle.name or doc["Image"] != handle.image_id
                    or config["Image"] != handle.image_id
                    or any(labels.get(k) != v for k, v in handle.labels.items())
                    or not re.fullmatch(r"[a-f0-9]{64}", doc["Id"])):
                raise OCIError("container_identity_mismatch")
            return doc
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise OCIError("invalid_container_inspection") from exc

    def inspect(self, handle: OCIHandle, *, read_summary: bool = True) -> OCIStatus:
        doc = self._document(handle)
        if doc is None:
            return OCIStatus("unknown", detail="container_inspection_unconfirmed")
        state = doc.get("State", {})
        running, restarting, paused = (state.get(key) for key in ("Running", "Restarting", "Paused"))
        if any(type(flag) is not bool for flag in (running, restarting, paused)) or restarting:
            return OCIStatus("unknown", container_id=doc["Id"], detail="container_state_unconfirmed")
        if not running:
            if state.get("Status") in {"created", "exited", "dead"} and state.get("Pid") == 0:
                return OCIStatus("stopped", True, doc["Id"], detail="daemon_confirmed_stopped",
                                 container_exit_code=state.get("ExitCode") if type(state.get("ExitCode")) is int else None,
                                 oom_killed=state.get("OOMKilled") is True)
            return OCIStatus("unknown", container_id=doc["Id"], detail="container_stop_unconfirmed")
        if paused:
            return OCIStatus("unknown", container_id=doc["Id"], detail="container_unexpectedly_paused")
        if not read_summary:
            return OCIStatus("running", container_id=doc["Id"])
        limit = handle.request.limits.log_bytes * 2 + 16384
        logs = self._run(["logs", doc["Id"]], max_bytes=limit)
        if logs.uncertain or logs.truncated or logs.returncode != 0:
            return OCIStatus("unknown", container_id=doc["Id"], detail="supervisor_log_unconfirmed")
        if not logs.stdout.strip():
            return OCIStatus("running", container_id=doc["Id"])
        try:
            summary = json.loads(logs.stdout)
            if (not isinstance(summary, dict) or summary.get("phase") != "complete"
                    or type(summary.get("exit_code")) is not int
                    or not -255 <= summary["exit_code"] <= 255
                    or type(summary.get("timed_out")) is not bool
                    or type(summary.get("truncated")) is not bool
                    or not isinstance(summary.get("error"), str)):
                raise ValueError("invalid_summary")
            streams = [base64.b64decode(summary[key], validate=True) for key in ("stdout", "stderr")]
            if sum(map(len, streams)) > handle.request.limits.log_bytes:
                raise ValueError("log_limit")
        except (ValueError, KeyError, TypeError) as exc:
            raise OCIError("invalid_supervisor_summary") from exc
        return OCIStatus("retained", container_id=doc["Id"], child_exit_code=summary["exit_code"],
                         timed_out=summary["timed_out"], detail=summary["error"], summary=summary)

    def collect(self, handle: OCIHandle, *, include_outputs: bool = True) -> OCICollection:
        """Collect bounded regular-file bytes while tmpfs is still alive.

        Content is untrusted, not a receipt. Descendants may still be running;
        the controller must kill/confirm stop before finalizing/releasing leases.
        Missing, linked, nonregular or oversized declared outputs fail closed.
        include_outputs=False returns verified failure diagnostics without reading files.
        """
        status = self.inspect(handle)
        if status.state != "retained":
            raise OCIError("live_retained_supervisor_required_for_collection")
        request = handle.request
        outputs: dict[str, bytes] = {}
        if include_outputs and request.outputs:
            config = json.dumps({"paths": request.outputs, "bytes": request.limits.output_bytes},
                                separators=(",", ":"))
            response = self._run(["exec", "--user=0:65534", status.container_id, self.config.python_path, "-I", "-c",
                                  _COLLECTOR, config],
                                 max_bytes=(request.limits.output_bytes * 4 + 2) // 3
                                 + request.limits.output_files * 2048 + 16384)
            if response.uncertain or response.truncated or response.returncode != 0:
                raise OCIError("output_collection_unconfirmed")
            try:
                raw = json.loads(response.stdout, object_pairs_hook=_unique_object)
                if not isinstance(raw, dict) or set(raw) != set(request.outputs):
                    raise ValueError("output_manifest_mismatch")
                for path, value in raw.items():
                    if not isinstance(value, str):
                        raise ValueError("invalid_output_encoding")
                    outputs[path] = base64.b64decode(value, validate=True)
                if sum(map(len, outputs.values())) > request.limits.output_bytes:
                    raise ValueError("output_limit")
            except (ValueError, TypeError) as exc:
                raise OCIError("invalid_collected_output") from exc
        return OCICollection(outputs, base64.b64decode(status.summary["stdout"], validate=True),
                             base64.b64decode(status.summary["stderr"], validate=True),
                             status.child_exit_code, status.timed_out, status.summary["truncated"])

    def kill(self, handle: OCIHandle) -> OCIStatus:
        status = self.inspect(handle, read_summary=False)
        if status.stopped:
            return status
        if not status.container_id:
            return OCIStatus("unknown", detail="kill_target_unconfirmed")
        result = self._run(["kill", "--signal=KILL", status.container_id])
        # CLI success alone is never evidence of stopped processes.
        checked = self.inspect(handle, read_summary=False)
        if checked.stopped:
            return checked
        return OCIStatus("unknown", container_id=status.container_id,
                         detail="container_kill_unconfirmed" if result.uncertain else "container_still_not_stopped")

    def confirm_removed(self, handle: OCIHandle, *, container_id: str) -> bool:
        """Fresh daemon absence by BOTH exact prior ID and exact name.

        Internal observation only. Caller must already hold a durable removal
        intent for this previously identity-verified, physically stopped target.
        This must never be used to reconcile an unconfirmed running attempt.
        """
        self._validate_handle(handle)
        if not isinstance(container_id, str) or not re.fullmatch(r"[a-f0-9]{64}", container_id):
            raise OCIError("exact_removed_container_identity_required")
        for selection in ("id=" + container_id, "name=^/" + handle.name + "$"):
            listed = self._run(["container", "ls", "--all", "--no-trunc", "--filter", selection,
                                "--format={{.ID}}"])
            if (listed.uncertain or listed.truncated or listed.returncode != 0 or listed.stdout.strip()):
                return False
        return True

    def remove(self, handle: OCIHandle) -> bool:
        """Never force-remove a running/unverified target; True confirms absence."""
        status = self.inspect(handle, read_summary=False)
        if not status.stopped:
            return False
        result = self._run(["rm", status.container_id])
        if result.uncertain or result.truncated or result.returncode != 0:
            return False
        listed = self._run(["container", "ls", "--all", "--no-trunc", "--filter",
                            "name=^/" + handle.name + "$", "--format={{.ID}}"])
        return (not listed.uncertain and not listed.truncated and listed.returncode == 0
                and not listed.stdout.strip())


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result

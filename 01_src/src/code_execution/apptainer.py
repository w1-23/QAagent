"""Offline CPU Code lifecycle on pinned SIF, systemd and bounded private tmpfs.

No Docker socket, host-Python fallback, writable host guest bind, or replay.
A retained child result is separate from the full service cgroup's physical stop.
Real Linux namespace/cgroup support is checked by guards before user code starts.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import time
import uuid

from .oci import (OCIConfig, OCIError, OfflineOCIBackend, DockerCLITransport,
                  OCICollection, _SUPERVISOR, _COLLECTOR, _unique_object,
                  _host_directory, safe_linux_path)
from .protocol import ExecutionRequest, identifier, bounded_integer


@dataclass(frozen=True)
class ApptainerConfig:
    image_id: str
    staging_root: str
    sif_path: str
    runtime: str = "/usr/bin/apptainer"
    python_path: str = "/usr/local/bin/python"
    host_python: str = "/usr/bin/python3"
    service_path: str = str(Path(__file__).with_name("apptainer_service.py"))
    control_timeout_sec: int = 20
    tmp_bytes: int = 16 * 1024**2
    # A small trusted controller SIF may establish the runtime's supported
    # user namespace before unshare on AppArmor-restricted Linux hosts.
    namespace_sif_path: str = ""
    namespace_sif_sha256: str = ""

    def __post_init__(self):
        OCIConfig(self.image_id, self.staging_root, control_timeout_sec=self.control_timeout_sec,
                  tmp_bytes=self.tmp_bytes)
        for value in (self.sif_path, self.runtime, self.python_path, self.host_python, self.service_path):
            safe_linux_path(value)
        if not self.sif_path.endswith(".sif"):
            raise OCIError("local_pinned_sif_required")
        if self.namespace_sif_path or self.namespace_sif_sha256:
            safe_linux_path(self.namespace_sif_path)
            if (not self.namespace_sif_path.endswith('.sif')
                    or not isinstance(self.namespace_sif_sha256, str)
                    or not re.fullmatch(r'[a-f0-9]{64}', self.namespace_sif_sha256)):
                raise OCIError('pinned_namespace_sif_required')


@dataclass(frozen=True)
class ApptainerHandle:
    execution_id: str
    fence: int
    name: str
    image_id: str
    input_root: str
    linux_root: str
    request: ExecutionRequest
    sif_path: str
    environment_sha256: str = ""

    def __post_init__(self):
        identifier(self.execution_id)
        bounded_integer(self.fence, 1, 2**63 - 1, "fence")
        if (not re.fullmatch("citrus-code-appt-" + self.execution_id + "-" + str(self.fence)
                             + r"-[a-f0-9]{24}\.service", self.name)
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", self.image_id)
                or type(self.request) is not ExecutionRequest):
            raise OCIError("invalid_apptainer_handle")
        for value in (self.sif_path, self.linux_root):
            safe_linux_path(value)
        if (not isinstance(self.input_root, str)
                or not (self.input_root.startswith("/") or Path(self.input_root).is_absolute())
                or not self.sif_path.endswith(".sif")):
            raise OCIError("invalid_apptainer_paths")
        if (self.request.network_policy != "offline"
                or self.request.environment_id == "base" and self.request.environment_revision != 1
                or (self.request.environment_id == "base" and self.environment_sha256 != "")
                or (self.request.environment_id != "base"
                    and not re.fullmatch(r"[a-f0-9]{64}", self.environment_sha256))):
            raise OCIError("invalid_apptainer_environment")

    def as_dict(self):
        return {"backend": "apptainer-v1", "execution_id": self.execution_id, "fence": self.fence,
                "name": self.name, "image_id": self.image_id, "input_root": self.input_root,
                "linux_root": self.linux_root, "request": self.request.serialize(),
                "sif_path": self.sif_path, "environment_sha256": self.environment_sha256}

    @classmethod
    def parse(cls, payload):
        fields = {"backend", "execution_id", "fence", "name", "image_id", "input_root", "linux_root",
                  "request", "sif_path", "environment_sha256"}
        if type(payload) is not dict or set(payload) != fields or payload["backend"] != "apptainer-v1":
            raise OCIError("invalid_persisted_apptainer_handle")
        try:
            return cls(**{k: ExecutionRequest.parse(v) if k == "request" else v
                          for k, v in payload.items() if k != "backend"})
        except (ValueError, TypeError) as exc:
            raise OCIError("invalid_persisted_apptainer_handle") from exc

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class ApptainerStatus:
    state: str
    stopped: bool = False
    identity: dict = field(default_factory=dict)
    child_exit_code: int | None = None
    timed_out: bool = False
    summary: dict = field(default_factory=dict, repr=False, compare=False)

    def validate_identity(self, handle):
        i = self.identity
        if (type(i) is not dict or set(i) != {"backend", "unit", "invocation", "cgroup", "device", "inode", "handle_sha256"}
                or i["backend"] != "apptainer-v1" or i["unit"] != handle.name
                or i["handle_sha256"] != handle.digest
                or not isinstance(i["invocation"], str) or not re.fullmatch(r"[a-f0-9]{32}", i["invocation"])
                or type(i["device"]) is not int or i["device"] < 0
                or type(i["inode"]) is not int or i["inode"] <= 0):
            raise OCIError("invalid_apptainer_runtime_identity")
        safe_linux_path(i["cgroup"])
        if not i["cgroup"].endswith("/" + handle.name):
            raise OCIError("foreign_apptainer_cgroup")


# Same inner supervisor protocol as OCI, preceded by runtime namespace and mount
# assertions. No user code is created until all these checks pass inside the SIF.
_NAMESPACE_GUARD = r'''
import json, os, pathlib, socket
v = json.loads(__import__('sys').argv[1])
assert os.getuid() != 0
f = dict(line.split(':', 1) for line in pathlib.Path('/proc/self/status').read_text().splitlines() if ':' in line)
assert f['NoNewPrivs'].strip() == '1' and int(f['CapEff'].strip(), 16) == 0
assert os.readlink('/proc/self/ns/net') != v['host_netns']
assert os.readlink('/proc/self/ns/pid') != v['host_pidns']
assert os.readlink('/proc/1/ns/pid') == os.readlink('/proc/self/ns/pid')
assert all(name == 'lo' for _, name in socket.if_nameindex())
assert not pathlib.Path('/sys/fs/cgroup/cgroup.controllers').exists()
assert os.statvfs('/').f_flag & os.ST_RDONLY
for name, budget in v['budgets'].items():
    fs = os.statvfs('/' + name)
    assert fs.f_blocks * fs.f_frsize <= budget and fs.f_files <= 4096
    required_flags = os.ST_NOSUID | os.ST_NODEV | os.ST_NOEXEC
    assert fs.f_flag & required_flags == required_flags
assert not pathlib.Path(v['host_control']).exists()
assert not pathlib.Path('/dev/nvidia0').exists() and not pathlib.Path('/dev/dri').exists()
'''


class _LocalTransport(DockerCLITransport):
    def __init__(self, config):
        # Reuse ONLY bounded, scrubbed subprocess pipes, never Docker command().
        super().__init__(OCIConfig(config.image_id, config.staging_root))

    def run(self, arguments, *, timeout, max_bytes):
        return self._run(arguments, timeout=timeout, max_bytes=max_bytes)

    def _environment(self):
        from src.figures.apptainer import _host_env
        return _host_env()


class OfflineApptainerBackend:
    def __init__(self, config, transport=None, *, environment_loader=None):
        self.config = config
        self.transport = transport if transport is not None else _LocalTransport(config)
        self.environment_loader = environment_loader

    def _run(self, arguments, *, max_bytes=262144):
        return self.transport.run(arguments, timeout=self.config.control_timeout_sec, max_bytes=max_bytes)

    def _validate_host_image(self):
        from src.figures.apptainer import ApptainerBackend
        # The figure backend's host/SIF validation does not run its drawing job
        # protocol. It verifies Linux, unprivileged user, local runtime and hash.
        validator = ApptainerBackend(image=self.config.sif_path,
            image_sha256=self.config.image_id.removeprefix("sha256:"), runtime=self.config.runtime)
        validator._validate_host()
        validator._validate_image()
        if self.config.namespace_sif_path:
            ApptainerBackend(image=self.config.namespace_sif_path,
                image_sha256=self.config.namespace_sif_sha256,
                runtime=self.config.runtime)._validate_image()
        if shutil.which("unshare", path="/usr/bin:/bin") is None:
            raise OCIError("code_apptainer_user_mount_namespace_required")
        for executable in (self.config.host_python, self.config.service_path):
            path = Path(executable).resolve(strict=True)
            for item in (path, *path.parents):
                info = item.lstat()
                if stat.S_ISLNK(info.st_mode) or info.st_mode & 0o022:
                    raise OCIError("trusted_apptainer_launcher_required")

    def prepare(self, request, *, execution_id, fence, input_root):
        self._validate_host_image()
        # OCI prepare is passive: exact snapshot validation and trusted overlay
        # materialization only, without daemon or image execution.
        prepared = OfflineOCIBackend(OCIConfig(self.config.image_id, self.config.staging_root),
            self.transport, environment_loader=self.environment_loader).prepare(
                request, execution_id=execution_id, fence=fence, input_root=input_root)
        if len(os.fsencode(str(Path(input_root) / "control.sock"))) > 107:
            raise OCIError("apptainer_control_socket_path_too_long")
        if sum(len(d) + len(f) for _, d, f in os.walk(input_root)) > 1000:
            raise OCIError("apptainer_private_control_entry_budget")
        handle = ApptainerHandle(prepared.execution_id, prepared.fence,
            f"citrus-code-appt-{execution_id}-{fence}-{uuid.uuid4().hex[:24]}.service",
            prepared.image_id, prepared.input_root, prepared.linux_root, prepared.request,
            self.config.sif_path, prepared.environment_sha256)
        # Reject impossible page-sized hard budgets or missing host namespace
        # observations while the controller can still prove this is unstarted.
        self._settings(handle)
        return handle

    def _validate_handle(self, handle):
        if (type(handle) is not ApptainerHandle or handle.image_id != self.config.image_id
                or handle.sif_path != self.config.sif_path or handle.input_root != handle.linux_root
                or Path(handle.input_root) != Path(self.config.staging_root) / handle.execution_id):
            raise OCIError("foreign_apptainer_handle")
        # Frozen instances can still have been constructed with invalid data by
        # trusted Python callers; repeat structural validation at every boundary.
        handle.__post_init__()

    def build_command(self, handle):
        self._validate_handle(handle)
        from src.figures.apptainer import _host_env
        limits = handle.request.limits
        command = ["/usr/bin/systemd-run", "--user", "--quiet", "--unit", handle.name]
        properties = ["Type=exec", "RemainAfterExit=yes", "KillMode=control-group", "SendSIGKILL=yes",
            "TimeoutStopSec=2s", f"RuntimeMaxSec={limits.timeout_sec + 60}s",
            f"MemoryMax={limits.memory_mb * 1024**2}", "MemorySwapMax=0", f"TasksMax={limits.pids}",
            f"CPUQuota={limits.cpu_millis / 10:g}%", "NoNewPrivileges=yes", "LimitCORE=0", "LimitNOFILE=256",
            "UMask=0077", "StandardOutput=null", "StandardError=null", "Restart=no",
            "Description=citrus-code-apptainer-v1-" + handle.digest]
        for value in properties:
            command += ["--property", value]
        command += ["--", "/usr/bin/env", "-i", *(f"{k}={v}" for k, v in _host_env().items()),
            self.config.host_python, "-I", self.config.service_path, "--bootstrap",
            str(PurePosixPath(handle.linux_root) / "control.json")]
        return command

    def _guard_command(self, handle):
        from src.figures.apptainer import _CGROUP_GUARD
        limits = handle.request.limits
        python = '/usr/local/bin/python' if self.config.namespace_sif_path else self.config.host_python
        command = ["/usr/bin/unshare", "--user", "--map-current-user", "--keep-caps",
            "--mount", "--fork", "--kill-child=KILL", python, "-I",
            self.config.service_path, str(PurePosixPath(handle.linux_root) / "control.json")]
        if self.config.namespace_sif_path:
            from src.figures.apptainer import _host_env
            # Only trusted controller code runs in this outer SIF. It preserves
            # the host cgroup/PID/net views, never receives model keys/assets,
            # and does not execute a generated script. The inner SIF still has
            # every ordinary Code isolation restriction.
            wrapper = [self.config.runtime, 'exec', '--userns', '--cleanenv', '--no-home',
                '--no-mount', 'hostfs,cwd,home,bind-paths', '--pwd', '/tmp']
            binds = [(handle.input_root, 'rw'), (self.config.service_path, 'ro'),
                     (handle.sif_path, 'ro'), (self.config.runtime, 'ro'),
                     ('/usr/libexec/apptainer', 'ro'), ('/etc/apptainer', 'ro'),
                     (_host_env()['XDG_RUNTIME_DIR'], 'rw')]
            for path, mode in binds:
                wrapper += ['--bind', f'{path}:{path}:{mode}']
            command = wrapper + [self.config.namespace_sif_path] + command
        return [self.config.host_python, "-I", "-c", _CGROUP_GUARD,
            str(limits.memory_mb * 1024**2), str(limits.pids), str(limits.cpu_millis / 1000),
            *command]

    def _settings(self, handle):
        from src.figures.apptainer import _host_env
        request, root = handle.request, PurePosixPath(handle.linux_root)
        budgets = {"workspace": request.limits.scratch_bytes, "outputs": request.limits.output_bytes,
                   "tmp": self.config.tmp_bytes}
        page = os.sysconf("SC_PAGE_SIZE")
        budgets = {name: size // page * page for name, size in budgets.items()}
        if any(size < page for size in budgets.values()):
            raise OCIError("apptainer_tmpfs_budget_below_kernel_page_size")
        settings = {"snapshots": [{"path": s.path, "sha256": s.sha256, "size_bytes": s.size_bytes}
                                  for s in (request.script, *request.inputs)],
            "script": request.script.path, "arguments": request.arguments,
            "timeout_sec": request.limits.timeout_sec, "log_bytes": request.limits.log_bytes,
            "environment_sha256": handle.environment_sha256, "budgets": budgets,
            "host_netns": os.readlink("/proc/self/ns/net"), "host_pidns": os.readlink("/proc/self/ns/pid"),
            "host_control": str(root / "control.json")}
        command = [self.config.runtime, "exec", "--userns", "--containall", "--cleanenv", "--no-home",
            "--no-mount", "hostfs,cwd,home,bind-paths,sys", "--net", "--network", "none", "--pid", "--ipc",
            "--no-privs", "--drop-caps", "ALL", "--cwd", "/workspace"]
        for folder in ("input", "workspace", "outputs"):
            command += ["--bind", f"{root / folder}:/snapshot/{folder}:ro"]
        command += ["--bind", f"{root / 'input'}:/input:ro"]
        if handle.environment_sha256:
            command += ["--bind", f"{root / 'environment'}:/code-environment:ro"]
        for folder in budgets:
            command += ["--bind", f"{root / 'scratch' / folder}:/{folder}:rw"]
        for target in ("/var/tmp", "/dev/shm"):
            command += ["--bind", f"{root / 'scratch' / 'tmp'}:{target}:rw"]
        # /environment is Apptainer's own environment-script link. Keep the
        # exact OCI supervisor protocol with only its immutable overlay path adapted.
        supervisor = _SUPERVISOR.replace('/environment/site.zip', '/code-environment/site.zip')
        command += ["--env", "HOME=/workspace", "--env", "OMP_NUM_THREADS=1",
                    "--env", "OPENBLAS_NUM_THREADS=1", "--env", "MKL_NUM_THREADS=1",
                    handle.sif_path, self.config.python_path, "-I", "-c", _NAMESPACE_GUARD + supervisor,
                    json.dumps(settings, separators=(",", ":"))]
        collector = _COLLECTOR.replace("os.open('/' + parts[0],", f"os.open({str(root / 'scratch')!r} + '/' + parts[0],")
        return {"unit": handle.name, "handle_sha256": handle.digest, "host_env": _host_env(),
            "sif_path": handle.sif_path, "sif_sha256": handle.image_id.removeprefix("sha256:"),
            "namespace_sif_path": self.config.namespace_sif_path,
            "namespace_sif_sha256": self.config.namespace_sif_sha256,
            "command": command, "guard_command": self._guard_command(handle), "budgets": budgets, "collector": collector,
            "summary_bytes": request.limits.log_bytes * 2 + 16384,
            "collection_bytes": (request.limits.output_bytes * 4 + 2) // 3 + request.limits.output_files * 2048 + 16384,
            "output_bytes": request.limits.output_bytes, "outputs": request.outputs}

    def start(self, handle):
        self._validate_handle(handle)
        self._validate_host_image()
        root = _host_directory(handle.input_root)
        # Exclusive control file/dirs prohibit a second start of this handle.
        with (root / "control.json").open("x", encoding="utf-8") as stream:
            json.dump(self._settings(handle), stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        (root / "scratch").mkdir(mode=0o700)
        for folder in ("workspace", "outputs", "tmp"):
            (root / "scratch" / folder).mkdir(mode=0o700)
        result = self._run(self.build_command(handle))
        if result.uncertain or result.truncated or result.returncode != 0:
            return ApptainerStatus("unknown")
        deadline = time.monotonic() + self.config.control_timeout_sec
        while time.monotonic() < deadline:
            observed = self.inspect(handle)
            if observed.state != "unknown":
                return observed
            time.sleep(0.05)
        return ApptainerStatus("unknown")

    def _unit(self, handle):
        self._validate_handle(handle)
        response = self._run(["/usr/bin/systemctl", "--user", "show", handle.name, "--no-pager",
            "--property=Id,LoadState,ActiveState,SubState,InvocationID,ControlGroup,MainPID,Description,KillMode"])
        if response.uncertain or response.truncated:
            return None
        fields = dict(line.split("=", 1) for line in response.stdout.decode("utf-8", "strict").splitlines() if "=" in line)
        if fields.get("LoadState") == "not-found":
            return {"LoadState": "not-found"}
        if (response.returncode != 0 or fields.get("Id") != handle.name
                or fields.get("Description") != "citrus-code-apptainer-v1-" + handle.digest
                or fields.get("KillMode") != "control-group"):
            return None
        return fields

    def _identity(self, handle):
        path = Path(handle.input_root) / "identity.json"
        _host_directory(path.parent)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 4096:
            raise OCIError("trusted_apptainer_identity_required")
        with path.open("rb") as stream:
            data = stream.read(4097)
            if not os.path.samestat(info, os.fstat(stream.fileno())):
                raise OCIError("apptainer_identity_changed")
        identity = json.loads(data, object_pairs_hook=_unique_object)
        ApptainerStatus("unknown", identity=identity).validate_identity(handle)
        return identity

    def _cgroup_matches(self, identity):
        path = Path("/sys/fs/cgroup") / identity["cgroup"].lstrip("/")
        try:
            _host_directory(path)
            info = path.stat()
            return (info.st_dev, info.st_ino) == (identity["device"], identity["inode"])
        except (OSError, OCIError):
            return False

    def _cgroup_empty(self, identity):
        path = Path("/sys/fs/cgroup") / identity["cgroup"].lstrip("/")
        try:
            try:
                path.lstat()
            except FileNotFoundError:
                return True
            _host_directory(path)
            info = path.stat()
            if (info.st_dev, info.st_ino) != (identity["device"], identity["inode"]):
                return False
            fields = dict(line.split() for line in (path / "cgroup.events").read_text().splitlines())
            return fields.get("populated") == "0"
        except (OSError, ValueError):
            return False

    def _control(self, handle, identity, *, collect=False, outputs=False):
        maximum = handle.request.limits.log_bytes * 2 + 32768
        if outputs:
            maximum += (handle.request.limits.output_bytes * 4 + 2) // 3 + handle.request.limits.output_files * 2048
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(self.config.control_timeout_sec)
            client.connect(str(Path(handle.input_root) / "control.sock"))
            client.sendall(json.dumps({"identity": identity, "operation": "collect" if collect else "inspect",
                                      "outputs": outputs}, separators=(",", ":")).encode() + b"\n")
            payload = bytearray()
            while block := client.recv(min(65536, maximum + 1 - len(payload))):
                payload.extend(block)
                if len(payload) > maximum:
                    raise OCIError("apptainer_control_record_limit")
        parsed = json.loads(payload, object_pairs_hook=_unique_object)
        if parsed.get("identity") != identity:
            raise OCIError("apptainer_control_identity_unconfirmed")
        return parsed

    def inspect(self, handle, *, read_summary=True):
        self._validate_handle(handle)
        try:
            unit, identity = self._unit(handle), self._identity(handle)
            if (unit is None or unit.get("InvocationID") != identity["invocation"]
                    or unit.get("ControlGroup") not in ("", identity["cgroup"])):
                return ApptainerStatus("unknown")
            stopped = (unit.get("MainPID") == "0" and unit.get("SubState") in ("exited", "dead", "failed")
                       and self._cgroup_empty(identity))
            if stopped:
                return ApptainerStatus("stopped", True, identity)
            if (unit.get("ActiveState") != "active" or unit.get("SubState") != "running"
                    or unit.get("ControlGroup") != identity["cgroup"]
                    or not unit.get("MainPID", "").isdigit() or int(unit["MainPID"]) <= 0
                    or not self._cgroup_matches(identity)):
                return ApptainerStatus("unknown", identity=identity)
            if not read_summary:
                return ApptainerStatus("running", identity=identity)
            response = self._control(handle, identity)
            if response.get("phase") == "running":
                return ApptainerStatus("running", identity=identity)
            summary = response["summary"]
            self._validate_summary(handle, summary)
            return ApptainerStatus("retained", identity=identity, child_exit_code=summary["exit_code"],
                                   timed_out=summary["timed_out"], summary=summary)
        except (OSError, ValueError, KeyError, TypeError, OCIError):
            return ApptainerStatus("unknown")

    @staticmethod
    def _validate_summary(handle, summary):
        if (type(summary) is not dict or summary.get("phase") != "complete"
                or type(summary.get("exit_code")) is not int or not -255 <= summary["exit_code"] <= 255
                or type(summary.get("timed_out")) is not bool or type(summary.get("truncated")) is not bool
                or type(summary.get("error")) is not str):
            raise OCIError("invalid_apptainer_summary")
        streams = [base64.b64decode(summary[key], validate=True) for key in ("stdout", "stderr")]
        if sum(map(len, streams)) > handle.request.limits.log_bytes:
            raise OCIError("apptainer_log_limit")

    def collect(self, handle, *, include_outputs=True):
        status = self.inspect(handle)
        if status.state != "retained":
            raise OCIError("retained_apptainer_supervisor_required")
        response = self._control(handle, status.identity, collect=True, outputs=include_outputs)
        if response.get("summary") != status.summary:
            raise OCIError("apptainer_summary_changed")
        raw = response.get("outputs")
        expected = set(handle.request.outputs) if include_outputs else set()
        if type(raw) is not dict or set(raw) != expected:
            raise OCIError("apptainer_output_set_mismatch")
        outputs = {k: base64.b64decode(v, validate=True) for k, v in raw.items()}
        if sum(map(len, outputs.values())) > handle.request.limits.output_bytes:
            raise OCIError("apptainer_output_limit")
        return OCICollection(outputs, base64.b64decode(status.summary["stdout"], validate=True),
            base64.b64decode(status.summary["stderr"], validate=True), status.child_exit_code,
            status.timed_out, status.summary["truncated"])

    def kill(self, handle):
        status = self.inspect(handle, read_summary=False)
        if status.stopped or status.state not in {"running", "retained"} or not status.identity:
            return status
        # Never stop a replacement invocation. systemd kills the entire cgroup,
        # including detached/double-forked descendants. A successful CLI is not proof.
        unit = self._unit(handle)
        if not unit or unit.get("InvocationID") != status.identity["invocation"]:
            return ApptainerStatus("unknown")
        self._run(["/usr/bin/systemctl", "--user", "kill", "--kill-whom=all", "--signal=KILL", handle.name])
        # systemd can report a delivery error after signalling some processes.
        # In every case use bounded observation of the original invocation and
        # cgroup to determine whether the whole execution has actually stopped.
        deadline = time.monotonic() + self.config.control_timeout_sec
        while time.monotonic() < deadline:
            observed = self.inspect(handle, read_summary=False)
            if observed.stopped and observed.identity == status.identity:
                return observed
            time.sleep(0.05)
        return ApptainerStatus("unknown", identity=status.identity)

    def _remove_socket(self, handle):
        path = Path(handle.input_root) / "control.sock"
        if path.exists():
            if not stat.S_ISSOCK(path.lstat().st_mode):
                raise OCIError("foreign_apptainer_control_socket")
            path.unlink()

    def confirm_removed(self, handle, *, container_id):
        self._validate_handle(handle)
        try:
            identity = json.loads(container_id, object_pairs_hook=_unique_object)
            ApptainerStatus("unknown", identity=identity).validate_identity(handle)
            unit = self._unit(handle)
            if (self._identity(handle) != identity or unit != {"LoadState": "not-found"}
                    or not self._cgroup_empty(identity)):
                return False
            self._remove_socket(handle)
            return True
        except (OSError, ValueError, TypeError):
            return False

    def remove(self, handle):
        status = self.inspect(handle, read_summary=False)
        if not status.stopped:
            return False
        identity = json.dumps(status.identity, sort_keys=True, separators=(",", ":"))
        self._remove_socket(handle)
        # Failed transient units otherwise remain loaded. Both mutations occur
        # only after exact invocation + entire-cgroup stop have been confirmed.
        self._run(["/usr/bin/systemctl", "--user", "reset-failed", handle.name])
        current = self._unit(handle)
        if current == {"LoadState": "not-found"}:
            return self.confirm_removed(handle, container_id=identity)
        if not current or current.get("InvocationID") != status.identity["invocation"]:
            return False
        result = self._run(["/usr/bin/systemctl", "--user", "stop", handle.name])
        return (not result.uncertain and not result.truncated and result.returncode == 0
                and self.confirm_removed(handle, container_id=identity))

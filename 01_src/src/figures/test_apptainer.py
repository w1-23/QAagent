"""Offline assertions for the Apptainer security boundary (no runtime needed)."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import signal
import sys
import pytest

from src.figures import apptainer
from src.figures.sandbox import (
    EXIT_OUTPUT_TOO_LARGE, EXIT_TIMEOUT, SandboxError, SandboxJob,
    SandboxUnavailable, _job_directory, _write_job_files, select_backend,
)


@pytest.fixture(autouse=True)
def host_identity(monkeypatch):
    monkeypatch.setattr(apptainer.os, "getuid", lambda: 1003, raising=False)
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)


def _job(**kwargs):
    return SandboxJob(script="pass", **kwargs)


def _backend(tmp_path):
    image = tmp_path / "sandbox.sif"
    image.write_bytes(b"pinned SIF fixture")
    image.chmod(0o444)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    return apptainer.ApptainerBackend(image=str(image), image_sha256=digest)


def test_command_has_only_engine_input_output_binds_and_all_isolation(tmp_path):
    runner = _backend(tmp_path)
    command = runner.build_command(_job(), workdir=tmp_path, unit="test.service")
    joined = " ".join(command)
    for flag in ("--containall", "--cleanenv", "--no-home", "--pid", "--ipc", "--no-privs"):
        assert flag in command
    assert "--no-mount hostfs,cwd,home,bind-paths,sys" in joined
    assert "--net --network none" in joined
    assert "--drop-caps ALL" in joined
    binds = [command[index + 1] for index, arg in enumerate(command) if arg == "--bind"]
    assert binds == [f"{tmp_path / 'engine'}:/opt/citrusfig:ro",
                     f"{tmp_path / 'in'}:/work/in:ro", f"{tmp_path / 'out'}:/work/out:rw"]
    assert "--writable" not in command and "--writable-tmpfs" not in command
    assert "python -I -X utf8 /opt/citrusfig/worker.py --job /work/in/job.json" in joined
    assert command.count(runner.image) == 1
    assert "src/config.py" not in joined


def test_systemd_enforces_resources_lifetime_and_privileges(tmp_path):
    command = apptainer.ApptainerBackend(image="/images/pinned.sif").build_command(
        _job(memory_mb=512, pids=64, cpus="1.5", timeout_sec=17),
        workdir=tmp_path, unit="test.service")
    properties = [command[index + 1] for index, arg in enumerate(command) if arg == "--property"]
    assert set(properties) >= {
        "KillMode=control-group", "SendSIGKILL=yes", "TimeoutStopSec=2s",
        "MemoryMax=536870912", "MemorySwapMax=0", "TasksMax=64",
        "CPUQuota=150%", "RuntimeMaxSec=17s", "NoNewPrivileges=yes", "LimitCORE=0",
    }
    assert apptainer._CGROUP_GUARD in command
    assert "os.sched_setaffinity" in apptainer._CGROUP_GUARD
    assert command.index(apptainer._CGROUP_GUARD) < command.index("/usr/bin/apptainer")


def test_host_environment_is_explicit_and_scrubs_manager_environment(tmp_path, monkeypatch):
    for key in ("APPTAINER_BIND", "APPTAINER_BINDPATH", "APPTAINERENV_SECRET",
                "SINGULARITY_BIND", "LD_PRELOAD", "SECRET_API_KEY"):
        monkeypatch.setenv(key, "/secrets")
    env = apptainer._host_env()
    assert set(env) == {"PATH", "HOME", "LANG", "GOMAXPROCS", "GOMEMLIMIT",
                        "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"}
    assert env["GOMAXPROCS"] == "2"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1003/bus"
    command = apptainer.ApptainerBackend(image="/pinned.sif").build_command(
        _job(), workdir=tmp_path, unit="test.service")
    assert command[command.index("/usr/bin/env") + 1] == "-i"
    assert "/secrets" not in " ".join(command)


def test_minimal_engine_matches_worker_layout(tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "out").mkdir()
    apptainer.ApptainerBackend._stage_engine(tmp_path)
    names = {str(path.relative_to(tmp_path / "engine")).replace("\\", "/")
             for path in (tmp_path / "engine").rglob("*") if path.is_file()}
    assert names == {"worker.py", *(f"src/figures/{name}" for name in apptainer.ENGINE_FILES)}
    assert not any("config" in name or "auth" in name or "sandbox" in name for name in names)


@pytest.mark.skipif(sys.platform != "linux", reason="local SIF paths and permissions require Linux")
def test_correct_pin_required_and_image_must_remain_unchanged(tmp_path):
    runner = _backend(tmp_path)
    assert runner._validate_image() == Path(runner.image)
    runner.image_sha256 = "f" * 64
    with pytest.raises(SandboxUnavailable, match="hash"):
        runner._validate_image()


@pytest.mark.parametrize("image,pin", [
    ("docker://python:3.12", "0" * 64), ("relative.sif", "0" * 64),
    ("/tmp/image.sif", ""), ("/tmp/image.sif", "not-a-hash"),
    ("/tmp/image:rw.sif", "0" * 64), ("/tmp/image.sif\n", "0" * 64),
])
def test_nonlocal_or_unpinned_images_fail_closed(image, pin):
    with pytest.raises(SandboxUnavailable):
        apptainer.ApptainerBackend(image=image, image_sha256=pin)._validate_image()


def test_unavailable_policy_has_no_local_fallback(monkeypatch):
    monkeypatch.setattr(apptainer.ApptainerBackend, "available", lambda self: False)
    assert select_backend(backend="apptainer", image="/pinned.sif", allow_local=True) is None


def test_apptainer_selection_forwards_pin(monkeypatch):
    monkeypatch.setattr(apptainer.ApptainerBackend, "available", lambda self: self.image_sha256 == "a" * 64)
    selected = select_backend(backend="apptainer", image="/pinned.sif", image_sha256="a" * 64)
    assert isinstance(selected, apptainer.ApptainerBackend)


@pytest.mark.parametrize("kwargs", [
    {"cpus": "nan"}, {"cpus": "0"}, {"memory_mb": -1}, {"timeout_sec": 0},
    {"pids": 0}, {"image": "/other.sif"}, {"runtime": "/bin/sh"},
    {"image_sha256": "wrong"}, {"extra_env": {"APPTAINER_BIND": "/"}},
    {"inputs": {"data": "../escape.csv"}}, {"inputs": {"data": "job.json"}},
    {"outputs": {"svg": "../escape.svg"}},
    {"outputs": {"first": "same.svg", "second": "SAME.svg"}},
])
def test_invalid_job_is_rejected_before_launch(kwargs, tmp_path):
    with pytest.raises(SandboxError):
        apptainer.ApptainerBackend().build_command(_job(**kwargs), workdir=tmp_path, unit="x.service")


def test_availability_requires_isolation_probe_success(tmp_path, monkeypatch):
    runner = _backend(tmp_path)
    monkeypatch.setattr(runner, "_validate_host", lambda: None)
    monkeypatch.setattr(runner, "_validate_image", lambda: Path(runner.image))
    monkeypatch.setattr(apptainer.os, "readlink", lambda value: "ns:[123]")
    monkeypatch.setattr(runner, "_execute", lambda *a, **k: (1, "", "denied", 1))
    assert runner.available() is False
    monkeypatch.setattr(runner, "_execute", lambda *a, **k: (0, "", "", 1))
    assert runner.available() is False
    monkeypatch.setattr(runner, "_execute", lambda *a, **k: (0, "CITRUS_APPTAINER_ISOLATED\n", "", 1))
    assert runner.available() is True


class _ExecReached(Exception):
    pass


@pytest.mark.parametrize("bad_file,bad_value", [
    (None, None), ("memory.max", "max"), ("memory.max", "1073741824"),
    ("memory.swap.max", "1"), ("pids.max", "max"), ("pids.max", "256"),
    ("cpu.max", "max 100000"), ("cpu.max", "200000 100000"),
    ("status", "NoNewPrivs:\t0\nCapEff:\t0\n"),
    ("status", "NoNewPrivs:\t1\nCapEff:\t1\n"),
])
def test_guard_checks_real_kernel_limits_before_exec(monkeypatch, bad_file, bad_value):
    files = {
        "/proc/self/status": "NoNewPrivs:\t1\nCapEff:\t0\n",
        "/proc/self/cgroup": "0::/user.slice/test.service\n",
        "/sys/fs/cgroup/user.slice/test.service/memory.max": "536870912",
        "/sys/fs/cgroup/user.slice/test.service/memory.swap.max": "0",
        "/sys/fs/cgroup/user.slice/test.service/pids.max": "128",
        "/sys/fs/cgroup/user.slice/test.service/cpu.max": "100000 100000",
    }
    if bad_file:
        key = "/proc/self/status" if bad_file == "status" else f"/sys/fs/cgroup/user.slice/test.service/{bad_file}"
        files[key] = bad_value
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: files[str(self).replace("\\", "/")])
    monkeypatch.setattr(sys, "argv", ["guard", "536870912", "128", "1", "/usr/bin/apptainer", "exec"])
    affinity = []
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: {2, 3, 4}, raising=False)
    monkeypatch.setattr(os, "sched_setaffinity", lambda pid, cpus: affinity.append(cpus), raising=False)
    monkeypatch.setattr(os, "execv", lambda *a: (_ for _ in ()).throw(_ExecReached()))
    if bad_file:
        with pytest.raises(SystemExit):
            exec(apptainer._CGROUP_GUARD, {})
        assert not affinity
    else:
        with pytest.raises(_ExecReached):
            exec(apptainer._CGROUP_GUARD, {})
        assert affinity == [[2]]


class _Process:
    def __init__(self, *, stdout=b"", stderr=b"", cancelled=False):
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.pid = 4321
        self.returncode = None
        self.cancelled = cancelled

    def poll(self):
        if self.cancelled:
            raise KeyboardInterrupt
        return None

    def wait(self, timeout):
        self.returncode = -9
        return -9


def _mock_process(monkeypatch, runner, process):
    stopped, killed = [], []
    monkeypatch.setattr(apptainer.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(runner, "_stop_unit", lambda unit: stopped.append(unit))
    monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append((pid, sig)), raising=False)
    return stopped, killed


def test_timeout_kills_entire_unit_and_process_group(tmp_path, monkeypatch):
    runner = apptainer.ApptainerBackend()
    stopped, killed = _mock_process(monkeypatch, runner, _Process())
    times = iter([0, 2, 2])
    monkeypatch.setattr(apptainer.time, "monotonic", lambda: next(times))
    code, _, _, _ = runner._execute(["placeholder"], workdir=tmp_path, unit="test.service", timeout=1)
    assert code == EXIT_TIMEOUT
    assert stopped == ["test.service"]
    assert killed == [(4321, signal.SIGKILL)]


def test_cancellation_also_kills_entire_unit(tmp_path, monkeypatch):
    runner = apptainer.ApptainerBackend()
    stopped, killed = _mock_process(monkeypatch, runner, _Process(cancelled=True))
    with pytest.raises(KeyboardInterrupt):
        runner._execute(["placeholder"], workdir=tmp_path, unit="test.service", timeout=1)
    assert stopped == ["test.service"]
    assert killed == [(4321, signal.SIGKILL)]


def test_request_cancel_token_kills_running_unit(tmp_path, monkeypatch):
    from src.core.cancel import CancelToken, ToolCancelled, bound_token

    runner = apptainer.ApptainerBackend()
    process = _Process()
    token = CancelToken()
    process.poll = lambda: (token.cancel("user"), None)[1]
    stopped, killed = _mock_process(monkeypatch, runner, process)
    with bound_token(token), pytest.raises(ToolCancelled, match="apptainer_wait"):
        runner._execute(["placeholder"], workdir=tmp_path, unit="test.service", timeout=2)
    assert stopped == ["test.service"]
    assert killed == [(4321, signal.SIGKILL)]


def test_cancelled_request_never_starts_runtime(monkeypatch):
    from src.core.cancel import CancelToken, ToolCancelled, bound_token

    runner = apptainer.ApptainerBackend()
    monkeypatch.setattr(runner, "_validate_host", lambda: pytest.fail("must not reach runtime validation"))
    token = CancelToken()
    token.cancel()
    with bound_token(token), pytest.raises(ToolCancelled, match="apptainer_start"):
        runner.run(_job())


def test_chatty_process_is_stopped_and_logs_are_bounded(tmp_path, monkeypatch):
    runner = apptainer.ApptainerBackend()
    stopped, _ = _mock_process(monkeypatch, runner, _Process(
        stdout=b"x" * (apptainer.MAX_LOG_BYTES * 3), stderr=b"y" * (apptainer.MAX_LOG_BYTES * 2)))
    code, stdout, stderr, _ = runner._execute(["placeholder"], workdir=tmp_path,
                                             unit="test.service", timeout=2)
    assert code == EXIT_OUTPUT_TOO_LARGE
    assert len(stdout) == apptainer.MAX_LOG_BYTES
    assert len(stderr) == apptainer.MAX_LOG_BYTES
    assert stopped == ["test.service"]


def test_windows_local_worker_preserves_only_required_os_directories(monkeypatch):
    from src.figures.sandbox import _local_env

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SYSTEMROOT", "C:/Windows")
    monkeypatch.setenv("WINDIR", "C:/Windows")
    monkeypatch.setenv("PRIVATE_API_TOKEN", "never-inherit")
    monkeypatch.setenv("USERPROFILE", "C:/Users/private-profile")
    env = _local_env()
    assert env["SYSTEMROOT"] == env["WINDIR"] == "C:/Windows"
    assert env["USERPROFILE"] == env["HOME"]
    assert env["USERPROFILE"] != "C:/Users/private-profile"
    assert "PRIVATE_API_TOKEN" not in env


def test_hash_validates_copied_input_and_profile_is_serialized(tmp_path):
    source = tmp_path / "data.csv"
    source.write_bytes(b"column\n1\n")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    job = _job(inputs={"data": "data.csv"}, sources={"data": str(source)},
               expected_input_sha256={"data.csv": expected}, qa_profile={"required": True})
    payload = json.loads(job.as_json())
    assert payload["expected_input_sha256"] == {"data.csv": expected}
    assert payload["qa_profile"] == {"required": True}
    with _job_directory() as workdir:
        _write_job_files(workdir, job)
        assert (workdir / "in/data.csv").read_bytes() == source.read_bytes()
    source.write_bytes(b"column\n2\n")
    with _job_directory() as workdir, pytest.raises(SandboxError, match="SHA-256"):
        _write_job_files(workdir, job)

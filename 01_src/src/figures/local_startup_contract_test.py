"""Offline regressions for the WSL transport and complete local startup gates."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.figures import readiness
from src.figures.sandbox import SandboxJob, SandboxResult, SandboxUnavailable
from src.figures.wsl_docker import WslDockerBackend, validate_staging, windows_env


@pytest.fixture
def config(monkeypatch, tmp_path):
    monkeypatch.setenv("CITRUSQA_FIGURE_JOB_DIR", str(tmp_path))
    monkeypatch.setattr(readiness, "_EVIDENCE", {})
    return SimpleNamespace(FIGURES_SANDBOX_BACKEND="wsl-docker", FIGURES_SANDBOX_IMAGE="fixture:1",
                           FIGURES_SANDBOX_IMAGE_SHA256="", FIGURES_SANDBOX_RUNTIME="",
                           FIGURES_SANDBOX_WSL_DISTRO="Ubuntu-24.04")


def test_missing_and_relative_staging_fail_closed(monkeypatch):
    monkeypatch.delenv("CITRUSQA_FIGURE_JOB_DIR", raising=False)
    with pytest.raises(SandboxUnavailable):
        validate_staging()
    monkeypatch.setenv("CITRUSQA_FIGURE_JOB_DIR", "relative")
    with pytest.raises(SandboxUnavailable):
        validate_staging()


def test_fixed_distribution_socket_and_container_restrictions(monkeypatch, tmp_path):
    runner = WslDockerBackend(image="fixture:1", distribution="Ubuntu-24.04")
    runner.wsl = "wsl.exe"
    monkeypatch.setattr(runner, "_mount_source", lambda path: "/mnt/e/测试 space/" + path.name)
    command = runner.build_command(SandboxJob(), workdir=tmp_path, name="citrusqa-fig-fixture")
    assert command[:4] == ["wsl.exe", "--distribution", "Ubuntu-24.04", "--exec"]
    assert command[command.index("--host") + 1] == "unix:///var/run/docker.sock"
    for flag, value in [("--network", "none"), ("--cap-drop", "ALL"),
                        ("--security-opt", "no-new-privileges"), ("--user", "65534:65534"),
                        ("--memory", "1024m"), ("--cpus", "1.0"), ("--pids-limit", "128")]:
        assert command[command.index(flag) + 1] == value
    assert "--read-only" in command and "--pull=never" in command
    mounts = [command[i + 1] for i, item in enumerate(command) if item == "--mount"]
    assert mounts == ["type=bind,src=/mnt/e/测试 space/in,dst=/work/in,readonly",
                      "type=bind,src=/mnt/e/测试 space/out,dst=/work/out"]
    assert "--privileged" not in command


def test_environment_does_not_forward_keys(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-not-a-key")
    monkeypatch.setenv("DOCKER_HOST", "tcp://synthetic.invalid:1234")
    assert "DEEPSEEK_API_KEY" not in windows_env()
    assert "DOCKER_HOST" not in windows_env()


def test_mount_rejects_outside_staging(config, tmp_path):
    runner = WslDockerBackend(image="fixture:1")
    with pytest.raises(SandboxUnavailable):
        runner._mount_source(tmp_path.parent)


def test_mount_mapping_roundtrip_and_spaces(config, tmp_path, monkeypatch):
    job = tmp_path / "作业 space"
    (job / "in").mkdir(parents=True)
    runner = WslDockerBackend(image="fixture:1")
    runner.wsl = "wsl.exe"
    responses = iter(["/mnt/e/测试 space/in", str(job / "in")])
    monkeypatch.setattr(runner, "_control", lambda command: next(responses))
    assert runner._mount_source(job / "in") == "/mnt/e/测试 space/in"
    responses = iter(["/mnt/e/测试 space/in", str(tmp_path)])
    with pytest.raises(SandboxUnavailable):
        runner._mount_source(job / "in")


def test_cleanup_failure_is_not_reported_as_success(monkeypatch):
    runner = WslDockerBackend(image="fixture:1")
    runner.wsl = "wsl.exe"
    monkeypatch.setattr("src.figures.wsl_docker.subprocess.run", Mock(return_value=
                        SimpleNamespace(returncode=1, stderr=b"daemon unavailable")))
    with pytest.raises(SandboxUnavailable, match="cleanup"):
        runner._remove_container("citrusqa-fig-fixture")


def test_unconfirmed_cleanup_retains_job_mounts(config, tmp_path):
    from src.figures.wsl_docker import CleanupUnconfirmed, _WslJobDirectory
    with pytest.raises(CleanupUnconfirmed):
        with _WslJobDirectory() as workdir:
            raise CleanupUnconfirmed("synthetic daemon failure")
    assert (workdir / "in").is_dir() and (workdir / "out").is_dir()


def fake_result(job, *, bad_edit=False, bad_receipt=False):
    # Synthetic protocol receipt only; this mocked transport runs no skill code.
    import hashlib
    from src.figures.nature_receipts import COMMIT
    nature_receipt = {"version": 1, "commit": COMMIT, "status": "review_required",
        "pdf_sha256": hashlib.sha256(b"%PDF-fixture").hexdigest(), "checks": {
            "source_preflight": {"status": "review_required"},
            "panel_alignment": {"status": "not_applicable"},
            "pdf_text": {"status": "pass"}, "pdf_collisions": {"status": "pass"}}}
    if job.output_contract:
        import hashlib
        import pandas as pd
        from src.figures.dataset_codec import encode_frame
        payloads = {}
        for key in job.outputs:
            if key.startswith('table:'):
                payloads[key] = json.dumps(encode_frame(pd.DataFrame({'id': pd.Series([2**53+1], dtype='Int64')}))).encode()
            elif key.endswith(':png'):
                payloads[key] = b'\x89PNG\r\n\x1a\nfixture'
            elif key.endswith(':manifest'):
                payloads[key] = json.dumps({'nature_skill_receipt': nature_receipt,
                    'objects': [{'gid': 'axes_0.title', 'text': 'SYNTHETIC protocol edited'}]}).encode()
            elif key.endswith(':overrides'):
                payloads[key] = b'[]'
        slots = {key: ({'status': 'ok', 'size': len(payloads[key]), 'sha256': hashlib.sha256(payloads[key]).hexdigest()}
                       if key in payloads else {'status': 'missing', 'error': 'Not emitted.'})
                 for key in job.outputs if key != 'result'}
        payloads['result'] = json.dumps({'version': 2, 'ok': True, 'status': 'ok', 'job_id': job.job_id,
            'output_key': job.output_key, 'slots': slots, 'stdout': '',
            'figure_count': 1 if job.output_key else 2, 'applied_count': len(job.overrides)}).encode()
        return SandboxResult(ok=True, exit_code=0, outputs=payloads)
    edited = bool(job.overrides)
    manifest = {"nature_skill_receipt": nature_receipt, "objects": [{"gid": "axes_0.title", "text":
        "wrong" if bad_edit else "SYNTHETIC startup edited", "fontsize": 16}]}
    receipt = dict(override_protocol=0 if bad_receipt else 1, applied_count=len(job.overrides))
    return SandboxResult(ok=True, exit_code=0, stdout=json.dumps(receipt), outputs={
        "svg": b"<svg></svg>", "png": b"\x89PNG\r\n\x1a\nfixture", "pdf": b"%PDF-fixture",
        "manifest": json.dumps(manifest).encode(), "overrides": b"[]"})


@pytest.mark.parametrize("failure", ["missing", "failed", "pdf_changed"])
def test_probe_rejects_invalid_nature_receipt(config, failure):
    def run(job):
        result = fake_result(job)
        manifest = json.loads(result.outputs['manifest'])
        if failure == 'missing':
            manifest.pop('nature_skill_receipt')
        elif failure == 'failed':
            manifest['nature_skill_receipt']['checks']['pdf_collisions']['status'] = 'fail'
        else:
            result.outputs['pdf'] = b'%PDF-different-fixture'
        result.outputs['manifest'] = json.dumps(manifest).encode()
        return result
    runner = SimpleNamespace(name='wsl-docker', distribution='Ubuntu-24.04',
                             image_id='sha256:fixture', run=run)
    with pytest.raises(SandboxUnavailable, match='Nature'):
        readiness.verify_worker(config, runner)
    assert readiness.worker_snapshot(config) is None


def test_probe_runs_create_and_edit_once_and_invalidates_on_config(config):
    runner = SimpleNamespace(name="wsl-docker", distribution="Ubuntu-24.04", image_id="sha256:fixture",
                             run=Mock(side_effect=fake_result))
    assert readiness.verify_worker(config, runner)["verified"]
    assert runner.run.call_count == 4
    assert readiness.verify_worker(config, runner)["verified"]
    assert runner.run.call_count == 4
    config.FIGURES_SANDBOX_WSL_DISTRO = "Different"
    assert readiness.worker_snapshot(config) is None


@pytest.mark.parametrize("bad_edit,bad_receipt", [(True, False), (False, True)])
def test_probe_rejects_false_edit_or_old_worker_protocol(config, bad_edit, bad_receipt):
    runner = SimpleNamespace(name="wsl-docker", distribution="Ubuntu-24.04", image_id="sha256:fixture",
        run=lambda job: fake_result(job, bad_edit=bad_edit, bad_receipt=bad_receipt))
    with pytest.raises(SandboxUnavailable):
        readiness.verify_worker(config, runner)
    assert not readiness.worker_snapshot(config)


def test_changed_image_invalidates_verified_evidence(config):
    readiness._EVIDENCE.update(configuration_key=readiness.configuration_key(config),
                               verified=True, image_id="sha256:old")
    with pytest.raises(SandboxUnavailable):
        readiness.check_image_identity("sha256:new")
    assert readiness.worker_snapshot(config)["verified"] is False


def test_invalidated_image_cannot_run_on_second_attempt(config):
    readiness._EVIDENCE.update(configuration_key=readiness.configuration_key(config),
                               verified=True, image_id="sha256:old")
    for _ in range(2):
        with pytest.raises(SandboxUnavailable):
            readiness.check_image_identity("sha256:new")


@pytest.mark.parametrize("operation", ["available", "run"])
def test_daemon_or_image_failure_invalidates_readiness(config, monkeypatch, operation):
    readiness._EVIDENCE.update(configuration_key=readiness.configuration_key(config),
                               verified=True, image_id="sha256:old")
    runner = WslDockerBackend(image="fixture:1")
    runner.wsl = "wsl.exe"
    monkeypatch.setattr(runner, "_control", Mock(side_effect=SandboxUnavailable("daemon unavailable")))
    if operation == "available":
        assert runner.available() is False
    else:
        with pytest.raises(SandboxUnavailable):
            runner.run(SandboxJob())
    assert readiness.worker_snapshot(config)["verified"] is False


def launcher_module():
    script = Path(__file__).resolve().parents[2] / "scripts/local_startup.py"
    spec = importlib.util.spec_from_file_location("local_startup_fixture", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_port_conflict_never_starts_second_application(monkeypatch, tmp_path):
    launcher = launcher_module()
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(launcher, "listening", lambda port: True)
    monkeypatch.setattr(launcher, "reuse_existing", lambda *args: False)
    prepare = Mock()
    monkeypatch.setattr(launcher, "prepare_docker", prepare)
    with pytest.raises(RuntimeError, match="occupied"):
        launcher.main([])
    prepare.assert_not_called()


def test_matching_instance_is_reused_without_docker_or_models(monkeypatch, tmp_path):
    launcher = launcher_module()
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(launcher, "listening", lambda port: True)
    monkeypatch.setattr(launcher, "reuse_existing", lambda *args: True)
    prepare = Mock()
    monkeypatch.setattr(launcher, "prepare_docker", prepare)
    assert launcher.main([]) == 0
    prepare.assert_not_called()


@pytest.mark.parametrize("actual_image,expected", [("sha256:verified", True), ("sha256:changed", False)])
def test_reuse_checks_real_process_identity_and_current_image(monkeypatch, tmp_path, actual_image, expected):
    from contextlib import contextmanager
    import io
    import sys
    launcher = launcher_module()
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    record = tmp_path / "record.json"
    record.write_text(json.dumps(dict(pid=500, created=123.0, distribution="Ubuntu-24.04",
        image="fixture:1", instance_id="synthetic-instance")), encoding="utf-8")
    process = SimpleNamespace(pid=500, create_time=lambda: 123.0, exe=lambda: sys.executable)
    fake_psutil = SimpleNamespace(Process=lambda pid: process, CONN_LISTEN="LISTEN", Error=RuntimeError,
        net_connections=lambda **kwargs: [SimpleNamespace(pid=500, status="LISTEN", laddr=SimpleNamespace(port=8010))])
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    body = dict(ready=True, status="ready", worker=dict(verified=True, image_id="sha256:verified"),
                local_runtime=dict(profile="full-v1", instance_id="synthetic-instance", pid=500, workspace=str(tmp_path)))
    @contextmanager
    def response(*args, **kwargs):
        yield io.BytesIO(json.dumps(body).encode())
    monkeypatch.setattr(launcher, "build_opener", lambda *args: SimpleNamespace(open=response))
    prepare = Mock(side_effect=lambda runner: setattr(runner, "image_id", actual_image))
    monkeypatch.setattr(launcher, "prepare_docker", prepare)
    assert launcher.reuse_existing(8010, "Ubuntu-24.04", "fixture:1", record) is expected
    prepare.assert_called_once()


def test_missing_daemon_start_permission_is_explicit(monkeypatch):
    launcher = launcher_module()
    runner = SimpleNamespace(distribution="Fixture", _wsl_command=lambda *a: list(a),
                             _docker_command=lambda *a: list(a))
    runner._control = Mock(side_effect=["", SandboxUnavailable("daemon down"),
                                        SandboxUnavailable("sudo password required")])
    with pytest.raises(RuntimeError, match="noninteractively"):
        launcher.prepare_docker(runner)
    assert runner._control.call_count == 3
    assert runner._control.call_args.args[0][:2] == ["/usr/bin/sudo", "-n"]

"""Real trusted declarative child lifecycle; no script sandbox is involved."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
import os
import subprocess
import threading

import pytest

from src.research.test_figure_tools import account


def _resolved():
    from src.figures.spec import parse_spec
    from src.figures.resolve import resolve_spec
    return resolve_spec(parse_spec({"chart": "scatter", "title": "Measured", "x_label": "Time", "y_label": "Mass",
        "x": {"kind": "inline", "values": [1, 4, 9]},
        "y": {"kind": "inline", "values": [2, 6, 4]},
        "qa_profile": {"caption": "Three actual fixture observations."}}))[0]


def _watch(monkeypatch):
    from src.figures import trusted_render
    calls = []
    original = trusted_render._spawn
    def observe(command, **kwargs):
        job = json.loads((kwargs["root"] / "job.json").read_bytes())
        process = original(command, **kwargs)
        calls.append((process, kwargs["root"], job, kwargs))
        return process
    monkeypatch.setattr(trusted_render, "_spawn", observe)
    return calls


def test_real_child_returns_all_formats_data_and_resource_receipt(monkeypatch):
    from src.figures import trusted_render
    calls = _watch(monkeypatch)
    result = trusted_render.render_resolved(_resolved())
    assert result.worker_pid != os.getpid()
    assert result.worker_pid == result.observations["execution"]["pid"]
    assert result.observations["execution"]["service_modules"] == []
    assert result.blobs["svg"].startswith(b"<?xml")
    assert result.blobs["png"].startswith(b"\x89PNG")
    assert result.blobs["pdf"].startswith(b"%PDF-")
    assert result.observations["series"][0]["offsets"] == [[1, 2], [4, 6], [9, 4]]
    assert result.manifest["qa_receipt"]["version"] == 1
    process, root, job, options = calls[0]
    assert process.poll() is not None and not root.exists()
    assert options["stdout"] == options["stderr"] == subprocess.DEVNULL
    assert set(job) == {"version", "resolved", "formats", "dpi", "overrides", "base_overrides", "memory_mb", "cpu_sec"}
    assert not set(job["resolved"]) & {"script", "sources", "inputs", "file_id", "path", "owner_user_id", "database"}
    if os.name == "nt":
        assert result.observations["execution"]["windows_job_bound"] is True
    else:
        limits = result.observations["execution"]["limits"]
        assert limits["address_space"] == [trusted_render.MEMORY_MB * 1024 * 1024] * 2
        assert 0 < limits["cpu"][0] <= 30
        assert limits["file_size"][0] == 48 * 1024 * 1024


def test_production_create_edit_import_reopen_never_builds_artist_in_parent(account, monkeypatch):
    from src.figures import core, exporter, trusted_render, projects
    from src.research.figure_tools import create_research_figure
    from src.research.test_figure_tools import _inline_spec
    from src.session import files
    calls = _watch(monkeypatch)
    monkeypatch.setattr(core, "build_resolved_figure", lambda *a, **k: pytest.fail("Figure built in API/tool process"))
    monkeypatch.setattr(exporter, "export_all", lambda *a, **k: pytest.fail("Figure exported in API/tool process"))
    _, result = create_research_figure.func(spec_json=_inline_spec(), formats_json=["svg"])
    assert result["editable"], result
    figure_id = result["figure_id"]
    edits = [{"gid": "axes_0.title", "prop": "fontsize", "value": 14}]
    preview = projects.preview_overrides(figure_id, 101, edits, expected_revision=1)
    saved = projects.save_overrides(figure_id, 101, edits, expected_revision=1)
    assert projects.detail_for_owner(figure_id, 101)["overrides"] == edits
    entry = files.get_account_file(saved["exports"]["svg"]["file_id"], 101)
    assert entry["path"].read_text(encoding="utf-8") == preview["svg"]
    source = files.register_file(session_id=account["session_id"], owner_user_id=101,
                                 filename="measured.csv", content=b"time,mass\nA,2\nB,6\n")
    imported = projects.import_from_library(101, account["session_id"], source["file_id"])
    assert projects.detail_for_owner(imported["figure_id"], 101)["manifest"]["objects"]
    assert len(calls) == 4
    assert len({process.pid for process, _, _, _ in calls}) == 4  # recycle after each job
    for process, root, job, _ in calls:
        assert process.poll() is not None and not root.exists()
        # A source file id/path is retained only by the parent project, never
        # transported with the resolved numeric plotting request.
        assert source["file_id"] not in json.dumps(job)
        assert str(files.FILES_ROOT) not in json.dumps(job)


def test_real_timeout_kills_owned_child_and_removes_files(monkeypatch):
    from src.figures import trusted_render
    calls = _watch(monkeypatch)
    with pytest.raises(trusted_render.TrustedRenderError) as exc:
        trusted_render.render_resolved(_resolved(), timeout_sec=.05)
    assert exc.value.code == "render_timeout"
    assert len(calls) == 1
    process, root, _, _ = calls[0]
    assert process.poll() is not None and not root.exists()


def test_actual_request_token_reaches_executor_and_kills_real_child(monkeypatch):
    from src.figures import trusted_render
    from src.core.cancel import CancelToken, ToolCancelled, bound_token
    calls = _watch(monkeypatch)
    original = trusted_render._spawn
    started = threading.Event()
    def signal_start(*args, **kwargs):
        process = original(*args, **kwargs)
        started.set()
        return process
    monkeypatch.setattr(trusted_render, "_spawn", signal_start)
    token = CancelToken()
    with bound_token(token), ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(copy_context().run, trusted_render.render_resolved, _resolved())
        assert started.wait(10), "real worker never started"
        token.cancel("trusted-render-test")
        with pytest.raises(ToolCancelled):
            future.result(timeout=10)
    assert len(calls) == 1
    process, root, _, _ = calls[0]
    assert process.poll() is not None and not root.exists()


def test_cancelled_and_full_queue_never_spawn_or_fall_back(monkeypatch):
    from src.figures import trusted_render
    from src.core.cancel import CancelToken, ToolCancelled, bound_token
    monkeypatch.setattr(trusted_render, "_spawn", lambda *a, **k: pytest.fail("unadmitted job spawned"))
    token = CancelToken()
    token.cancel()
    with bound_token(token), pytest.raises(ToolCancelled):
        trusted_render.render_resolved(_resolved())
    admission = threading.BoundedSemaphore(1)
    admission.acquire()
    monkeypatch.setattr(trusted_render, "_ADMISSION", admission)
    with pytest.raises(trusted_render.TrustedRenderError) as exc:
        trusted_render.render_resolved(_resolved())
    assert exc.value.code == "render_busy"


def test_queue_timeout_is_bounded_without_spawning(monkeypatch):
    from src.figures import trusted_render
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(trusted_render, "_SLOTS", slots)
    monkeypatch.setattr(trusted_render, "_spawn", lambda *a, **k: pytest.fail("queued job spawned"))
    with pytest.raises(trusted_render.TrustedRenderError) as exc:
        trusted_render.render_resolved(_resolved(), timeout_sec=.05)
    assert exc.value.code == "render_timeout"


def test_cancellation_while_waiting_for_slot_never_starts_child(monkeypatch):
    from types import SimpleNamespace
    from src.figures import trusted_render
    from src.core.cancel import CancelToken, ToolCancelled, bound_token
    blocked = threading.BoundedSemaphore(1)
    blocked.acquire()
    waiting = threading.Event()
    def acquire(timeout):
        waiting.set()
        return blocked.acquire(timeout=timeout)
    monkeypatch.setattr(trusted_render, "_SLOTS", SimpleNamespace(acquire=acquire, release=blocked.release))
    monkeypatch.setattr(trusted_render, "_spawn", lambda *a, **k: pytest.fail("cancelled queued job spawned"))
    token = CancelToken()
    with bound_token(token), ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(copy_context().run, trusted_render.render_resolved, _resolved())
        assert waiting.wait(5)
        token.cancel()
        with pytest.raises(ToolCancelled):
            future.result(timeout=5)


@pytest.mark.parametrize("damage", ["hardlink", "oversized", "bad_signature", "bad_manifest"])
def test_real_child_output_is_validated_before_return(monkeypatch, damage):
    from src.figures import trusted_render
    calls = _watch(monkeypatch)
    collect = trusted_render._collect
    def damaged_collect(root, outputs):
        target = root / "out/figure.svg"
        if damage == "hardlink":
            target.unlink()
            os.link(root / "job.json", target)
        elif damage == "oversized":
            with target.open("wb") as stream:
                stream.truncate(24 * 1024 * 1024 + 1)
        elif damage == "bad_signature":
            target.write_bytes(b"not an SVG")
        elif damage == "bad_manifest":
            (root / "out/objects.json").write_text('{"objects": "invalid"}', encoding="utf-8")
        return collect(root, outputs)
    monkeypatch.setattr(trusted_render, "_collect", damaged_collect)
    with pytest.raises(trusted_render.TrustedRenderError) as exc:
        trusted_render.render_resolved(_resolved(), formats=("svg",))
    assert exc.value.code == "worker_protocol_invalid"
    assert len(calls) == 1 and calls[0][0].poll() is not None and not calls[0][1].exists()


def test_worker_refuses_python_and_account_refs_and_cleans_up(monkeypatch):
    from src.figures import trusted_render
    calls = _watch(monkeypatch)
    class InvalidResolved:
        style = _resolved().style
        def as_dict(self):
            return {"script": "raise RuntimeError('must never execute')", "file_id": "foreign-owner-file"}
    with pytest.raises(trusted_render.TrustedRenderError) as exc:
        trusted_render.render_resolved(InvalidResolved())
    assert exc.value.code == "invalid_resolved"
    assert len(calls) == 1
    assert calls[0][0].poll() is not None and not calls[0][1].exists()


def test_input_budget_rejects_before_spawning(monkeypatch):
    from src.figures import trusted_render
    monkeypatch.setattr(trusted_render, "MAX_JOB_BYTES", 1)
    monkeypatch.setattr(trusted_render, "_spawn", lambda *a, **k: pytest.fail("oversized job spawned"))
    with pytest.raises(trusted_render.TrustedRenderError) as exc:
        trusted_render.render_resolved(_resolved())
    assert exc.value.code == "render_too_large"


@pytest.mark.skipif(os.name != "nt", reason="Windows suspended JobObject lifecycle")
def test_windows_job_attachment_failure_kills_suspended_child_without_fallback(monkeypatch):
    from src.figures import trusted_render
    calls = _watch(monkeypatch)
    def reject(*args):
        raise OSError("synthetic job attachment rejection")
    monkeypatch.setattr(trusted_render._WindowsJob, "attach_and_resume", reject)
    with pytest.raises(trusted_render.TrustedRenderError) as exc:
        trusted_render.render_resolved(_resolved())
    assert exc.value.code == "worker_unavailable"
    assert len(calls) == 1 and calls[0][0].poll() is not None and not calls[0][1].exists()


@pytest.mark.parametrize("leader_exited", [False, True])
@pytest.mark.parametrize("group_exists", [False, True])
def test_posix_cleanup_always_kills_owned_group_even_after_leader_exit(monkeypatch, leader_exited, group_exists):
    # Deterministic OS seam also runs on Windows; it specifically protects the
    # case where a CPU/AS limit killed the leader but left a descendant alive.
    from types import SimpleNamespace
    from src.figures import trusted_render
    actions = []
    def kill_group(pid, sig):
        actions.append(("killpg", pid, sig))
        if not group_exists:
            raise ProcessLookupError()
    process = SimpleNamespace(pid=938471, poll=lambda: 0 if leader_exited else None,
                              wait=lambda timeout: actions.append(("wait", timeout)))
    monkeypatch.setattr(trusted_render, "os", SimpleNamespace(name="posix", killpg=kill_group))
    monkeypatch.setattr(trusted_render, "signal", SimpleNamespace(SIGKILL=9))
    trusted_render._stop(process)
    assert actions == [("killpg", 938471, 9), ("wait", 5)]


def test_child_environment_has_no_service_secrets_or_real_home(tmp_path, monkeypatch):
    from src.figures import trusted_render
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-cross")
    monkeypatch.setenv("PYTHONPATH", "must-not-cross")
    env = trusted_render._environment(tmp_path)
    assert "ANTHROPIC_API_KEY" not in env and "PYTHONPATH" not in env
    assert env["HOME"] == env["USERPROFILE"] == str(tmp_path)
    assert env["OPENBLAS_NUM_THREADS"] == "1"

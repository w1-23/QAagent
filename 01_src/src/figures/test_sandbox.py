"""Tests for the script sandbox.

The sandbox is a security boundary, so the tests check the boundary rather
than the convenience:

* the Docker command line carries every restriction the design calls for;
* the worker refuses the obvious escapes and reports them clearly;
* a timeout kills the job rather than letting it run;
* **when no sandbox is configured, scripts do not run at all** — the
  fail-closed property. A silent fallback to a local subprocess would turn
  a missing configuration into an open door, and this test is what keeps
  that from happening by accident.

The Docker-backed tests are skipped when no daemon is available; the
command-line assertions run everywhere, because the flags *are* the
boundary and they should be checked even on a machine that cannot run
containers.
"""
from __future__ import annotations

import json

from pathlib import Path

import pytest

from src.figures.sandbox import (
    DockerBackend, LocalBackend, SandboxJob, SandboxUnavailable,
    select_backend,
)

GOOD_SCRIPT = """
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(6, 4))
ax.bar(['CK', 'T1'], [10.1, 12.2], color='#0F4D92')
ax.set_title('果实重量')
ax.set_ylabel('g')
"""


def _job(script: str = GOOD_SCRIPT, **kwargs) -> SandboxJob:
    kwargs.setdefault("outputs", {"svg": "figure.svg", "png": "figure.png"})
    kwargs.setdefault("timeout_sec", 30)
    return SandboxJob(script=script, **kwargs)


# ── Choosing a backend ────────────────────────────────────────────────────

def test_off_means_no_backend_at_all():
    assert select_backend(backend="off") is None
    assert select_backend(backend="") is None
    assert select_backend(backend="none") is None


def test_local_is_refused_unless_it_was_asked_for():
    assert select_backend(backend="local", allow_local=False) is None
    assert isinstance(select_backend(backend="local", allow_local=True),
                      LocalBackend)


def test_docker_is_refused_when_it_cannot_actually_run():
    """A configured backend that does not work is not a working backend.

    The failure mode this prevents: the operator sets ``sandbox_backend:
    docker`` but the daemon is not running, and the service quietly falls
    back to something weaker. There is no fallback.
    """
    runner = select_backend(backend="docker", image="citrusqa/sandbox:1.0.0")
    if runner is None:
        # Either Docker is missing or the daemon is down: the script path is
        # closed, which is the correct outcome.
        return
    assert runner.name == "docker"


def test_an_unknown_backend_name_closes_the_script_path():
    assert select_backend(backend="something-else", allow_local=True) is None


# ── The Docker command line ───────────────────────────────────────────────

def test_the_docker_command_carries_every_restriction():
    """These flags are the boundary; a missing one is a hole."""
    backend = DockerBackend(image="citrusqa/sandbox:1.0.0")
    command = backend.build_command(_job(), workdir=Path("/tmp/job"))

    joined = " ".join(command)
    for expected in ("--network none", "--cap-drop ALL",
                     "--security-opt no-new-privileges", "--read-only",
                     "--pids-limit", "--memory", "--cpus", "--user 65534:65534",
                     "--rm"):
        assert expected in joined, f"缺少隔离参数：{expected}"
    # The input directory is mounted read-only; only the output is writable.
    assert "dst=/work/in,readonly" in joined
    assert "dst=/work/out" in joined


def test_the_docker_command_names_the_image_and_the_worker():
    backend = DockerBackend(image="citrusqa/sandbox:1.0.0")
    command = backend.build_command(_job(), workdir=Path("/tmp/job"))
    assert "citrusqa/sandbox:1.0.0" in command
    assert "/opt/citrusfig/worker.py" in " ".join(command)


def test_the_gvisor_runtime_is_requested_when_configured():
    backend = DockerBackend(image="x", runtime="runsc")
    command = backend.build_command(_job(), workdir=Path("/tmp/job"))
    assert "--runtime" in command
    assert "runsc" in command


def test_the_resource_limits_come_from_the_job():
    backend = DockerBackend(image="x")
    job = _job(timeout_sec=30)
    command = backend.build_command(
        SandboxJob(script="", outputs={}, memory_mb=2048, cpus="2.0", pids=64),
        workdir=Path("/tmp/job"))
    assert "--memory" in command and "2048m" in command
    assert "2.0" in command
    assert "64" in command
    assert job.timeout_sec == 30


def test_a_docker_backend_without_an_image_is_not_available():
    assert DockerBackend(image="").available() is False


# ── The worker, through the local backend ─────────────────────────────────

def test_a_good_script_produces_the_figure():
    result = LocalBackend().run(_job())
    assert result.ok is True
    assert result.exit_code == 0
    assert b"<svg" in result.outputs["svg"]
    assert result.outputs["png"].startswith(b"\x89PNG\r\n\x1a\n")


def test_a_script_that_makes_no_figure_is_reported_as_such():
    result = LocalBackend().run(_job(script="x = 1 + 1\n"))
    assert result.ok is False
    assert result.exit_code == 11
    assert "FIGURE_NO_FIGURE" in result.describe()


def test_a_script_that_makes_two_figures_is_refused():
    script = (
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.figure(); plt.plot([1, 2], [3, 4])\n"
        "plt.figure(); plt.plot([1, 2], [4, 3])\n"
    )
    result = LocalBackend().run(_job(script=script))
    assert result.ok is False
    assert result.exit_code == 12
    assert "FIGURE_MULTIPLE_FIGURES" in result.describe()


def test_a_raising_script_reports_the_error():
    result = LocalBackend().run(_job(script="raise ValueError('boom')"))
    assert result.ok is False
    assert result.exit_code == 10
    assert "boom" in result.stderr


@pytest.mark.parametrize("module", ["socket", "subprocess", "ctypes", "urllib"])
def test_a_script_that_imports_a_forbidden_module_is_refused(module):
    result = LocalBackend().run(_job(script=f"import {module}\n"))
    assert result.ok is False
    assert result.exit_code == 15
    assert result.failure_kind == "forbidden"
    assert module in result.describe()


def test_a_script_that_loops_is_killed_at_the_timeout():
    result = LocalBackend().run(
        _job(script="while True:\n    pass\n", timeout_sec=3))
    assert result.ok is False
    assert result.timed_out is True
    assert result.failure_kind == "timeout"
    assert "FIGURE_SCRIPT_TIMEOUT" in result.describe()


def test_the_scratch_directory_is_removed_after_the_job():
    import tempfile

    before = set(Path(tempfile.gettempdir()).glob("citrusqa-figure-job-*"))
    LocalBackend().run(_job())
    after = set(Path(tempfile.gettempdir()).glob("citrusqa-figure-job-*"))
    assert after == before, "沙箱作业目录必须在作业结束后删除"


def test_the_child_process_does_not_inherit_the_service_environment():
    """A script must not be able to read the server's API keys."""
    import os

    os.environ["CITRUSQA_TEST_SECRET"] = "do-not-leak"
    # The script prints a secret instead of drawing, so the worker's own
    # stdout carries the answer.
    script = (
        "import os\n"
        "print('LEAKED' if os.environ.get('CITRUSQA_TEST_SECRET') else 'CLEAN')\n"
    )
    result = LocalBackend().run(_job(script=script))
    assert "CLEAN" in result.stdout + result.message
    assert "do-not-leak" not in result.stdout + result.message


# ── The tool's own behaviour when no sandbox is configured ────────────────

def test_the_script_tool_refuses_when_no_backend_is_configured():
    from src.research.figure_tools import run_research_figure_script

    content, result = run_research_figure_script.func(script=GOOD_SCRIPT)
    assert content.startswith("[FIGURE_SANDBOX_UNAVAILABLE]")
    assert result["_tool_outcome"]["status"] == "failed"
    assert result["_tool_outcome"]["reason_code"] == "SANDBOX_UNAVAILABLE"


def test_an_empty_script_is_refused_before_any_sandbox_is_chosen():
    from src.research.figure_tools import run_research_figure_script

    content, _result = run_research_figure_script.func(script="   ")
    assert content.startswith("[FIGURE_ARG]")


def test_an_oversized_script_is_refused_before_any_sandbox_is_chosen():
    from src.research.figure_tools import run_research_figure_script

    content, _result = run_research_figure_script.func(script="x = 1\n" * 40000)
    assert content.startswith("[FIGURE_ARG]")


# ── The failures only a container can show ────────────────────────────────

def test_the_worker_is_named_only_once_in_the_run_command():
    """The image's ENTRYPOINT already runs the worker.

    Docker appends the command to the entrypoint, so naming the worker again
    executed ``python worker.py python worker.py --job ...``: argparse saw two
    stray positionals and exited 2 before a single line of the user's script
    ran. The command has to override the entrypoint, not repeat it — and the
    override is also where the wall-clock budget lives, because killing the
    docker client does not stop the container.
    """
    backend = DockerBackend(image="citrusqa/sandbox:1.0.0")
    command = backend.build_command(_job(timeout_sec=30), workdir=Path("/tmp/job"))
    joined = " ".join(command)

    assert joined.count("/opt/citrusfig/worker.py") == 1, joined
    assert "--entrypoint /usr/bin/timeout" in joined
    assert "30s" in joined, "墙钟预算要传进容器"
    assert joined.index("--entrypoint") < joined.index("citrusqa/sandbox:1.0.0")


def test_a_hung_container_is_killed_after_the_client_gives_up(monkeypatch):
    """The client timing out is not the container stopping.

    ``subprocess.run`` gives up on the docker CLI; the daemon keeps the
    container running with the CPU and memory the job was granted. So the
    container is killed by name, and the name is the one the command carried.
    """
    from src.figures import sandbox as sandbox_module

    called: list[list[str]] = []

    def _fake_run(command, **_kwargs):
        called.append(list(command))

        class _Done:
            returncode = 0

        return _Done()

    monkeypatch.setattr(sandbox_module.subprocess, "run", _fake_run)
    backend = DockerBackend(image="citrusqa/sandbox:1.0.0")
    backend._kill("citrusqa-fig-job-1")

    assert called == [["docker", "kill", "citrusqa-fig-job-1"]]
    command = backend.build_command(_job(job_id="job-1"), workdir=Path("/tmp/job"))
    assert "citrusqa-fig-job-1" in command


def test_the_sandbox_image_puts_the_engine_where_it_imports_itself():
    """A layout mismatch here kills every job before the script runs.

    The engine's own modules import ``src.figures.*``, so the image must place
    them under a ``src`` package. This was wrong once and no service-side test
    could see it: the failure only appears inside the container, as a
    ModuleNotFoundError in the worker's stderr.
    """
    root = Path(__file__).resolve().parents[2]        # 01_src
    text = (root / "docker" / "Dockerfile.sandbox").read_text(encoding="utf-8")

    for module in ("__init__", "spec", "styling", "core", "manifest",
                   "overrides", "exporter"):
        assert f"/opt/citrusfig/src/figures/{module}.py" in text, module
    assert "/opt/citrusfig/worker.py" in text
    assert "PYTHONPATH=/opt/citrusfig" in text
    # And the engine really does import its own package by that name.
    core = (root / "src" / "figures" / "core.py").read_text(encoding="utf-8")
    assert "from src.figures." in core

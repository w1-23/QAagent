"""Security, from the negative side.

Everything here is a refusal. The point of these tests is not that the
happy path works — the other files cover that — but that the ways in are
closed:

* a caller cannot set an arbitrary Matplotlib attribute through the
  editor's API;
* a caller cannot change a number the figure claims;
* a script cannot walk out of its own directory;
* a script cannot reach the network or spawn a process;
* the service does not run scripts at all when no sandbox is configured;
* a rendered figure carries nothing that could act.

The Docker-only rules (no network, no privileges, memory and time limits)
are asserted on the *command line* rather than by running a container, so
they are checked even on a machine without a daemon. The container's own
behaviour is verified at deployment time; see ``01_src/docker/README.md``.
"""
from __future__ import annotations

import json

import pytest


# ── The override API is a closed vocabulary ───────────────────────────────

def _manifest() -> dict:
    from src.figures.core import build_resolved_figure
    from src.figures.manifest import build_manifest
    from src.figures.resolve import resolve_spec
    from src.figures.spec import parse_spec

    spec = parse_spec({
        "chart": "bar", "title": "T", "x_label": "X", "y_label": "Y",
        "x": {"kind": "inline", "column": "c", "values": ["A", "B"]},
        "y": {"kind": "inline", "column": "v", "values": [1.0, 2.0]}})
    rendered = build_resolved_figure(resolve_spec(spec)[0])
    return build_manifest(rendered, source="spec")


@pytest.mark.parametrize("prop", [
    "get_height", "set_height", "height", "values", "data", "y_values",
    "set_ylim", "ylim", "get_ylim", "set_xlim", "position", "set_position",
    "__class__", "__dict__", "figure", "read", "write", "eval", "exec",
])
def test_an_arbitrary_attribute_cannot_be_set(prop):
    """The editor's API is a list of names, not a path into the object.

    A property that is not on the list is refused before anything is looked
    up, so there is no name that reaches a Matplotlib setter the service did
    not intend to expose.
    """
    from src.figures.overrides import OverrideError, validate_overrides

    manifest = _manifest()
    series = next(item for item in manifest["objects"] if item["role"] == "series")
    with pytest.raises(OverrideError):
        validate_overrides(
            [{"gid": series["gid"], "prop": prop, "value": 1}], manifest)


@pytest.mark.parametrize("prop", [
    "height", "values", "y_values", "error", "error_kind", "set_ylim",
    "set_xlim", "ticks", "set_yticks",
])
def test_a_number_in_the_figure_cannot_be_overridden(prop):
    """The data is not editable. Only its appearance is."""
    from src.figures.overrides import OverrideError, validate_overrides

    manifest = _manifest()
    series = next(item for item in manifest["objects"] if item["role"] == "series")
    with pytest.raises(OverrideError):
        validate_overrides(
            [{"gid": series["gid"], "prop": prop, "value": 99}], manifest)


def test_the_editable_list_is_the_whole_vocabulary():
    """Every object says what may be changed, and nothing else is accepted."""
    from src.figures.overrides import ALL_EDITABLE_PROPS

    # Explicit reviewed vocabulary, independent of the production allowlist.
    expected = {"color", "fontsize", "fontfamily", "fontweight", "linewidth",
                "offset_px", "frameon", "visible", "marker", "markersize",
                "markerfacecolor", "markeredgecolor", "markeredgewidth", "linestyle",
                "alpha", "text", "label", "fontstyle", "horizontalalignment", "ncols", "loc"}
    assert ALL_EDITABLE_PROPS == expected
    manifest = _manifest()
    for item in manifest["objects"]:
        for prop in item.get("editable", ()):
            assert prop in expected, (item["gid"], prop)


@pytest.mark.parametrize("prop,value", [
    ("fontfamily", "../../SYNTHETIC_untrusted_font.ttf"),
    ("fontfamily", {"fname": "SYNTHETIC_font.ttf"}),
    ("fontweight", "__class__"), ("visible", "false"),
])
def test_new_text_properties_do_not_accept_paths_objects_or_coerced_visibility(prop, value):
    from src.figures.overrides import OverrideError, validate_overrides
    manifest = _manifest()
    title = next(item for item in manifest["objects"] if item["role"] == "title")
    with pytest.raises(OverrideError):
        validate_overrides([{"gid": title["gid"], "prop": prop, "value": value}], manifest)


@pytest.mark.parametrize("value", [
    "url(javascript:alert(1))", "expression(alert(1))", "red; background:url(x)",
    "#12345", "#GGGGGG", "rgb(1,2,3,4,5)", "", None, 123, ["#ffffff"],
])
def test_a_colour_that_is_not_a_colour_is_refused(value):
    from src.figures.overrides import OverrideError, validate_overrides

    manifest = _manifest()
    title = next(item for item in manifest["objects"] if item["role"] == "title")
    with pytest.raises(OverrideError):
        validate_overrides(
            [{"gid": title["gid"], "prop": "color", "value": value}], manifest)


@pytest.mark.parametrize("value", [0.0, 4.9, 24.1, 1000, "big", None, True])
def test_a_font_size_outside_the_range_is_refused(value):
    from src.figures.overrides import OverrideError, validate_overrides

    manifest = _manifest()
    title = next(item for item in manifest["objects"] if item["role"] == "title")
    with pytest.raises(OverrideError):
        validate_overrides(
            [{"gid": title["gid"], "prop": "fontsize", "value": value}], manifest)


@pytest.mark.parametrize("value", [0.0, 0.1, 8.1, 100, "thick", None])
def test_a_line_width_outside_the_range_is_refused(value):
    from src.figures.overrides import OverrideError, validate_overrides

    manifest = _manifest()
    series = next(item for item in manifest["objects"] if item["role"] == "series")
    with pytest.raises(OverrideError):
        validate_overrides(
            [{"gid": series["gid"], "prop": "linewidth", "value": value}], manifest)


@pytest.mark.parametrize("value", [[9999, 0], [0, -9999], [201, 0], [0, -201],
                                  [float("inf"), 0], [0, float("nan")], "far"])
def test_an_offset_outside_the_canvas_is_refused(value):
    from src.figures.overrides import OverrideError, validate_overrides

    manifest = _manifest()
    title = next(item for item in manifest["objects"] if item["role"] == "title")
    with pytest.raises(OverrideError):
        validate_overrides(
            [{"gid": title["gid"], "prop": "offset_px", "value": value}], manifest)


def test_an_override_on_a_property_the_object_does_not_offer_is_refused():
    """A legend has no line width, a series has no frame."""
    from src.figures.overrides import OverrideError, validate_overrides

    manifest = _manifest()
    legend = next((item for item in manifest["objects"]
                   if item["role"] == "legend"), None)
    if legend is None:
        pytest.skip("这张图没有图例")
    with pytest.raises(OverrideError):
        validate_overrides(
            [{"gid": legend["gid"], "prop": "linewidth", "value": 2.0}], manifest)


def test_a_gid_that_names_nothing_is_refused():
    from src.figures.overrides import OverrideError, validate_overrides

    manifest = _manifest()
    with pytest.raises(OverrideError):
        validate_overrides(
            [{"gid": "axes_9.series_0", "prop": "color", "value": "#000000"}],
            manifest)


def test_a_batch_that_is_not_a_list_is_refused():
    from src.figures.overrides import OverrideError, validate_overrides

    manifest = _manifest()
    with pytest.raises(OverrideError):
        validate_overrides({"gid": "axes_0.title", "prop": "color",
                            "value": "#000000"}, manifest)


def test_a_batch_that_is_too_long_is_refused():
    from src.figures.overrides import OverrideError, validate_overrides

    manifest = _manifest()
    title = next(item for item in manifest["objects"] if item["role"] == "title")
    batch = [{"gid": title["gid"], "prop": "color", "value": "#000000"}] * 201
    with pytest.raises(OverrideError):
        validate_overrides(batch, manifest)


def test_an_entry_without_a_gid_is_refused():
    from src.figures.overrides import OverrideError, validate_overrides

    manifest = _manifest()
    with pytest.raises(OverrideError):
        validate_overrides([{"prop": "color", "value": "#000000"}], manifest)


# ── The figure the service renders carries nothing that can act ───────────

def test_a_rendered_figure_carries_no_active_content():
    from src.figures.core import build_resolved_figure
    from src.figures.exporter import export_all
    from src.figures.resolve import resolve_spec
    from src.figures.spec import parse_spec

    spec = parse_spec({
        "chart": "bar", "title": "T", "x_label": "X", "y_label": "Y",
        "x": {"kind": "inline", "column": "c", "values": ["A", "B"]},
        "y": {"kind": "inline", "column": "v", "values": [1.0, 2.0]}})
    rendered = build_resolved_figure(resolve_spec(spec)[0])
    svg = export_all(rendered, formats=("svg",))["svg"].decode("utf-8").lower()
    for forbidden in ("<script", "javascript:", "data:", "<image", "<foreignobject",
                      "onload", "onerror", "onclick", "<style", "style=",
                      "<!doctype", "<!entity"):
        assert forbidden not in svg, forbidden
    # No address may be fetched. The SVG namespace is a name, not a place
    # the browser goes to, so it is the one ``http`` that is allowed.
    without_namespace = svg.replace("http://www.w3.org/2000/svg", "")
    assert "http://" not in without_namespace
    assert "https://" not in without_namespace
    # A ``url()`` may only point at a fragment of this very document. A
    # reference to anything else would be a fetch.
    import re
    for reference in re.findall(r"url\(([^)]*)\)", without_namespace):
        assert reference.strip().startswith("#"), reference


def test_the_flattened_svg_keeps_the_text_readable():
    """A figure whose text became outlines could not be edited or checked."""
    from src.figures.core import build_resolved_figure
    from src.figures.exporter import export_all
    from src.figures.resolve import resolve_spec
    from src.figures.spec import parse_spec

    spec = parse_spec({
        "chart": "bar", "title": "果实重量", "x_label": "处理", "y_label": "g",
        "x": {"kind": "inline", "column": "c", "values": ["A", "B"]},
        "y": {"kind": "inline", "column": "v", "values": [1.0, 2.0]}})
    rendered = build_resolved_figure(resolve_spec(spec)[0])
    svg = export_all(rendered, formats=("svg",))["svg"].decode("utf-8")
    assert "<text" in svg
    assert "果实重量" in svg


# ── The script path stays closed without a sandbox ───────────────────────

def test_without_a_sandbox_no_script_runs():
    """The default configuration closes the script path.

    The service must not fall back to a local subprocess: a missing
    configuration is a closed door, not a weaker door.
    """
    from src.research.figure_tools import run_research_figure_script

    content, result = run_research_figure_script.func(script="x = 1\n")
    assert content.startswith("[FIGURE_SANDBOX_UNAVAILABLE]")
    assert result["_tool_outcome"]["reason_code"] == "SANDBOX_UNAVAILABLE"


def test_the_script_tool_is_not_offered_without_a_sandbox():
    """Visibility follows the environment, not the question's wording.

    A model cannot reach the script path by asking for it: the tool is not
    in the visible list at all.
    """
    from src.graph import tool_availability

    assert tool_availability.script_sandbox_available() is False
    names = tool_availability.available_tool_names(
        tool_availability.runtime_env())
    assert "run_research_figure_script" not in names


def test_an_oversized_script_is_refused_before_anything_runs():
    from src.research.figure_tools import run_research_figure_script

    content, _result = run_research_figure_script.func(script="x = 1\n" * 40000)
    assert content.startswith("[FIGURE_ARG]")


# ── The sandbox's own rules ───────────────────────────────────────────────

def test_a_script_that_imports_a_forbidden_module_is_refused():
    """The intent to escape is reported, not merely survived."""
    from src.figures.sandbox import LocalBackend, SandboxJob

    job = SandboxJob(script="import socket\n", outputs={}, timeout_sec=20)
    result = LocalBackend().run(job)
    assert result.ok is False
    assert result.failure_kind == "forbidden"
    assert "socket" in result.describe()


def test_the_worker_refuses_a_path_outside_the_job_directory():
    """A script cannot name a file above its own directory."""
    from src.figures import worker

    with pytest.raises(ValueError):
        worker._resolve(__import__("pathlib").Path("/work/in"), "../../etc/passwd")


def test_the_docker_command_line_carries_every_restriction():
    """The flags are the boundary; a missing one is a hole.

    Asserted on the command line rather than by running a container, so the
    check holds on a machine without a daemon.
    """
    from pathlib import Path

    from src.figures.sandbox import DockerBackend, SandboxJob

    backend = DockerBackend(image="citrusqa/sandbox:1.0.0")
    job = SandboxJob(script="", outputs={}, memory_mb=512, cpus="0.5", pids=32)
    command = backend.build_command(job, workdir=Path("/srv/citrusqa/jobs/job-1"))
    joined = " ".join(command)
    for expected in ("--rm", "--network none", "--cap-drop ALL",
                     "--security-opt no-new-privileges", "--read-only",
                     "--pids-limit 32", "--memory 512m", "--cpus 0.5",
                     "--user 65534:65534"):
        assert expected in joined, expected
    assert "dst=/work/in,readonly" in joined
    assert "dst=/work/out" in joined


def test_the_sandbox_job_directory_is_removed_after_the_job():
    """Nothing of a script's stays on the disk after it finishes."""
    import tempfile
    from pathlib import Path

    from src.figures.sandbox import LocalBackend, SandboxJob

    before = set(Path(tempfile.gettempdir()).glob("citrusqa-figure-job-*"))
    LocalBackend().run(SandboxJob(script="x = 1\n", outputs={}, timeout_sec=20))
    after = set(Path(tempfile.gettempdir()).glob("citrusqa-figure-job-*"))
    assert after == before


def test_a_script_does_not_inherit_the_services_environment():
    """A script cannot read the server's keys by printing the environment."""
    import os

    from src.figures.sandbox import LocalBackend, SandboxJob

    os.environ["CITRUSQA_TEST_SECRET"] = "do-not-leak"
    script = (
        "import os\n"
        "print('LEAKED' if os.environ.get('CITRUSQA_TEST_SECRET') else 'CLEAN')\n"
    )
    result = LocalBackend().run(SandboxJob(script=script, outputs={},
                                           timeout_sec=20))
    assert "CLEAN" in result.stdout + result.message
    assert "do-not-leak" not in result.stdout + result.message


def test_a_looping_script_is_killed_at_the_timeout():
    """A script cannot hold a worker slot for ever."""
    from src.figures.sandbox import LocalBackend, SandboxJob

    result = LocalBackend().run(
        SandboxJob(script="while True:\n    pass\n", outputs={}, timeout_sec=3))
    assert result.ok is False
    assert result.timed_out is True
    assert result.failure_kind == "timeout"


def test_the_worker_reports_a_script_that_draws_nothing():
    from src.figures.sandbox import LocalBackend, SandboxJob

    result = LocalBackend().run(SandboxJob(script="x = 1 + 1\n", outputs={},
                                           timeout_sec=20))
    assert result.ok is False
    assert "FIGURE_NO_FIGURE" in result.describe()


def test_the_worker_reports_a_script_that_draws_too_many_figures():
    from src.figures.sandbox import LocalBackend, SandboxJob

    script = (
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.figure(); plt.plot([1, 2], [3, 4])\n"
        "plt.figure(); plt.plot([1, 2], [4, 3])\n"
    )
    result = LocalBackend().run(SandboxJob(script=script, outputs={},
                                           timeout_sec=30))
    assert result.ok is False
    assert "FIGURE_MULTIPLE_FIGURES" in result.describe()


def test_the_worker_writes_only_the_declared_outputs():
    """The worker's result names what it produced; nothing else is taken."""
    from src.figures.sandbox import LocalBackend, SandboxJob

    job = SandboxJob(script="open('/work/out/secret.txt', 'w').write('x')\n"
                            "print('done')\n",
                     outputs={"svg": "figure.svg"}, timeout_sec=20)
    result = LocalBackend().run(job)
    # The script may write into the output directory, but the service only
    # takes the outputs the job declared.
    assert set(result.outputs) == set()


def test_the_job_description_carries_no_host_paths():
    """A job names files, not locations on the host.

    The same description has to mean the same thing inside the container and
    outside it, so it holds file names only.
    """
    from src.figures.sandbox import SandboxJob

    job = SandboxJob(script="x = 1", outputs={"svg": "figure.svg"},
                     inputs={"data": "data.csv"},
                     sources={"data": "/srv/citrusqa/state/session_files/x/data.csv"})
    payload = json.loads(job.as_json())
    assert "/srv" not in json.dumps(payload)
    assert payload["inputs"] == {"data": "data.csv"}
    assert payload["outputs"] == {"svg": "figure.svg"}

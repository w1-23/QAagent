"""S4 figure contracts. LocalBackend is test-only and proves no isolation."""
import hashlib
import json

import pytest

from src.research.test_figure_tools import account


def _panel(panel_id, values, groups=None):
    return {"panel_id": panel_id, "chart": "line", "title": panel_id,
            "x": {"kind": "inline", "values": list(range(len(values)))},
            "y": {"kind": "inline", "values": values},
            **({"group": {"kind": "inline", "values": groups}} if groups else {})}


def _multi():
    return {"title": "Two measurements", "panels": [_panel("growth", [1, 2]), _panel("yield", [30, 50])],
            "layout": {"rows": 1, "cols": 2}, "shared_legend": False,
            "qa_profile": {"name": "scientific", "caption": "Measured values; no error bars."}}


def _local(monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "FIGURES_SANDBOX_BACKEND", "local")
    monkeypatch.setattr(settings, "FIGURES_SANDBOX_ALLOW_LOCAL", True)


def _file(account, content=b"x,y\n1,11\n2,22\n", owner=101):
    from src.session import files
    entry = files.register_file(session_id=account["session_id"], owner_user_id=owner,
                                filename="measurements.csv", content=content, mime="text/csv")
    return files.get_account_file(entry["file_id"], owner)


def test_panels_use_their_own_data_and_stable_ids_after_reorder():
    from src.figures.spec import parse_spec
    from src.figures.resolve import resolve_spec
    from src.figures.core import build_resolved_figure
    from src.figures.manifest import build_manifest
    raw = _multi()
    spec = parse_spec(raw)
    assert parse_spec(spec.as_dict()).as_dict() == spec.as_dict()
    resolved, info = resolve_spec(spec)
    first = build_resolved_figure(resolved)
    assert list(first.axes[0].lines[0].get_ydata()) == [1, 2]
    assert list(first.axes[1].lines[0].get_ydata()) == [30, 50]
    before = {item["gid"] for item in build_manifest(first)["objects"] if item["role"] in {"axes", "series"}}
    raw["panels"].reverse()
    second = build_resolved_figure(resolve_spec(parse_spec(raw))[0])
    after = {item["gid"] for item in build_manifest(second)["objects"] if item["role"] in {"axes", "series"}}
    assert before == after
    assert set(info["panels"]) == {"growth", "yield"}


def test_panel_create_preview_save_reopen_keeps_edits_and_datasets(account):
    from src.research.figure_tools import create_research_figure
    from src.figures import projects, store
    from src.session import files
    _, result = create_research_figure.func(spec_json=json.dumps(_multi()))
    assert result["editable"], result
    figure_id = result["figure_id"]
    detail = projects.detail_for_owner(figure_id, 101)
    series = [item for item in detail["manifest"]["objects"] if item["role"] == "series"]
    target = next(item["gid"] for item in series if item["locator"]["panel_id"] == "yield")
    edits = [{"gid": target, "prop": "color", "value": "#b64342"},
             {"gid": "panel_growth.title", "prop": "fontsize", "value": 14}]
    preview = projects.preview_overrides(figure_id, 101, edits, expected_revision=1)
    saved = projects.save_overrides(figure_id, 101, edits, expected_revision=1)
    svg = files.get_account_file(saved["exports"]["svg"]["file_id"], 101)
    assert svg["path"].read_text(encoding="utf-8") == preview["svg"]
    reopened = projects.detail_for_owner(figure_id, 101)
    assert sorted(reopened["overrides"], key=lambda item: item["gid"]) == sorted(edits, key=lambda item: item["gid"])
    assert next(item for item in reopened["manifest"]["objects"] if item["gid"] == target)["color"] == "#b64342"
    source = store.get_project(figure_id, 101)["source_json"]["spec"]
    assert source["panels"][1]["y"]["values"] == [30, 50]
    with pytest.raises(projects.ProjectError):
        projects.preview_overrides(figure_id, 101, [{"gid": target, "prop": "ydata", "value": [100, 100]}])


def test_shared_legend_has_explicit_links_and_rejects_misleading_partial_edit():
    from src.figures.spec import parse_spec
    from src.figures.resolve import resolve_spec
    from src.figures.core import build_resolved_figure
    from src.figures.manifest import build_manifest
    from src.figures.overrides import apply_overrides, validate_overrides, OverrideError
    raw = _multi()
    raw["shared_legend"] = True
    for panel in raw["panels"]:
        panel["group"] = {"kind": "inline", "values": ["control", "control"]}
    rendered = build_resolved_figure(resolve_spec(parse_spec(raw))[0])
    manifest = build_manifest(rendered)
    gids = manifest["shared_legend_groups"]["control"]
    assert len(gids) == 2
    assert len(rendered.figure.legends[0].get_texts()) == 1
    with pytest.raises(OverrideError, match="共享图例"):
        apply_overrides(rendered, manifest, validate_overrides([{"gid": gids[0], "prop": "color", "value": "#b64342"}], manifest))
    edits = [{"gid": gid, "prop": "color", "value": "#b64342"} for gid in gids]
    apply_overrides(rendered, manifest, validate_overrides(edits, manifest))
    assert all(item["color"] == "#b64342" for item in build_manifest(rendered)["objects"] if item["role"] == "series")
    assert rendered.figure.legends[0].legend_handles[0].get_color() == "#b64342"


@pytest.mark.parametrize("change", [
    lambda raw: raw["panels"][1].update(panel_id="growth"),
    lambda raw: raw.update(layout={"rows": 1, "cols": 1}),
    lambda raw: raw["panels"][0].update(panel_id="../../escape"),
])
def test_panel_contract_rejects_ambiguous_identity_and_layout(change):
    from src.figures.spec import parse_spec, SpecError
    raw = _multi()
    change(raw)
    with pytest.raises(SpecError):
        parse_spec(raw)


def test_shared_legend_refuses_incompatible_mark_contracts():
    from src.figures.spec import parse_spec, SpecError
    from src.figures.resolve import resolve_spec
    from src.figures.core import build_resolved_figure
    raw = _multi()
    raw["shared_legend"] = True
    for panel in raw["panels"]:
        panel["group"] = {"kind": "inline", "values": ["same", "same"]}
    raw["panels"][1]["chart"] = "scatter"
    with pytest.raises(SpecError) as exc:
        build_resolved_figure(resolve_spec(parse_spec(raw))[0])
    assert exc.value.code == "incompatible_shared_legend"


def test_script_figure_legend_is_addressable_without_guessing_series_links():
    from src.figures.sandbox import LocalBackend, SandboxJob
    result = LocalBackend().run(SandboxJob(script="""fig, ax = plt.subplots()
ax.plot([1, 2], [3, 4], label='observed')
fig.legend()
fig.suptitle('Overall title')
""", outputs={"svg": "figure.svg", "manifest": "objects.json"}))
    assert result.ok, result.describe()
    manifest = json.loads(result.outputs["manifest"])
    assert next(item for item in manifest["objects"] if item["gid"] == "figure_1.legend_0")["editable"]
    assert next(item for item in manifest["objects"] if item["gid"] == "figure_1.title")["role"] == "title"
    series = next(item for item in manifest["objects"] if item["role"] == "series")
    assert series["editable"] == []
    assert series["editing_limitation"] == "unverified_figure_legend_links"


def test_qa_receipt_measures_final_size_and_reports_unsupported():
    from src.figures.spec import parse_spec
    from src.figures.resolve import resolve_spec
    from src.figures.core import build_resolved_figure
    from src.figures.manifest import build_manifest
    raw = _multi()
    raw["qa_profile"] = {"name": "nature_single", "caption": "Actual caption"}
    rendered = build_resolved_figure(resolve_spec(parse_spec(raw))[0])
    receipt = build_manifest(rendered)["qa_receipt"]
    assert receipt["checks"]["dimensions"]["final_size_mm"][0] == 89
    assert receipt["checks"]["fonts"]["status"] == "fail"  # 7 pt shrinks below 5 pt
    assert receipt["checks"]["linewidth"]["status"] == "fail"
    assert receipt["checks"]["caption"]["status"] == "pass"
    assert "scientific_claims" in receipt["unsupported"]
    assert receipt["status"] == "needs_attention"


INPUT_SCRIPT = """import pandas as pd
import matplotlib.pyplot as plt
frame = pd.read_csv(data)
fig, ax = plt.subplots(figsize=(4, 3))
ax.plot(frame['x'], frame['y'], label='measured')
ax.set_title('Pinned measurements')
ax.legend()
"""


def test_script_input_snapshots_replay_original_bytes_and_keep_pins(account, monkeypatch):
    from src.research.figure_tools import run_research_figure_script
    from src.figures import projects, store
    from src.session import files
    _local(monkeypatch)
    original = _file(account)
    _, result = run_research_figure_script.func(script=INPUT_SCRIPT, formats_json=["svg"],
        inputs_json={"data": {"file_id": original["file_id"]}},
        qa_profile_json={"caption": "Measurements without error bars."})
    assert result.get("editable"), result
    project = store.get_project(result["figure_id"], 101)
    source = project["source_json"]
    snapshot = source["input_snapshots"][0]
    assert snapshot["source_file_id"] == original["file_id"]
    assert snapshot["file_id"] != original["file_id"]
    assert snapshot["sha256"] == hashlib.sha256(b"x,y\n1,11\n2,22\n").hexdigest()
    assert all("path" not in item for item in source["input_snapshots"])
    assert any(item["file_id"] == snapshot["file_id"] for item in project["data_refs_json"])
    # Changing an original source never changes an existing project's snapshot.
    original["path"].write_bytes(b"x,y\n1,900\n2,800\n")
    edits = [{"gid": "axes_0.title", "prop": "fontsize", "value": 12}]
    preview = projects.preview_overrides(result["figure_id"], 101, edits)
    saved = projects.save_overrides(result["figure_id"], 101, edits)
    svg = files.get_account_file(saved["exports"]["svg"]["file_id"], 101)
    assert svg["path"].read_text(encoding="utf-8") == preview["svg"]
    assert projects.detail_for_owner(result["figure_id"], 101)["editable"]
    snap_entry = files.get_account_file(snapshot["file_id"], 101)
    snap_entry["path"].write_bytes(b"x,y\n1,99\n2,88\n")
    with pytest.raises(projects.ProjectError) as exc:
        projects.preview_overrides(result["figure_id"], 101, [])
    assert exc.value.code == "source_changed"


def test_script_input_ownership_and_path_rejected_before_execution(account, monkeypatch):
    from src.research.figure_tools import run_research_figure_script
    from src.figures import sandbox
    _local(monkeypatch)
    foreign = _file(account, owner=202)
    monkeypatch.setattr(sandbox.LocalBackend, "run", lambda *a: pytest.fail("unauthorized input executed"))
    for bindings in ({"data": {"file_id": foreign["file_id"]}}, {"data": {"path": str(foreign["path"])}}):
        _, result = run_research_figure_script.func(script=INPUT_SCRIPT, inputs_json=bindings)
        assert result["_tool_outcome"]["status"] == "failed"


def test_worker_rechecks_snapshot_hash(tmp_path):
    from src.figures.sandbox import LocalBackend, SandboxJob, SandboxError
    data = tmp_path / "data.csv"
    data.write_bytes(b"x,y\n1,11\n")
    with pytest.raises(SandboxError, match="hash|摘要|SHA|digest"):
        LocalBackend().run(SandboxJob(script=INPUT_SCRIPT, inputs={"data": "data.csv"},
            sources={"data": str(data)}, expected_input_sha256={"data.csv": "0" * 64}, outputs={"svg": "figure.svg"}))


def test_seaborn_artists_create_edit_save_reopen(account, monkeypatch):
    pytest.importorskip("seaborn")
    from src.research.figure_tools import run_research_figure_script
    from src.figures import projects
    _local(monkeypatch)
    script = """import seaborn as sns
import matplotlib.pyplot as plt
fig, (left, right) = plt.subplots(1, 2, figsize=(6, 3))
sns.scatterplot(x=[1, 2], y=[3, 5], ax=left, label='observed')
sns.lineplot(x=[1, 2], y=[7, 9], ax=right, label='trend', errorbar=None)
left.set_title('Points')
right.set_title('Trend')
"""
    _, result = run_research_figure_script.func(script=script, formats_json=["svg"])
    assert result.get("editable"), result
    detail = projects.detail_for_owner(result["figure_id"], 101)
    target = next(item["gid"] for item in detail["manifest"]["objects"]
                  if item["role"] == "point" and item["locator"]["axes_index"] == 0)
    edits = [{"gid": target, "prop": "color", "value": "#b64342"}]
    preview = projects.preview_overrides(result["figure_id"], 101, edits)
    saved = projects.save_overrides(result["figure_id"], 101, edits)
    assert set(saved["exports"]) == {"svg", "png", "pdf"}
    assert projects.detail_for_owner(result["figure_id"], 101)["overrides"] == edits
    assert next(item for item in preview["manifest"]["objects"] if item["gid"] == target)["color"] == "#b64342"


def test_analysis_result_input_uses_authenticated_full_precision_snapshot(account, monkeypatch):
    import pandas as pd
    from src.research.analysis_results import persist_analysis_result, persist_dataset_snapshot
    from src.research.figure_tools import run_research_figure_script
    from src.figures import projects, store
    from src.session import files
    _local(monkeypatch)
    dataset = persist_dataset_snapshot(101, pd.DataFrame({"x": [1, 2], "y": [3, 4]}), source={"kind": "test_fixture"})
    precise = 0.12345678901234567
    analysis = persist_analysis_result(101, dataset.ref, outputs={"estimate": precise}, parameters={},
        design={}, exclusions=[], coverage={"rows": 2})
    script = """import json
import pandas as pd
import matplotlib.pyplot as plt
result = json.loads(analysis_result.read_text(encoding='utf-8'))
frame = pd.read_csv(analysis_data)
fig, ax = plt.subplots()
ax.plot(frame['x'], frame['y'])
ax.set_title(str(result['outputs']['estimate']))
"""
    _, result = run_research_figure_script.func(script=script, formats_json=["svg"], analysis_result_ref_json=analysis.ref)
    assert result.get("editable"), result
    source = store.get_project(result["figure_id"], 101)["source_json"]
    assert source["analysis_result_ref"] == analysis.ref
    assert source["dataset_ref"] == dataset.ref
    snapshot = next(item for item in source["input_snapshots"] if item["variable"] == "analysis_result")
    stored = files.get_account_file(snapshot["file_id"], 101)
    assert json.loads(stored["path"].read_bytes())["outputs"]["estimate"] == precise
    projects.preview_overrides(result["figure_id"], 101, [])
    from src.core import tracing
    tracing.set_user_id(202)
    try:
        _, rejected = run_research_figure_script.func(script=script, analysis_result_ref_json=analysis.ref)
        assert rejected["_tool_outcome"]["status"] == "failed"
    finally:
        tracing.set_user_id(101)

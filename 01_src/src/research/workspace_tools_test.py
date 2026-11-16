"""Script/skill discovery and cross-turn editing without executing source."""
import json
from types import SimpleNamespace
import pytest
from src.research.workspace_tools import research_workspace
from src.core import account_leases, tracing


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    from src import config, auth
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config, "settings", SimpleNamespace(RESEARCH_SCRIPT_IDLE_DAYS=7))
    monkeypatch.setattr(auth, "user_by_id", lambda owner: {"id": owner, "active": True})
    monkeypatch.setattr(account_leases, "_states", {})
    monkeypatch.setattr(tracing, "get_user_id", lambda: 31499)
    monkeypatch.setattr(tracing, "get_session_id", lambda: "workspace-test-session")
    token = account_leases.set_current_account_lease(None)
    yield
    account_leases.reset_current_account_lease(token)


def call(**kwargs):
    text, data = research_workspace.func(**kwargs)
    assert json.loads(text) == data
    return data


def test_save_read_patch_preserves_old_version():
    first = call(operation="save", name="analysis.py", source="x = 1\n")
    ref = first["script_ref"]
    second = call(operation="patch", script_ref=ref, expected_version=ref["version"],
                  old_text="1", new_text="2")
    assert second["script_ref"]["version"] == ref["version"] + 1
    assert call(operation="read", script_ref=ref)["source"] == "x = 1\n"
    assert call(operation="read", script_ref=second["script_ref"])["source"] == "x = 2\n"
    conflict = call(operation="patch", script_ref=ref, expected_version=ref["version"], old_text="1", new_text="3")
    assert conflict["_tool_outcome"]["status"] == "failed"
    assert conflict["_tool_outcome"]["reason_code"] == "script_version_conflict"
    assert conflict["write_applied"] is False
    assert conflict["latest_ref"] == second["script_ref"]
    current = call(operation="read", script_ref=conflict["latest_ref"])
    assert current["source"] == "x = 2\n"
    recovered = call(operation="patch", script_ref=current["script_ref"],
                     expected_version=current["script_ref"]["version"],
                     old_text="2", new_text="3")
    assert recovered["script_ref"]["version"] == 3
    assert call(operation="read", script_ref=ref)["source"] == "x = 1\n"


@pytest.mark.parametrize("invalid_scope", ["owner", "session", "hash", "latest_bytes"])
def test_conflict_does_not_disclose_unverified_latest_ref(monkeypatch, invalid_scope):
    from src.research import script_assets
    first = call(operation="save", source="x = 1\n")["script_ref"]
    second = call(operation="patch", script_ref=first, expected_version=1,
                  old_text="1", new_text="2")["script_ref"]
    if invalid_scope == "owner":
        monkeypatch.setattr(tracing, "get_user_id", lambda: 31500)
    elif invalid_scope == "session":
        monkeypatch.setattr(tracing, "get_session_id", lambda: "other-workspace-session")
    elif invalid_scope == "hash":
        first = {**first, "sha256": "b" * 64}
    else:
        with script_assets._connect() as conn:
            conn.execute("UPDATE versions SET source='tampered' WHERE id=? AND version=?",
                         (second["id"], second["version"]))
    rejected = call(operation="save", script_ref=first, expected_version=1, source="x = 3\n")
    assert rejected["_tool_outcome"]["status"] == "failed"
    assert "latest_ref" not in rejected


def test_builtin_derivation_keeps_origin_after_patch():
    from src.research.script_presets import get_preset
    preset = get_preset("wide_to_long")
    saved = call(operation="save", script_ref=preset["script_ref"])
    patched = call(operation="patch", script_ref=saved["script_ref"], expected_version=saved["script_ref"]["version"],
                   old_text='import pandas as pd', new_text='import pandas as pd\n# task revision')
    assert call(operation="read", script_ref=patched["script_ref"])["origin_ref"] == preset["script_ref"]
    assert get_preset("wide_to_long")["source"] == preset["source"]


def test_skill_resources_share_read_only_workspace_entrypoint():
    skills = call(operation="skills")["skills"]
    assert any(s["id"] == "nature-figure" and s["verified"] for s in skills)
    entry = call(operation="read_skill", skill_id="nature-figure", max_chars=60)
    assert entry["skill"]["read_only"] and len(entry["entrypoint"]["text"]) == 60
    rejected = call(operation="read_skill_resource", skill_id="nature-figure", resource="../../secret")
    assert rejected["_tool_outcome"]["status"] == "failed"


def test_intent_is_proposed_state_not_authorization():
    result = call(operation="intent", research_intent={"question": "compare variables", "units": {"x": "unknown"}})
    assert result["intent_status"] == "model_proposed_not_authorization"
    rejected = call(operation="intent", research_intent={"authorized_owner": 1})
    assert rejected["_tool_outcome"]["status"] == "failed"


@pytest.mark.parametrize("legacy_text", [False, True])
def test_readonly_figure_returns_owned_recovery_facts_without_saving(monkeypatch, legacy_text):
    from src.figures import projects
    ref = call(operation="save", source="pass")["script_ref"]
    dataset = {"kind": "dataset_snapshot", "id": "b" * 32, "version": 1, "sha256": "c" * 64}
    snapshot = {"variable": "tidy", "dataset_ref": dataset, "sha256": "c" * 64}
    source = {"script": "pass", "script_ref": ref, "run_id": "source-run",
              "input_snapshots": [{"variable": "data", "source_file_id": "original-file",
                                   "file_id": "private-snapshot", "path": "/private/path"}]}
    if legacy_text:
        source["frozen_render"] = {"version": 1, "input_snapshots": [snapshot],
            "receipt": {"geometry_unsupported": ["unsupported artist: Text"]}}
    project = {"figure_id": "owned", "source_kind": "script", "source_json": source,
               "render_version": 1, "manifest_json": {"objects": [],
               "data_provenance": {"dataset_refs": [dataset]}}, "exports_json": {}}
    def owned(figure_id, owner):
        assert figure_id == "owned" and owner == 31499
        return project
    monkeypatch.setattr(projects, "get_for_owner", owned)
    monkeypatch.setattr(projects, "save_overrides", lambda *a, **kw: pytest.fail("readonly must not save"))
    inspected = call(operation="figure_inspect", figure_id="owned")
    refused = call(operation="figure_edit", figure_id="owned", expected_revision=1,
                   overrides=[{"gid": "title", "prop": "color", "value": "purple"}])
    for result in (inspected, refused):
        assert result["editable"] is False and result["script_ref"] == ref
        assert result["run_id"] == "source-run" and result["dataset_refs"] == [dataset]
        assert result["input_bindings"] == {"data": {"file_id": "original-file"}}
        assert result["frozen_data_refs"] == ([snapshot] if legacy_text else [])
        assert result["edit_error"]["code"] == "analysis_not_frozen"
        assert ("旧版" if legacy_text else "独立渲染") in result["edit_limitation"]
        assert "/private/path" not in json.dumps(result)
        assert "private-snapshot" not in json.dumps(result)
    assert refused["_tool_outcome"]["status"] == "not_run"
    assert refused["_tool_outcome"]["reason_code"] == "figure_readonly"
    assert project["render_version"] == 1

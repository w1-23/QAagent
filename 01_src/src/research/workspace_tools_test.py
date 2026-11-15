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

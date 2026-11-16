"""Production Skill receipts and host integration, entirely in temporary stores."""
import asyncio
import json
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from src.agent import host
from src.agent.host_test import environment, install, action, scripted_response
from src.agent.identity import ExecutionIdentity
from src.agent.runtime import AdmissionStopped
from src.agent.skill_adapter import SkillSession, skill_catalog, validate_selected_skills, directory_prompt, _encode
from src.prompts import skill_registry as registry


@pytest.fixture
def skills_root(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    entries = []
    for sid, tools, deps, core in [
        ("statistics", ["analyze_research_data"], [], ["rules.txt"]),
        ("figure-initial", ["create_research_figure"], ["nature-figure"], []),
        ("nature-figure", ["create_research_figure"], [], ["rules.txt"]),
        ("figure-edit", ["research_workspace"], [], []),
    ]:
        package = root / sid
        package.mkdir()
        (package / "SKILL.md").write_text("method for " + sid + "\n", encoding="utf-8")
        for resource in core:
            (package / resource).write_text("Required rules for " + sid + "\n", encoding="utf-8")
        entries.append({"id": sid, "root": sid, "version": "1.0", "description": sid + " guidance",
            "source": "fixture", "entrypoint": "SKILL.md", "core": core, "depends_on": deps,
            "requires_any": tools, "execution": "existing_tools"})
    (root / "catalog.json").write_text(json.dumps({"schema_version": 1, "skills": entries}), encoding="utf-8")
    monkeypatch.setattr(registry, "ROOT", root)
    return root


def selection(sid):
    return {key: registry.skill_info(sid)[key] for key in ("id", "version", "sha256")}


def session(allowed=None):
    names = allowed if allowed is not None else {"analyze_research_data", "create_research_figure", "research_workspace", "generic", "read_skill"}
    return SkillSession(ExecutionIdentity(17, "session", "turn", "run"), lambda: names)


def observe_page(s, sid="statistics", resource="", start=0, size=3000, cid="page"):
    item = selection(sid)
    payload = s.page(sid, item["version"], item["sha256"], resource, start, size)
    m = ToolMessage(content=_encode(payload), tool_call_id=cid, id="message-" + cid, name="read_skill")
    s.observe(m)
    return m


def test_catalog_contract_and_public_middleware_directory(skills_root):
    rows = skill_catalog({"analyze_research_data"})
    assert [row["id"] for row in rows] == ["statistics"]
    assert "text" not in _encode(rows) and "method for" not in _encode(rows)
    chosen = selection("statistics")
    assert validate_selected_skills([chosen]) == [chosen]
    for invalid in ([chosen, chosen], [{**chosen, "version": "old"}], [{**chosen, "extra": True}], ["statistics"]):
        with pytest.raises(ValueError):
            validate_selected_skills(invalid)
    with pytest.raises(ValueError):
        validate_selected_skills([chosen], {"generic"})
    prompt = directory_prompt({"analyze_research_data"})
    assert "statistics" in prompt and chosen["sha256"] in prompt
    assert "method for statistics" not in prompt and "read_skill" in prompt


def test_partial_missing_wrong_version_and_generic_authorization(skills_root):
    s = session()
    first = observe_page(s, size=3)
    assert not s.verify_view([first])
    assert s.allowed({"analyze_research_data", "generic"}) == {"generic"}
    entry = observe_page(s, cid="entry")
    assert not s.verify_view([first, entry])  # missing required rules
    rules = observe_page(s, resource="rules.txt", cid="rules")
    assert s.verify_view([first, entry, rules]) == {"statistics"}
    assert "analyze_research_data" in s.allowed({"analyze_research_data"})
    with pytest.raises(ValueError):
        s.page("statistics", "old", selection("statistics")["sha256"])
    (skills_root / "statistics" / "rules.txt").write_text("changed rules", encoding="utf-8")
    assert not s.verify_view([first, entry, rules])
    assert s.allowed({"generic", "analyze_research_data"}) == {"generic"}


def test_selected_dependency_loading_and_free_later_edit(skills_root):
    s = session()
    messages = s.selected_messages([selection("figure-initial")])
    assert {r["payload"]["skill"]["id"] for r in s.receipts.values()} == {"figure-initial", "nature-figure"}
    assert s.verify_view(messages) == {"figure-initial", "nature-figure"}
    assert "create_research_figure" in s.allowed({"create_research_figure"})
    # A dependency removed after the model proposal revokes only that method.
    (skills_root / "nature-figure" / "rules.txt").unlink()
    assert s.allowed({"create_research_figure", "generic"}) == {"generic"}
    edits = session()
    messages = edits.selected_messages([selection("figure-edit")])
    edits.verify_view(messages)
    assert edits.active == {"figure-edit"}
    assert edits.permits_call({"name": "research_workspace", "args": {"operation": "figure_edit"}})
    assert not edits.permits_call({"name": "create_research_figure"})
    blank = session()
    assert blank.permits_call({"name": "research_workspace", "args": {"operation": "read"}})
    assert not blank.permits_call({"name": "research_workspace", "args": {"operation": "figure_edit"}})


def test_recovery_identity_permissions_and_active_projection(skills_root):
    names = {"analyze_research_data", "generic"}
    s = session(names)
    messages = s.selected_messages([selection("statistics")])
    s.verify_view(messages)
    saved = s.export()
    restored = session(names)
    restored.restore(saved)
    assert restored.verify_view(messages) == {"statistics"}
    with pytest.raises(AdmissionStopped, match="missing_from_active_projection"):
        restored.verify_view(messages[:-1])
    wrong = SkillSession(replace(s.identity, owner_user_id=99), lambda: names)
    with pytest.raises(AdmissionStopped, match="identity"):
        wrong.restore(saved)
    names.remove("analyze_research_data")
    assert not restored.verify_view(messages)
    restored.restore(None)  # legacy Nature/Word flags have no receipt authority
    assert not restored.active and not restored.receipts


def test_forged_or_truncated_observation_cannot_certify(skills_root):
    s = session()
    m = observe_page(s)
    payload = json.loads(m.content)
    payload["page"]["text"] = "forged method"
    s.observe(m.model_copy(update={"tool_call_id": "fake", "content": _encode(payload)}))
    s.observe(m.model_copy(update={"tool_call_id": "cut", "content": m.content[:20]}))
    assert set(s.receipts) == {"page"}
    assert not s.verify_view([m])
    assert s.required_message_ids == {"message-page"}


def test_host_skill_hash_checks_leave_event_loop_and_keep_identity(environment, skills_root, monkeypatch):
    import threading
    from src.core import tracing
    state, job = environment
    item = selection("statistics")
    state["selected_skills"] = [item]
    main_thread = threading.get_ident()
    checked_threads = []
    original = registry._sources

    def checked(entry):
        checked_threads.append(threading.get_ident())
        assert threading.get_ident() != main_thread
        assert tracing.get_job_id() == job
        assert tracing.get_user_id() == state["user_id"]
        return original(entry)

    monkeypatch.setattr(registry, "_sources", checked)
    sent, disclosed = _install_model(monkeypatch, [
        action("load_skill", skill_id=item["id"], version=item["version"], sha256=item["sha256"]),
        action("analyze_research_data", cid="specialist"), AIMessage(content="checked off loop")],
        [(analyze_research_data, False)])
    result = asyncio.run(host.general_agent_node(state))
    assert result["answer"] == "checked off loop"
    assert len(sent) == 4 and all("analyze_research_data" in names for names in disclosed)
    assert len(checked_threads) > 5  # Fresh checks for loading, active view and dispatch.


def _install_model(monkeypatch, responses, tools):
    install(monkeypatch, responses, tools)
    sent, disclosed = [], []
    class Driver:
        def __init__(self, model, catalog, allowed, **kwargs):
            self.allowed = allowed
        async def __call__(self, messages, identity):
            sent.append(messages)
            disclosed.append(self.allowed(identity))
            return scripted_response(responses, len(sent) - 1, messages)
    monkeypatch.setattr(host, "ModelDriver", Driver)
    return sent, disclosed


@tool(response_format="content_and_artifact")
def analyze_research_data():
    """Synthetic specialization used only by offline tests."""
    return "statistical result", {"_tool_outcome": {"status": "complete"}}


def test_host_explicit_selection_loaded_before_model(environment, skills_root, monkeypatch):
    state, job = environment
    state["selected_skills"] = [selection("statistics")]
    sent, disclosed = _install_model(monkeypatch,
        [action("analyze_research_data"), AIMessage(content="done")], [(analyze_research_data, False)])
    result = asyncio.run(host.general_agent_node(state))
    assert result["answer"] == "done" and "analyze_research_data" in disclosed[0]
    assert "Required rules for statistics" in str(sent[0])
    user = next(m for m in sent[0] if isinstance(m, HumanMessage))
    assert user.additional_kwargs["selected_skills"] == state["selected_skills"]
    assert user.additional_kwargs["user_text"] == "synthetic task"
    assert result["tools_called"] == ["analyze_research_data"]


def test_host_paged_discovery_and_direct_dispatch_gate(environment, skills_root, monkeypatch):
    state, job = environment
    item = selection("statistics")
    args = {"skill_id": item["id"], "version": item["version"], "sha256": item["sha256"]}
    sent, disclosed = _install_model(monkeypatch, [
        action("analyze_research_data", cid="blocked"),
        action("read_skill", cid="entry", **args),
        action("analyze_research_data", cid="missing-core"),
        action("read_skill", cid="rules", resource="rules.txt", **args),
        action("analyze_research_data", cid="allowed"), AIMessage(content="done")],
        [(analyze_research_data, False)])
    result = asyncio.run(host.general_agent_node(state))
    assert result["answer"] == "done"
    assert all("analyze_research_data" not in names for names in disclosed[:4])
    assert "analyze_research_data" in disclosed[4]
    assert all("read_context_evidence" in names and "read_skill" in names for names in disclosed)
    observations = {m.tool_call_id: m for m in result["turn_trace"] if isinstance(m, ToolMessage)}
    assert observations["blocked"].status == observations["missing-core"].status == "error"
    assert observations["allowed"].content == "statistical result"


def test_host_receipts_recover_after_committed_batch(environment, skills_root, monkeypatch):
    from src.agent.batch_journal import BatchJournal
    state, job = environment
    item = selection("statistics")
    args = {"skill_id": item["id"], "version": item["version"], "sha256": item["sha256"]}
    _install_model(monkeypatch, [AIMessage(content="", tool_calls=[
        {"name": "read_skill", "id": "entry", "args": args},
        {"name": "read_skill", "id": "rules", "args": {**args, "resource": "rules.txt"}}])],
        [(analyze_research_data, False)])
    original = BatchJournal.execute
    async def crash(self, response, execute):
        await original(self, response, execute)
        raise RuntimeError("offline_crash_after_commit")
    monkeypatch.setattr(BatchJournal, "execute", crash)
    first = asyncio.run(host.general_agent_node(state))
    assert first["turn_outcome"]["status"] != "completed"
    monkeypatch.setattr(BatchJournal, "execute", original)
    sent, disclosed = _install_model(monkeypatch, [action("analyze_research_data", cid="after-recovery"),
        AIMessage(content="recovered")], [(analyze_research_data, False)])
    result = asyncio.run(host.general_agent_node({**state, "agent_resume": True}))
    assert result["answer"] == "recovered" and "analyze_research_data" in disclosed[0]
    assert sum(isinstance(m, HumanMessage) for m in sent[0]) == 1


def test_load_skill_full_delivery_and_projection_protection(environment, skills_root, monkeypatch):
    from src.core.model_context import is_skill_result
    state, job = environment
    # Larger than first_delivery's 16k clipping threshold; still installed guidance.
    (skills_root / "statistics" / "rules.txt").write_text("Required method detail\n" * 1000, encoding="utf-8")
    item = selection("statistics")
    sent, disclosed = _install_model(monkeypatch, [
        action("load_skill", skill_id=item["id"], version=item["version"], sha256=item["sha256"]),
        action("analyze_research_data", cid="specialist"), AIMessage(content="loaded")],
        [(analyze_research_data, False)])
    result = asyncio.run(host.general_agent_node(state))
    assert result["answer"] == "loaded"
    assert "analyze_research_data" not in disclosed[0]
    assert "analyze_research_data" in disclosed[1]
    method = next(m for m in sent[1] if isinstance(m, ToolMessage) and m.name == "load_skill")
    assert len(method.content) > 16000 and "stored_result_with_partial_preview" not in method.content
    assert is_skill_result(method)
    assert json.loads(method.content)["skill_load"][-1]["page"]["next_start_char"] is None


def test_host_rejects_dropped_method_after_projection(environment, skills_root, monkeypatch):
    from src.agent.context_projection import WorkingContextProjection
    state, job = environment
    state["selected_skills"] = [selection("statistics")]
    sent, _ = _install_model(monkeypatch, [AIMessage(content="must not be sent")], [(analyze_research_data, False)])
    async def drop(self, history):
        return tuple(m for m in history if not str(m.id or "").startswith("skill-"))
    monkeypatch.setattr(WorkingContextProjection, "project", drop)
    result = asyncio.run(host.general_agent_node(state))
    assert not sent and result["turn_outcome"]["status"] != "completed"
    assert result["_trace"]["stop_reason"] == "required_method_missing_from_active_projection"


def test_oversized_load_does_not_issue_partial_receipts(skills_root, monkeypatch):
    from src.agent import skill_adapter
    monkeypatch.setattr(skill_adapter, "_MAX_LOAD_CHARS", 1)
    s = session()
    with pytest.raises(ValueError, match="too_large"):
        s.selected_messages([selection("statistics")])
    assert not s.receipts and not s.active
    # Paged reads remain usable; partial reads still cannot unlock the tool.
    page = observe_page(s, size=1)
    assert not s.verify_view([page])


def test_unavailable_dependency_fails_closed(skills_root):
    (skills_root / "nature-figure" / "rules.txt").unlink()
    with pytest.raises(ValueError):
        validate_selected_skills([selection("figure-initial")])
    with pytest.raises(ValueError):
        session().load([selection("figure-initial")])

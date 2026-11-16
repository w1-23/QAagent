"""Global registry readers retain no owner and execute in copied request context."""
import asyncio
import contextvars
from dataclasses import asdict
import json
import threading

import pytest

from src.agent.capabilities import CapabilityCatalog
from src.agent.identity import ExecutionIdentity
from src.agent.skill_adapter import SkillSession
from src.agent.skill_adapter_test import skills_root, selection  # noqa: F401
from src.agent import skill_tools
from src.tools import registry


@pytest.mark.parametrize("name", ["read_skill", "load_skill"])
def test_global_registry_reader_isolates_concurrent_owners_and_permissions(skills_root, monkeypatch, name):
    monkeypatch.setattr(registry, "_TOOL_SPECS", {})
    first_catalog, second_catalog = CapabilityCatalog(), CapabilityCatalog()
    skill_tools.register_skill_reader(first_catalog)
    first_tool = first_catalog.resolve(name, {name})
    skill_tools.register_skill_reader(second_catalog)
    second_tool = second_catalog.resolve(name, {name})
    # Both registrations publish the same stateless tool, not a latest-session closure.
    assert first_tool is second_tool is registry.get_tool_spec(name).func
    assert first_tool.func.__closure__ is None
    assert skill_tools._skill_session.get() is None

    first_allowed = {"analyze_research_data"}
    second_allowed = {"analyze_research_data"}
    first = SkillSession(ExecutionIdentity(17, "owner-one-session", "turn-one", "run-one"),
                         lambda: first_allowed)
    second = SkillSession(ExecutionIdentity(29, "owner-two-session", "turn-two", "run-two"),
                          lambda: second_allowed)
    selected = selection("statistics")
    args = {"skill_id": selected["id"], "version": selected["version"], "sha256": selected["sha256"]}
    rendezvous = threading.Barrier(2, timeout=10)
    current = skill_tools._current_session

    def overlapping_context():
        session = current()
        rendezvous.wait()  # Both worker threads enter before either can read.
        assert current() is session
        return session
    monkeypatch.setattr(skill_tools, "_current_session", overlapping_context)

    async def invoke(session, cid):
        with skill_tools.skill_execution_context(session):
            # Exercise registry's actual context-copy/worker route using spec.func,
            # including the ToolCall envelope so full content/artifact is returned.
            return await registry._invoke_tool_with_ctx(contextvars.copy_context(),
                registry.get_tool_spec(name).func,
                {"type": "tool_call", "name": name, "id": cid, "args": args})

    async def scenario():
        observed = await asyncio.gather(invoke(first, "first"), invoke(second, "second"))
        for session, message in zip((first, second), observed):
            data = json.loads(message.content)
            pages = data["skill_load"] if name == "load_skill" else [data]
            assert pages and all(page["identity"] == asdict(session.identity) for page in pages)
            session.observe(message)
            assert session.receipts
        # Another owner's valid authorization must not cover this revocation.
        second_allowed.clear()
        allowed, denied = await asyncio.gather(invoke(first, "allowed"), invoke(second, "denied"),
                                                return_exceptions=True)
        assert not isinstance(allowed, BaseException)
        assert isinstance(denied, ValueError)
        assert skill_tools._skill_session.get() is None
    asyncio.run(scenario())
    assert skill_tools._skill_session.get() is None


@pytest.mark.parametrize("name", ["read_skill", "load_skill"])
def test_unbound_registry_reader_and_exception_cleanup_fail_closed(skills_root, monkeypatch, name):
    monkeypatch.setattr(registry, "_TOOL_SPECS", {})
    catalog = CapabilityCatalog()
    skill_tools.register_skill_reader(catalog)
    catalog.resolve(name, {name})
    selected = selection("statistics")
    args = {"skill_id": selected["id"], "version": selected["version"], "sha256": selected["sha256"]}
    with pytest.raises(PermissionError, match="skill_execution_context_required"):
        registry.get_tool_spec(name).func.invoke(args)
    session = SkillSession(ExecutionIdentity(17, "session", "turn", "run"), lambda: {"analyze_research_data"})
    with pytest.raises(asyncio.CancelledError):
        with skill_tools.skill_execution_context(session):
            assert skill_tools._current_session() is session
            raise asyncio.CancelledError()
    assert skill_tools._skill_session.get() is None
    with pytest.raises(PermissionError, match="skill_execution_context_required"):
        registry.get_tool_spec(name).func.invoke(args)

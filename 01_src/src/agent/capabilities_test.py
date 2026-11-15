"""Optional provider failure cannot remove unrelated general capabilities."""
import subprocess
import sys
from pathlib import Path

import pytest

from src.agent.capabilities import (CapabilityCatalog, CapabilityDefinition,
                                    CapabilityUnavailable)


def definition(name, factory, provider="core", schema=None):
    return CapabilityDefinition(name, "Synthetic capability", schema or {
        "type": "object", "properties": {}}, factory, provider=provider)


def test_discovery_and_schemas_do_not_import_optional_implementations():
    calls = []
    catalog = CapabilityCatalog()
    catalog.register(definition("read", lambda: calls.append("read") or object()))
    catalog.register(definition("figure", lambda: calls.append("figure") or object(), "science"))
    assert {d["state"] for d in catalog.describe()} == {"discoverable"}
    assert len(catalog.schemas({"read", "figure"})) == 2
    assert calls == []
    first = catalog.resolve("read", {"read"})
    assert catalog.resolve("read", {"read"}) is first
    assert calls == ["read"]


def test_missing_plugin_does_not_disable_general_tools_or_leak_error_text():
    catalog = CapabilityCatalog()
    def missing():
        raise ImportError("synthetic private path must not become public metadata")
    core = object()
    catalog.register(definition("read", lambda: core))
    catalog.register(definition("figure", missing, "science"))
    with pytest.raises(CapabilityUnavailable, match="load_failed:ImportError") as error:
        catalog.resolve("figure", {"read", "figure"})
    assert "private path" not in str(error.value)
    assert catalog.resolve("read", {"read"}) is core
    assert [s["function"]["name"] for s in catalog.schemas({"read", "figure"})] == ["read"]
    failed = next(d for d in catalog.describe() if d["name"] == "figure")
    assert failed["state"] == "unavailable"
    assert failed["reason"] == "load_failed:ImportError"


def test_real_permission_is_checked_again_after_loading():
    catalog = CapabilityCatalog()
    catalog.register(definition("write", object))
    catalog.resolve("write", {"write"})
    with pytest.raises(CapabilityUnavailable, match="not_authorized"):
        catalog.resolve("write", set())
    assert catalog.schemas(set()) == []


def test_contract_is_not_mutated_by_callers_or_returned_schemas():
    schema = {"type": "object", "properties": {"path": {"type": "string"}}}
    catalog = CapabilityCatalog()
    catalog.register(definition("read", object, schema=schema))
    schema["properties"].clear()
    first = catalog.schemas({"read"})
    first[0]["function"]["parameters"]["properties"].clear()
    assert "path" in catalog.schemas({"read"})[0]["function"]["parameters"]["properties"]
    with pytest.raises(ValueError, match="duplicate"):
        catalog.register(definition("read", object))


def test_readiness_refresh_is_scoped_and_does_not_execute_any_tool():
    catalog = CapabilityCatalog()
    loaded = []
    def load():
        loaded.append(True)
        if len(loaded) == 1:
            raise ImportError("missing")
        return "implementation"
    core = object()
    catalog.register(definition("read", lambda: core))
    catalog.register(definition("figure", load, "science"))
    catalog.resolve("read", {"read"})
    with pytest.raises(CapabilityUnavailable):
        catalog.resolve("figure", {"figure"})
    catalog.invalidate("science")
    assert len(loaded) == 1
    assert catalog.resolve("read", {"read"}) is core
    assert catalog.resolve("figure", {"figure"}) == "implementation"


def test_core_imports_without_research_figures_or_configuration():
    root = str(Path(__file__).resolve().parents[2])
    script = '''
import importlib.abc
import sys
class BlockDomains(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"src.config", "src.research", "src.figures", "src.tools"} or fullname.startswith(("src.research.", "src.figures.", "src.tools.")):
            raise AssertionError("domain/config imported by core: " + fullname)
sys.meta_path.insert(0, BlockDomains())
sys.path.insert(0, sys.argv[1])
from src.agent.runtime import AgentRuntime
from src.agent.capabilities import CapabilityCatalog
from src.agent.identity import ExecutionIdentity
print("independent")
'''
    result = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", script, root],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "independent"

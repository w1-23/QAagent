"""Installed presets stay discoverable with all domain imports blocked."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from src.agent import preset_catalog
from src.agent.capabilities import CapabilityUnavailable


_ROOT = str(Path(__file__).resolve().parents[2])


def _isolated(script: str) -> None:
    # -I prevents PYTHONPATH, cwd and user-site leakage; -B avoids cache writes.
    # Each child is new, so sys.modules cannot hide a forbidden eager import.
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-X", "utf8", "-c",
         "import sys\nsys.path.insert(0, sys.argv[1])\n" + script, _ROOT],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_discovery_with_domains_registry_config_and_langchain_blocked():
    _isolated('''
import importlib.abc
blocked = ("src.research", "src.figures", "src.tools", "src.config", "langchain_core")
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in blocked):
            raise AssertionError("eager import: " + fullname)
assert not any(any(n == p or n.startswith(p + ".") for p in blocked) for n in sys.modules)
sys.meta_path.insert(0, BlockImports())
from src.agent.preset_catalog import build_preset_catalog, preset_bindings, preset_metadata
catalog = build_preset_catalog()
names = set(preset_bindings())
assert len(names) == 25
assert len(catalog.schemas(names)) == 25
assert catalog.schemas(set()) == []
assert {d["state"] for d in catalog.describe()} == {"discoverable"}
assert set(preset_metadata()) == names
assert catalog.execution_mode("request_user_input") == "interaction"
assert catalog.execution_mode("save_research_note") == "sequential"
assert catalog.execution_mode("read_account_file") == "parallel"
assert not any(any(n == p or n.startswith(p + ".") for p in blocked) for n in sys.modules)
print("ok")
''')


def test_selected_general_tools_load_with_domains_and_legacy_blocked():
    _isolated('''
import importlib.abc
from types import SimpleNamespace
blocked = ("src.research", "src.figures", "src.tools.legacy_registry")
attempts = []
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in blocked):
            attempts.append(fullname)
            raise ModuleNotFoundError("blocked optional module")
sys.meta_path.insert(0, BlockImports())
from src.agent.preset_catalog import build_preset_catalog, preset_bindings
from src.agent.capabilities import CapabilityUnavailable
from src.tools.registry import register_tool, get_tool_spec, _TOOL_SPECS
register_tool(SimpleNamespace(name="existing_plugin"), is_readonly=True)
sentinel = get_tool_spec("existing_plugin")
catalog = build_preset_catalog()
assert set(_TOOL_SPECS) == {"existing_plugin"}
try:
    catalog.resolve("create_research_figure", set())
except CapabilityUnavailable as error:
    assert error.reason == "not_authorized_or_ready"
else:
    raise AssertionError("unauthorized resolution succeeded")
assert attempts == []
for name in ("request_user_input", "read_account_file"):
    tool = catalog.resolve(name, {name})
    assert tool.name == name
    assert get_tool_spec(name).func is tool
    assert catalog.resolve(name, {name}) is tool
assert attempts == []
assert set(_TOOL_SPECS) == {"existing_plugin", "request_user_input", "read_account_file"}
assert get_tool_spec("read_account_file").recovery_mode == "local_read"
try:
    catalog.resolve("create_research_figure", {"create_research_figure"})
except CapabilityUnavailable as error:
    assert error.reason == "load_failed:ModuleNotFoundError"
else:
    raise AssertionError("missing optional tool resolved")
assert attempts == ["src.research"]
assert get_tool_spec("create_research_figure") is None
assert get_tool_spec("existing_plugin") is sentinel
assert len(catalog.schemas(set(preset_bindings()))) == 24
assert catalog.resolve("read_account_file", {"read_account_file"}).name == "read_account_file"
assert not any(any(n == p or n.startswith(p + ".") for p in blocked) for n in sys.modules)
print("ok")
''')


def test_installed_declarations_schemas_and_effective_metadata_match():
    # Legacy import intentionally lives only in this disposable child: its
    # existing clear_tool_registry() must not touch the test runner's specs.
    _isolated('''
import ast
from pathlib import Path
from types import SimpleNamespace
from langchain_core.utils.function_calling import convert_to_openai_tool
from src.tools.legacy_registry import _TOOL_DECLARATIONS
from src.tools.registry import get_tool_spec, register_tool, _TOOL_SPECS
from src.agent.preset_catalog import build_preset_catalog, preset_bindings, preset_metadata, _TARGETS
source = ast.parse((Path(sys.argv[1]) / "src/tools/legacy_registry.py").read_text(encoding="utf-8"))
imports = {alias.asname or alias.name: (node.module, alias.name)
           for node in source.body if isinstance(node, ast.ImportFrom)
           for alias in node.names}
assert all(imports[name] == (module, name) for name, module in _TARGETS.items())
baseline = {tool.name: get_tool_spec(tool.name) for tool, *_ in _TOOL_DECLARATIONS}
register_tool(SimpleNamespace(name="existing_plugin"), is_readonly=True)
sentinel = get_tool_spec("existing_plugin")
catalog = build_preset_catalog()
bindings = preset_bindings()
metadata = preset_metadata()
assert len(_TOOL_DECLARATIONS) == len(bindings) == len(metadata) == 25
assert set(baseline) == set(bindings) == set(_TARGETS)
assert all(get_tool_spec(name) is spec for name, spec in baseline.items())
assert catalog.schemas(set(bindings)) == [convert_to_openai_tool(t) for t, *_ in _TOOL_DECLARATIONS]
for tool, category, key, scope, requires, produces, authorization in _TOOL_DECLARATIONS:
    name = tool.name
    assert bindings[name] == (tuple(requires), tuple(produces), authorization)
    meta = metadata[name]
    assert meta == {
        "category": category, "concurrency_key": key,
        "write_scope": scope, "is_readonly": scope is None,
        "authorization": authorization, "recovery_mode": baseline[name].recovery_mode,
        "execution_mode": "interaction" if name == "request_user_input" else "sequential" if scope is not None else "parallel",
    }
    assert catalog.execution_mode(name) == meta["execution_mode"]
    assert next(d for d in catalog.describe() if d["name"] == name)["mutates"] == (scope is not None)
    assert catalog.resolve(name, {name}) is tool
    spec = get_tool_spec(name)
    for field in ("category", "concurrency_key", "write_scope", "is_readonly", "authorization", "recovery_mode"):
        assert getattr(spec, field) == getattr(baseline[name], field), (name, field)
assert get_tool_spec("existing_plugin") is sentinel
assert set(_TOOL_SPECS) == set(bindings) | {"existing_plugin"}
print("ok")
''')


@pytest.mark.parametrize("field", ["name", "description", "parameters"])
def test_schema_drift_fails_before_registration_or_dispatch(monkeypatch, field):
    from src.tools import registry

    schemas = preset_catalog._read_manifest()
    expected = deepcopy(schemas)
    function = expected["read_account_file"]["function"]
    if field == "parameters":
        function[field]["properties"]["unexpected_parameter"] = {"type": "string"}
    else:
        function[field] += "_changed"
    # Keep the whitelist key unchanged, simulating an outdated installed schema.
    monkeypatch.setattr(preset_catalog, "_read_manifest", lambda: expected)
    registered = []
    monkeypatch.setattr(registry, "register_tool", lambda *args, **kwargs: registered.append(args))
    catalog = preset_catalog.build_preset_catalog()
    with pytest.raises(CapabilityUnavailable, match="load_failed:ValueError"):
        catalog.resolve("read_account_file", {"read_account_file"})
    assert registered == []
    assert catalog.schemas({"read_account_file"}) == []
    assert catalog.schemas({"request_user_input"})


def test_discovery_returns_independent_contracts_and_policy_records():
    first = preset_catalog.build_preset_catalog()
    bindings = preset_catalog.preset_bindings()
    metadata = preset_catalog.preset_metadata()
    names = set(bindings)
    expected = first.schemas(names)
    exposed = first.schemas(names)
    exposed[0]["function"]["parameters"].clear()
    bindings.clear()
    metadata["save_research_note"]["authorization"] = ""
    metadata["read_account_file"]["recovery_mode"] = "never"
    assert first.schemas(names) == expected
    assert preset_catalog.build_preset_catalog().schemas(names) == expected
    assert len(preset_catalog.preset_bindings()) == 25
    assert preset_catalog.preset_metadata()["save_research_note"]["authorization"] == "explicit_user_request"
    assert preset_catalog.preset_metadata()["read_account_file"]["recovery_mode"] == "local_read"


@pytest.mark.parametrize("defect", ["missing", "duplicate", "unknown", "target", "version", "parameters"])
def test_invalid_installed_manifest_is_rejected_without_imports(monkeypatch, defect):
    manifest = {"version": 1, "tools": list(preset_catalog._read_manifest().values())}
    if defect == "missing":
        manifest["tools"].pop()
    elif defect == "duplicate":
        manifest["tools"].append(deepcopy(manifest["tools"][0]))
    elif defect == "unknown":
        manifest["tools"][0]["function"]["name"] = "user_supplied_command"
    elif defect == "target":
        manifest["tools"][0]["function"]["module"] = "user_supplied_module"
    elif defect == "version":
        manifest["version"] = 2
    elif defect == "parameters":
        manifest["tools"][0]["function"]["parameters"] = {"type": "string"}

    class Resource:
        def joinpath(self, name):
            assert name == "preset_tool_schemas.json"
            return self

        def read_text(self, encoding):
            return json.dumps(manifest)

    monkeypatch.setattr(preset_catalog, "files", lambda package: Resource())
    def forbidden_import(name):
        pytest.fail("manifest attempted executable import: " + name)
    monkeypatch.setattr(preset_catalog, "import_module", forbidden_import)
    with pytest.raises(ValueError):
        preset_catalog.build_preset_catalog()

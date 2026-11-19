"""The code-owned manifest must equal what the runtime loader accepts.

`canonical_tool_schema` normalizes docstring indentation before comparing a
tool with its manifest entry. A generator that skips that normalization writes
a manifest no runtime can load, which only surfaces when the tool is first
selected. This guard keeps generation and loading on the same definition.
"""
from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path

from src.agent.preset_catalog import _PRESETS, _TARGETS, canonical_tool_schema


def test_manifest_matches_canonical_tool_schemas():
    manifest = json.loads(
        (Path(__file__).parent / "preset_tool_schemas.json").read_text(encoding="utf-8"))
    expected = {tool["function"]["name"]: tool for tool in manifest["tools"]}
    assert set(expected) == {preset.name for preset in _PRESETS}
    for preset in _PRESETS:
        tool = getattr(import_module(_TARGETS[preset.name]), preset.name)
        assert canonical_tool_schema(tool) == expected[preset.name], preset.name

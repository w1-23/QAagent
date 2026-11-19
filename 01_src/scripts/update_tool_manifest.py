"""Regenerate the code-owned preset schemas in an installed development env.

From _scratch/local_service_c37f40a:
  PYTHONPATH=../../01_src python -B ../../01_src/scripts/update_tool_manifest.py
Add --check to verify the installed manifest without writing. No tool is invoked;
legacy declarations are parsed, never imported (that import clears tool specs).
"""
from __future__ import annotations

import argparse
import ast
from importlib import import_module
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    from src.agent.preset_catalog import _PRESETS, _TARGETS, canonical_tool_schema

    root = Path(__file__).resolve().parents[1]
    source = ast.parse((root / "src/tools/legacy_registry.py").read_text(encoding="utf-8"))
    imports = {alias.asname or alias.name: (node.module, alias.name)
               for node in source.body if isinstance(node, ast.ImportFrom)
               for alias in node.names}
    declarations = next(node.value for node in source.body
                        if isinstance(node, ast.Assign) and any(
                            isinstance(t, ast.Name) and t.id == "_TOOL_DECLARATIONS"
                            for t in node.targets))
    expected = []
    for row in declarations.elts:
        name = row.elts[0].id
        if imports[name] != (_TARGETS.get(name), name):
            raise ValueError("preset_import_target_drift:" + name)
        expected.append((name, *(ast.literal_eval(value) for value in row.elts[1:])))
    actual = [(p.name, p.category, p.concurrency_key, p.write_scope,
               p.requires, p.produces, p.authorization) for p in _PRESETS]
    if actual != expected:
        raise ValueError("preset_metadata_drift")

    schemas = []
    for preset in _PRESETS:
        tool = getattr(import_module(_TARGETS[preset.name]), preset.name)
        # The loader compares this exact normalized form; keep both sides equal.
        schema = canonical_tool_schema(tool)
        if schema["function"]["name"] != preset.name:
            raise ValueError("preset_tool_name_drift:" + preset.name)
        schemas.append(schema)
    manifest = {"version": 1, "tools": schemas}
    destination = root / "src/agent/preset_tool_schemas.json"
    if args.check:
        if json.loads(destination.read_text(encoding="utf-8")) != manifest:
            raise ValueError("preset_schema_manifest_outdated")
        print(f"Verified {len(schemas)} preset schemas")
    else:
        destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Updated {len(schemas)} preset schemas")


if __name__ == "__main__":
    main()

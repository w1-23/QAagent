"""Allowlisted worker source bundle shared by image refresh and Apptainer.

Only service-owned execution modules and hash-verified skill resources enter
this bundle. Never copy an application directory or runtime data recursively.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

from .nature_runtime import COMMIT, SOURCE

ENGINE_FILES = (
    "__init__.py", "spec.py", "styling.py", "core.py", "runtime.py",
    "manifest.py", "overrides.py", "exporter.py", "script_outputs.py",
    "dataset_codec.py", "nature_runtime.py",
)
METHOD_FILES = ("audit_methods.py", "data_audit_rules.py", "summary_consistency.py")


def bundle_files() -> dict[str, bytes]:
    source = Path(__file__).resolve().parents[1]
    result = {f"src/figures/{name}": (source / "figures" / name).read_bytes()
              for name in ENGINE_FILES}
    result["worker.py"] = (source / "figures/worker.py").read_bytes()
    result.update({f"src/research/{name}": (source / "research" / name).read_bytes()
                   for name in METHOD_FILES})
    prefix = "src/prompts/skills/vendor/nature-skills/"
    vendor = source / "prompts/skills/vendor/nature-skills"
    raw = (vendor / "provenance.json").read_bytes()
    manifest = json.loads(raw)
    if manifest.get("source") != SOURCE or manifest.get("commit") != COMMIT:
        raise ValueError("Worker skill provenance identity mismatch")
    result[prefix + "provenance.json"] = raw
    seen = set()
    for record in manifest["files"]:
        name = record["path"]
        path = PurePosixPath(name)
        if (not name or path.is_absolute() or "\\" in name or ":" in name
                or any(part in {"", ".", ".."} for part in name.split("/"))
                or name in seen):
            raise ValueError("Invalid worker skill resource path")
        seen.add(name)
        target = vendor.joinpath(*path.parts)
        if not target.resolve().is_relative_to(vendor.resolve()):
            raise ValueError("Worker skill resource outside package")
        data = target.read_bytes()
        if hashlib.sha256(data).hexdigest() != record.get("sha256"):
            raise ValueError("Worker skill resource hash mismatch: " + name)
        result[prefix + name] = data
    if "LICENSE" not in seen:
        raise ValueError("Worker skill license missing")
    return result


def stage_engine(target: Path) -> None:
    files = bundle_files()  # Validate every resource before writing anything.
    target.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

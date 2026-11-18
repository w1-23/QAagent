"""Explicit, hash-validated access to immutable upstream helpers. No discovery or network."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

SOURCE = "https://github.com/Yuan1z0825/nature-skills"
COMMIT = "7bff1d0539c0dfad6c30178bb93ce6dd8a5cebf8"
SKILL_VERSION = "2.8.0"
# Canonical inventory fingerprint binds the injected provenance itself to this
# reviewed release; changing both a script and its local hash cannot bless it.
PROVENANCE_FILES_SHA256 = "a9b8e661bf460a401e17017f134a735432da9d7832e3f561a63c6cf47fb92b9e"
_MODULES = frozenset({"validate_figure", "audit_panel_alignment", "audit_pdf_text",
                      "audit_figure_collisions", "figure_safety", "plot_templates", "audit_svg_schematic"})


def validate_vendor(vendor_root, *, verify_files=True):
    root = Path(vendor_root).resolve(strict=True)
    provenance = json.loads((root / "provenance.json").read_bytes())
    if provenance.get("source") != SOURCE or provenance.get("commit") != COMMIT:
        raise ValueError("vendor_identity_mismatch")
    seen = set()
    for entry in provenance["files"]:
        relative = entry["path"]
        path = (root / relative).resolve(strict=verify_files)
        if relative in seen or not path.is_relative_to(root):
            raise ValueError("vendor_path_mismatch")
        seen.add(relative)
        if verify_files and hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("vendor_hash_mismatch: " + relative)
    inventory = json.dumps(sorted(provenance["files"], key=lambda row: row["path"]), sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(inventory).hexdigest() != PROVENANCE_FILES_SHA256:
        raise ValueError("vendor_inventory_hash_mismatch")
    return provenance


def load_module(name, *, vendor_root):
    if name not in _MODULES:
        raise ValueError("unapproved_vendor_module")
    root = Path(vendor_root).resolve(strict=True)
    # The inventory itself is pinned, but an absent unrelated peripheral must
    # not disable this check. Validate only the helper being executed here;
    # validate_vendor(root) separately proves full resource completeness.
    provenance = validate_vendor(root, verify_files=False)
    relative = f"skills/nature-figure/scripts/{name}.py"
    entry = next((r for r in provenance["files"] if r["path"] == relative), None)
    if entry is None:
        raise FileNotFoundError(relative)
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError("vendor_path_mismatch")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != entry["sha256"]:
        raise ValueError("vendor_hash_mismatch")
    module_name = "_citrus_nature_" + name + "_" + digest
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    if name == "audit_svg_schematic":
        # Pinned 7bff1d0 transform_matrix uses math without importing it.
        # Keep vendor bytes unchanged; declare the shim in each SVG receipt.
        import math
        module.__dict__["math"] = math
        module.__dict__["__citrus_compatibility__"] = ["pinned_svg_missing_math_import"]
    try:
        exec(compile(data, str(path), "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def configure_style(*, vendor_root):
    module = load_module("plot_templates", vendor_root=vendor_root)
    module.require_dependencies()
    module.configure_style()


def interp_monotone(target, xp, fp, *, vendor_root):
    return load_module("figure_safety", vendor_root=vendor_root).interp_monotone(target, xp, fp)


def label_y_above(values, spread=None, pad_fraction=0.04, *, vendor_root):
    return load_module("figure_safety", vendor_root=vendor_root).label_y_above(values, spread, pad_fraction)

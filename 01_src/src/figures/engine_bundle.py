"""Allowlisted worker source bundle shared by image refresh and Apptainer.

Only service-owned execution modules and hash-verified skill resources enter
this bundle. Never copy an application directory or runtime data recursively.
"""
from __future__ import annotations

import base64
import hashlib
from importlib import metadata
import json
import sysconfig
from pathlib import Path, PurePosixPath

from .nature_runtime import COMMIT, SOURCE

ENGINE_FILES = (
    "__init__.py", "spec.py", "styling.py", "core.py", "runtime.py",
    "manifest.py", "overrides.py", "exporter.py", "script_outputs.py",
    "dataset_codec.py", "frozen_render.py", "worker.py", "nature_runtime.py", "render_policy.py", "svg_projection.py",
)
METHOD_FILES = ("audit_methods.py", "data_audit_rules.py", "summary_consistency.py", "integrity.py")
SUPPORT_VERSION = "2.0.0"
# The worker needs deterministic tables and figures, not provider HTTP clients.
SUPPORT_PACKAGES = frozenset({"citrus_audit", "citrus_figures"})


def _support_files() -> dict[str, bytes]:
    """Stage the installed, non-editable wheel and verify every RECORD hash.

    This is the common source for Docker, image refresh and Apptainer. Never
    recursively copy a checkout, site-packages, an environment, or runtime data.
    """
    # A source-tree *.egg-info generated while building must not shadow the
    # installed wheel when this function is called from a developer checkout.
    installed = [dist for dist in metadata.distributions(
        path=sorted({sysconfig.get_path("purelib"), sysconfig.get_path("platlib")}))
        if str(dist.metadata.get("Name", "")).lower().replace("_", "-")
        == "citrusscholar-support"]
    if len(installed) != 1:
        raise ValueError("Install exactly one non-editable support wheel in this interpreter")
    distribution = installed[0]
    if distribution.version != SUPPORT_VERSION:
        raise ValueError("Install the matching citrusscholar-support wheel first")
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    if direct.get("dir_info", {}).get("editable"):
        raise ValueError("Worker bundle requires a non-editable support wheel")
    result = {}
    for entry in distribution.files or ():
        name = entry.as_posix()
        parts = PurePosixPath(name).parts
        if not parts or parts[0] not in SUPPORT_PACKAGES or not name.endswith(".py"):
            continue
        if any(part in {".", "..", "tests", "__pycache__"} for part in parts):
            raise ValueError("Invalid support wheel member: " + name)
        if entry.hash is None or entry.hash.mode != "sha256":
            raise ValueError("Support wheel member has no SHA-256 RECORD: " + name)
        data = Path(distribution.locate_file(entry)).read_bytes()
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        if digest != entry.hash.value or len(data) != entry.size:
            raise ValueError("Installed support wheel integrity mismatch: " + name)
        result[name] = data
    if any(package + "/__init__.py" not in result for package in SUPPORT_PACKAGES):
        raise ValueError("Support wheel execution package missing")
    record = distribution.read_text("RECORD")
    if not record:
        raise ValueError("Support wheel RECORD missing")
    manifest = {"schema": "citrus.worker-support/1", "distribution": distribution.metadata["Name"],
                "version": distribution.version,
                "record_sha256": hashlib.sha256(record.encode()).hexdigest(),
                "members": {name: hashlib.sha256(data).hexdigest()
                            for name, data in sorted(result.items())}}
    result["support-manifest.json"] = json.dumps(manifest, sort_keys=True).encode()
    return result


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
    result.update(_support_files())
    # New independent resources retain their own catalog/provenance identity;
    # v1 worker receipts above still refer to their original resource set.
    from citrus_figures.nature_runtime import validate_vendor
    skill_vendor = source.parents[1] / "skills/nature-figure/vendor/nature-skills"
    inventory = validate_vendor(skill_vendor)
    skill_prefix = "skills/nature-figure/vendor/nature-skills/"
    result[skill_prefix + "provenance.json"] = (skill_vendor / "provenance.json").read_bytes()
    for record in inventory["files"]:
        result[skill_prefix + record["path"]] = (skill_vendor / record["path"]).read_bytes()
    return result


def stage_engine(target: Path) -> dict[str, str]:
    files = bundle_files()  # Validate every resource before writing anything.
    target.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}

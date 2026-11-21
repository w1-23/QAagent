"""Pinned root-release discovery and exact, bounded resource reads.

The manifest authenticates the release identity; only requested guidance bytes
are read. Vendor scripts/assets are optional resources, never imports or tools.
The archived prompt registry remains an independent compatibility namespace.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re

ROOT = Path(__file__).resolve().parents[3]
CATALOG_PATH = "02_config/skills.catalog.json"
PINNED = {
    "candidate-search": ("2.0.0", "0c216b5803a20b50290a72b44d4e019f3f5cf4c256fd3b3549fd844465ae4d50"),
    "reference-verification": ("2.0.0", "1d2f55aaec2531b178d79dd005a44a11d3d04c0cc50e54f72f75c3813fbf3a62"),
    "data-audit": ("2.1.0", "42be5d63a924075901c5cc79dfa6fd698bbf565632097f2a253b26bd567f6bb7"),
    "nature-figure": ("2.1.0", "ed8c4840844904f7ccf7be7845382db58b7127534b9b4c1792de35570af30e7b"),
}
_ARCHIVED_NATURE_IDENTITY = ("2.0.0", "00779e01d2c8756245c3553adb8abf451c4eb1dcd15c46ce7192ce3892d7f127")
_ARCHIVED_NATURE_ROOT = "02_config/skill-history/nature-figure/2.0.0"
_ARCHIVED_NATURE_RESOURCES = frozenset({
    "SKILL.md", "provenance.json", "provenance.extra.json", "references/bridge.md",
})
_ARCHIVED_DATA_AUDIT_IDENTITY = ("2.0.0", "87247d295c5fcb2bd97ac833361afb0f9376c69bc35de309f5a0e09839b60339")
_ARCHIVED_DATA_AUDIT_ROOT = "02_config/skill-history/data-audit/2.0.0"
_MAX_CATALOG_BYTES = 256_000
_MAX_RESOURCE_BYTES = 1_000_000
_MAX_FILES = 512


def _path(root: Path, name: str) -> Path:
    if (type(name) is not str or not name or len(name) > 300 or "\\" in name or ":" in name
            or "\x00" in name or any(part in {"", ".", ".."} for part in name.split("/"))):
        raise ValueError("invalid_skill_resource_path")
    relative = PurePosixPath(name)
    if relative.is_absolute():
        raise ValueError("invalid_skill_resource_path")
    candidate = root.joinpath(*relative.parts)
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError("skill_resource_outside_package")
    current = candidate
    while current != root:
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError("skill_resource_link_prohibited")
        current = current.parent
    return candidate


def _bytes(path: Path, limit: int) -> bytes:
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("skill_resource_size_limit")
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError("skill_resource_size_limit")
    return content


def _catalog():
    data = json.loads(_bytes(_path(ROOT, CATALOG_PATH), _MAX_CATALOG_BYTES))
    if (type(data) is not dict or data.get("schema") != "citrus.skill-catalog/1"
            or data.get("version") != "2.0.0" or type(data.get("skills")) is not list
            or len(data["skills"]) != len(PINNED)):
        raise ValueError("invalid_research_skill_catalog")
    rows = data["skills"]
    if any(type(row) is not dict for row in rows):
        raise ValueError("invalid_research_skill_catalog")
    ids = [row.get("id") for row in rows]
    if any(type(sid) is not str for sid in ids) or set(ids) != set(PINNED):
        raise ValueError("invalid_research_skill_catalog")
    return rows


def _manifest(row, identity):
    sid = row["id"]
    if ((row.get("version"), row.get("sha256")) != identity
            or row.get("path") != "skills/" + sid or row.get("entry") != "SKILL.md"):
        raise ValueError("research_skill_identity_mismatch")
    files = row.get("files")
    if type(files) is not list or not 1 <= len(files) <= _MAX_FILES:
        raise ValueError("invalid_skill_manifest")
    records = {}
    for record in files:
        if type(record) is not dict or set(record) != {"path", "size", "sha256"}:
            raise ValueError("invalid_skill_manifest_record")
        name = record["path"]
        # Validate syntax here; optional resources need not be touched at discovery.
        _path(_path(ROOT, row["path"]), name)
        if (name in records or type(record["size"]) is not int
                or not 0 <= record["size"] <= _MAX_RESOURCE_BYTES
                or type(record["sha256"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is None):
            raise ValueError("invalid_skill_manifest_record")
        records[name] = record
    digest = hashlib.sha256(json.dumps(files, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    if digest != row["sha256"] or "SKILL.md" not in records:
        raise ValueError("skill_manifest_integrity_mismatch")
    return records


def _text(row, records, resource):
    if resource not in records:
        raise ValueError("skill_resource_not_in_manifest")
    record = records[resource]
    archived = (row["id"] == "nature-figure"
                and (row["version"], row["sha256"]) == _ARCHIVED_NATURE_IDENTITY)
    source = _ARCHIVED_NATURE_ROOT if archived and resource in _ARCHIVED_NATURE_RESOURCES else row["path"]
    if (row["id"] == "data-audit" and resource == "SKILL.md"
            and (row["version"], row["sha256"]) == _ARCHIVED_DATA_AUDIT_IDENTITY):
        source = _ARCHIVED_DATA_AUDIT_ROOT
    content = _bytes(_path(_path(ROOT, source), resource), record["size"])
    if len(content) != record["size"] or hashlib.sha256(content).hexdigest() != record["sha256"]:
        raise ValueError("skill_resource_integrity_mismatch")
    return content.decode("utf-8")


def _info(row, records):
    text = _text(row, records, "SKILL.md")
    header = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if header is None:
        raise ValueError("invalid_skill_frontmatter")
    fields = {key: value.strip() for key, value in
              re.findall(r"^(name|description):[ \t]*(.+)$", header[1], re.M)}
    version = re.search(r"^(?:  )?version:[ \t]*[\"']?([^\"'\r\n]+)[\"']?[ \t]*$", header[1], re.M)
    if fields.get("name") != row["id"] or version is None or version[1].strip() != row["version"]:
        raise ValueError("research_skill_identity_mismatch")
    # Local method references suffice for default loads. Vendored libraries,
    # scripts, templates and tests remain exact-manifest optional resources.
    resources = ["SKILL.md", *sorted(name for name in records
                 if name.startswith("references/") and name.endswith(".md"))]
    return {"id": row["id"], "version": row["version"], "sha256": row["sha256"],
            "description": fields.get("description", row["id"]), "applicability": "",
            "entrypoint": "SKILL.md", "required_resources": resources,
            "depends_on": [], "requires_any": [], "verified": True}


def list_skills():
    """Discovery failures are local; no root-release failure gates generic work."""
    try:
        rows = _catalog()
    except (OSError, ValueError, KeyError, TypeError):
        return []
    result = []
    for row in rows:
        try:
            result.append(_info(row, _manifest(row, PINNED[row["id"]])))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result


def has_identity(skill_id, version, sha256):
    identity = (version, sha256)
    return (PINNED.get(skill_id) == identity
            or (skill_id == "nature-figure" and identity == _ARCHIVED_NATURE_IDENTITY)
            or (skill_id == "data-audit" and identity == _ARCHIVED_DATA_AUDIT_IDENTITY))


def resolve(skill_id, version, sha256):
    """Resolve current and archived guidance by full, pinned identity."""
    identity = (version, sha256)
    if not has_identity(skill_id, version, sha256):
        raise ValueError("research_skill_identity_mismatch")
    archived_root = {("nature-figure", _ARCHIVED_NATURE_IDENTITY): _ARCHIVED_NATURE_ROOT,
                     ("data-audit", _ARCHIVED_DATA_AUDIT_IDENTITY): _ARCHIVED_DATA_AUDIT_ROOT}.get((skill_id, identity))
    if archived_root:
        row = json.loads(_bytes(_path(ROOT, archived_root + "/manifest.json"), _MAX_CATALOG_BYTES))
        if type(row) is not dict or row.get("id") != skill_id:
            raise ValueError("research_skill_identity_mismatch")
    else:
        row = next((entry for entry in _catalog() if entry["id"] == skill_id), None)
        if row is None:
            raise ValueError("research_skill_unavailable")
    records = _manifest(row, identity)
    return row, records, _info(row, records)


def read_resource(skill_id, version, sha256, resource="", *, start_char=0, max_chars=3000):
    if (type(start_char) is not int or start_char < 0
            or type(max_chars) is not int or not 1 <= max_chars <= 20000):
        raise ValueError("invalid_skill_pagination")
    row, records, info = resolve(skill_id, version, sha256)
    name = resource or info["entrypoint"]
    text = _text(row, records, name)
    start = min(start_char, len(text))
    end = min(start + max_chars, len(text))
    page = {"skill_id": skill_id, "version": version, "resource": name,
            "source": "installed:research-skills", "commit": "", "sha256": records[name]["sha256"],
            "text": text[start:end], "total_chars": len(text), "start_char": start,
            "next_start_char": end if end < len(text) else None,
            "content_origin": "installed_skill_guidance", "read_only": True}
    return info, page

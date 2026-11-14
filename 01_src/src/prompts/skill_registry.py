"""Read-only skill packages: catalog, verified text, and bounded resource access.

A skill supplies guidance; it cannot register a tool, import a plugin, install
packages or grant permissions. New packages are added to the service catalog.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent / "skills"


def _path(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise ValueError("Invalid skill resource path")
    relative = PurePosixPath(name)
    if relative.is_absolute() or any(p in {".", ".."} for p in name.split("/")):
        raise ValueError("Invalid skill resource path")
    candidate = root.joinpath(*relative.parts)
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError("Skill resource outside package")
    current = candidate
    while current != root:
        if current.is_symlink():
            raise ValueError("Skill resources must not be symlinks")
        current = current.parent
    return candidate


def _catalog() -> list[dict]:
    data = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("skills"), list):
        raise ValueError("Unsupported skill catalog")
    entries = data["skills"]
    ids = [entry["id"] for entry in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate skill id")
    return entries


def _entry(skill_id: str) -> dict:
    for entry in _catalog():
        if entry["id"] == skill_id:
            return entry
    raise ValueError("Skill is not registered")


def _sources(entry: dict) -> dict[str, str]:
    root = _path(ROOT, entry["root"])
    if entry.get("provenance"):
        provenance = json.loads(_path(root, entry["provenance"]).read_text(encoding="utf-8"))
        if provenance.get("commit") != entry.get("commit") or provenance.get("source") != entry["source"]:
            raise ValueError("Skill source/revision mismatch")
        records = provenance["files"]
    else:
        records = [{"path": name} for name in dict.fromkeys([entry["entrypoint"], *entry.get("core", [])])]
    sources = {}
    for record in records:
        name = record["path"]
        if name in sources:
            raise ValueError("Duplicate skill resource")
        content = _path(root, name).read_bytes()
        if len(content) > 1_000_000:
            raise ValueError("Skill resource exceeds size limit")
        if record.get("sha256") and hashlib.sha256(content).hexdigest() != record["sha256"]:
            raise ValueError("Skill resource integrity mismatch: " + name)
        if entry.get("provenance") and not record.get("sha256"):
            raise ValueError("Missing skill resource digest")
        sources[name] = content.decode("utf-8")
    for required in [entry["entrypoint"], *entry.get("core", [])]:
        if required not in sources:
            raise ValueError("Missing required skill resource: " + required)
    if entry.get("provenance") and "LICENSE" not in sources:
        raise ValueError("Missing upstream license")
    return sources


def verified_sources(skill_id: str) -> dict[str, str]:
    """No execution or implicit imports; validation is repeated after file changes."""
    return _sources(_entry(skill_id))


def _metadata(entry: dict, sources: dict) -> dict:
    digest = hashlib.sha256(json.dumps(
        {name: hashlib.sha256(text.encode()).hexdigest() for name, text in sorted(sources.items())},
        sort_keys=True).encode()).hexdigest()
    return {"id": entry["id"], "version": entry["version"], "description": entry["description"],
            "source": entry["source"], "commit": entry.get("commit", ""),
            "license": entry.get("license", "service"), "sha256": digest,
            "entrypoint": entry["entrypoint"], "execution": entry["execution"],
            "read_only": True, "verified": True,
            "resources": [{"path": name, "chars": len(text)} for name, text in sources.items()]}


def list_skills(available_tools: set[str] | None = None) -> list[dict]:
    result = []
    for entry in _catalog():
        if available_tools is not None and not set(entry.get("requires_any", [])).intersection(available_tools):
            continue
        try:
            meta = _metadata(entry, _sources(entry))
            meta.pop("resources")
            result.append(meta)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result.append({"id": entry["id"], "description": entry["description"],
                           "verified": False, "available": False,
                           "reason": type(exc).__name__})
    return result


def skill_info(skill_id: str) -> dict:
    entry = _entry(skill_id)
    return _metadata(entry, _sources(entry))


def read_resource(skill_id: str, resource: str = "", *, start_char: int = 0, max_chars: int = 12000) -> dict:
    entry = _entry(skill_id)
    sources = _sources(entry)
    name = resource or entry["entrypoint"]
    if name not in sources:
        raise ValueError("Resource is not part of the installed skill")
    if type(start_char) is not int or start_char < 0 or type(max_chars) is not int or max_chars <= 0:
        raise ValueError("Invalid skill pagination")
    text = sources[name]
    start = min(start_char, len(text))
    end = min(start + min(max_chars, 20000), len(text))
    return {"skill_id": skill_id, "version": entry["version"], "resource": name,
            "sha256": hashlib.sha256(text.encode()).hexdigest(), "text": text[start:end],
            "total_chars": len(text), "start_char": start,
            "next_start_char": end if end < len(text) else None,
            "content_origin": "installed_skill_guidance", "read_only": True}


def core_guidance(skill_id: str) -> str:
    entry = _entry(skill_id)
    sources = _sources(entry)
    return "\n\n".join(f'<skill_resource name="{name}">\n{sources[name]}\n</skill_resource>'
                       for name in [entry["entrypoint"], *entry.get("core", [])])

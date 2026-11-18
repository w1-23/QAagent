"""Explicit readonly projections, never a repository filesystem backend.

Enumeration reads catalog metadata only. File bytes are opened only for a
requested page/reference or a bounded search, with the private-store link rules.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

ROOT = Path(__file__).absolute().parents[3]
# Product implementation texts reviewed for this projection. No globs, config,
# credentials, state, environment directories, uploads or runtime data roots.
SOURCE_FILES = frozenset({
    "01_src/src/agent/capabilities.py", "01_src/src/agent/execution.py",
    "01_src/src/agent/identity.py", "01_src/src/agent/host_tools.py",
    "01_src/src/agent/workspace_storage.py", "01_src/src/agent/workspace_tools.py",
    "01_src/src/agent/workspace_projection.py", "01_src/src/agent/research_skill_catalog.py",
    "01_src/src/agent/skill_tools.py", "01_src/src/research/tools.py",
    "01_src/src/research/reference_extraction.py", "01_src/src/research/data_audit_rules.py",
    "01_src/src/retrieval/candidate_pool.py", "01_src/src/retrieval/bm25.py",
    "01_src/src/figures/spec.py", "01_src/src/figures/manifest.py",
})
ROOTS = ("/input", "/data", "/skills", "/01_src", "/workspace", "/outputs")


def within(key, scope):
    return scope == "/" or key == scope or key.startswith(scope + "/")


def bounded_bytes(path: Path, limit: int) -> bytes:
    from .workspace_storage import _no_links
    _no_links(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise ValueError("workspace_file_limit_or_type")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        _no_links(path)
        after = path.stat(follow_symlinks=False)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise PermissionError("workspace_file_changed")
        if len(data) > limit:
            raise ValueError("workspace_file_limit_or_type")
        return data
    finally:
        os.close(fd)


def resources(scope):
    """Yield exact allowed names and metadata; never read resource bodies here."""
    from .workspace_storage import MAX_FILE_BYTES, virtual_path
    if scope == "/" or scope.startswith("/01_src"):
        for name in sorted(SOURCE_FILES):
            key = "/" + name
            if within(key, scope):
                yield key, {"kind": "workspace_source", "path": key, "read_only": True}
    if scope != "/" and not scope.startswith("/skills"):
        return
    from . import research_skill_catalog as catalog
    try:
        data = json.loads(bounded_bytes(catalog.ROOT / catalog.CATALOG_PATH, catalog._MAX_CATALOG_BYTES))
        if (type(data) is not dict or data.get("schema") != "citrus.skill-catalog/1"
                or data.get("version") != "2.0.0" or type(data.get("skills")) is not list):
            return
        rows = data["skills"]
        if (len(rows) != len(catalog.PINNED) or any(type(row) is not dict for row in rows)
                or any(type(row.get("id")) is not str for row in rows)
                or {row["id"] for row in rows} != set(catalog.PINNED)):
            return
    except (OSError, ValueError, TypeError, KeyError):
        return  # Release availability must not gate generic private workspace use.
    for row in rows:
        if scope != "/" and not within(scope, "/skills/" + row["id"]) and not within("/skills/" + row["id"], scope):
            continue
        try:
            records = catalog._manifest(row)
        except (OSError, ValueError, TypeError, KeyError):
            continue
        for name, record in records.items():
            key = "/" + row["path"] + "/" + name
            if not within(key, scope) or record["size"] > MAX_FILE_BYTES:
                continue
            try:
                virtual_path(key)
            except ValueError:
                continue  # Only bounded supported text resources, never binary assets.
            yield key, {"kind": "workspace_skill_resource", "path": key,
                        "sha256": record["sha256"], "size_bytes": record["size"],
                        "skill_id": row["id"], "skill_version": row["version"],
                        "catalog_sha256": row["sha256"], "read_only": True}


def read(key, descriptor):
    from .workspace_storage import MAX_FILE_BYTES
    if descriptor["kind"] == "workspace_source":
        if key[1:] not in SOURCE_FILES:
            raise PermissionError("workspace_projection_denied")
        root = ROOT
    else:
        from . import research_skill_catalog as catalog
        root = catalog.ROOT
    path = root.joinpath(*key[1:].split("/"))
    # Lexical allowlist plus ancestor link checks, never caller-selected host paths.
    data = bounded_bytes(path, MAX_FILE_BYTES)
    digest = hashlib.sha256(data).hexdigest()
    if descriptor["kind"] == "workspace_skill_resource" and (
            digest != descriptor["sha256"] or len(data) != descriptor["size_bytes"]):
        raise ValueError("workspace_skill_integrity_mismatch")
    text = data.decode("utf-8")
    if "\x00" in text:
        raise ValueError("workspace_text_required")
    return text, {**descriptor, "sha256": digest, "size_bytes": len(data)}

"""FigureDocument v1: identity and references in the existing project row.

The project columns remain the sole owners of scientific data, manifests,
overrides and exported bytes. This document adds no renderer or file store.
An explicit renderer key is an author declaration, not an inferred match or
proof of scientific equivalence. Legacy Artist gids are render-local only.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import re
import uuid
from typing import Any

SCHEMA = "citrus.figure-document/1"
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}")


class DocumentTargetError(ValueError):
    """A document target cannot be resolved at the requested revision."""


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def build_figure_document(project: dict[str, Any]) -> dict[str, Any]:
    """Build current identity bindings; call only within a project save.

    Semantic ids are scoped to this project and role and survive render order
    changes. Unregistered/ambiguous objects receive fresh render-scoped ids.
    No label, gid, locator, geometry or source hash enters a semantic id.
    """
    from citrus_figures.manifest import EDITABLE_BY_ROLE

    figure_id = str(project["figure_id"])
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, "citrus.figure-document:" + figure_id)
    render_id = uuid.uuid4()
    manifest = project.get("manifest_json") or {}
    source = project.get("source_json") or {}
    objects = manifest.get("objects") or []
    objects = [(index, item) for index, item in enumerate(objects) if isinstance(item, dict)]
    keys = Counter(item.get("semantic_key") for _, item in objects
                   if isinstance(item.get("semantic_key"), str))
    gids = Counter(item.get("gid") for _, item in objects if isinstance(item.get("gid"), str))
    bindings = []
    for index, item in objects:
        key, role, gid = item.get("semantic_key"), item.get("role"), item.get("gid")
        reason = "unregistered"
        if manifest.get("semantic_identity_version") == 1 and key is not None:
            if (not isinstance(key, str) or not _KEY.fullmatch(key) or not isinstance(role, str)
                    or role not in {*EDITABLE_BY_ROLE, "figure"}):
                reason = "invalid_semantic_key"
            elif keys[key] != 1:
                reason = "ambiguous_key"
            elif item.get("identity_scope") == "semantic":
                reason = "explicit_key"
            else:
                reason = "unverified_semantic_key"
        if not isinstance(gid, str) or not gid or gids[gid] != 1:
            reason = "ambiguous_or_missing_gid"
        semantic = reason == "explicit_key"
        object_id = (uuid.uuid5(namespace, json.dumps([key, role], separators=(",", ":")))
                     if semantic else uuid.uuid5(render_id, str(index)))
        binding = {
            "object_id": "obj_" + object_id.hex,
            "role": role if isinstance(role, str) else "",
            "identity_scope": "semantic" if semantic else "render",
            "identity_reason": reason,
            "manifest_ref": f"/manifest_json/objects/{index}",
            "gid": gid if isinstance(gid, str) else "",
            "resolvable": isinstance(gid, str) and bool(gid) and gids[gid] == 1,
        }
        if semantic:
            binding["semantic_key"] = key
        bindings.append(binding)

    frozen = source.get("frozen_render")
    receipt = frozen.get("receipt") if isinstance(frozen, dict) else None
    script = project.get("source_kind") == "script" or "script" in source
    science = {
        "state": "receipt_recorded" if script and isinstance(receipt, dict) else "not_frozen",
        "style_replay": "existing_script_frozen_guard" if script else "declarative_resolve_required",
    }
    if not script and 'resolved_snapshot' in source:
        from src.figures.declarative_snapshot import load_snapshot
        load_snapshot(source)
        science.update(state="resolved_snapshot_recorded",
                       style_replay="resolved_snapshot_no_resolution",
                       snapshot_ref="/source_json/resolved_snapshot",
                       resolved_sha256=source['resolved_snapshot']['resolved_sha256'])
    if script and isinstance(receipt, dict):
        # Hash the existing evidence, do not upgrade it to verified by reading it.
        science.update(receipt_ref="/source_json/frozen_render/receipt",
                       receipt_sha256=_digest(receipt))
    return {
        "schema": SCHEMA, "document_id": figure_id,
        "revision": int(project.get("render_version") or 0),
        "render_id": render_id.hex,
        "source_sha256": _digest(source),
        "refs": {name: "/" + name + "_json" for name in
                 ("source", "manifest", "overrides", "exports", "data_refs")},
        "science": science,
        "objects": bindings,
    }


def resolve_document_object(document: dict[str, Any], object_id: str, *,
                            expected_revision: int) -> dict[str, Any]:
    """Resolve only within an exact current revision; never guess a fallback.

    This is the data foundation for semantic commands, not a replacement for
    the existing override validator or source/frozen-render verification.
    """
    if document.get("schema") != SCHEMA:
        raise DocumentTargetError("unsupported_document")
    if type(expected_revision) is not int or expected_revision != document.get("revision"):
        raise DocumentTargetError("revision_conflict")
    matches = [item for item in document.get("objects", []) if item.get("object_id") == object_id]
    if len(matches) != 1 or not matches[0].get("resolvable"):
        raise DocumentTargetError("unresolved_object")
    return dict(matches[0])

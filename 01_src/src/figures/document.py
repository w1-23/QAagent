"""FigureDocument v1: identity and references in the existing project row.

The project columns remain the sole owners of scientific data, manifests,
overrides and exported bytes. This document adds no renderer or file store.
An explicit renderer key is an author declaration, not an inferred match or
proof of scientific equivalence. Legacy Artist gids are render-local only.

Distribution marks are drawn from a frozen, versioned geometry, but the source
declares no per-bin or per-group key. This document never derives one from a
label, traversal index, geometry or hash: those marks stay render-scoped and
the limitation is disclosed explicitly. A creation-time downgrade is reported
with its reason instead of being hidden, and malformed persisted state fails
closed rather than claiming a freeze it cannot prove.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import re
import uuid
from typing import Any

SCHEMA = "citrus.figure-document/1"
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}")
_DOWNGRADE_CODES = frozenset({"scientific_geometry_not_exactly_replayable",
                              "unexpected_scientific_geometry"})


class DocumentTargetError(ValueError):
    """A document target cannot be resolved at the requested revision."""


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _freeze_downgrade(source: dict[str, Any]) -> dict[str, str] | None:
    """Return the explicit creation-time downgrade reason, or ``None``.

    ``prepare_source`` writes this marker only when distribution geometry was
    present but cannot be replayed exactly. A marker that is missing pieces,
    names an unknown code/chart or contradicts a frozen snapshot fails closed;
    it is never repaired by guessing or silently dropped.
    """
    if "freeze_downgrade" not in source:
        return None
    from src.figures.declarative_snapshot import (DISTRIBUTION_CHARTS, PAIRED_CHARTS,
                                                  SnapshotError)
    reason = source["freeze_downgrade"]
    if (not isinstance(reason, dict)
            or set(reason) != {"code", "chart", "panel_id"}
            or reason.get("code") not in _DOWNGRADE_CODES
            or reason.get("chart") not in DISTRIBUTION_CHARTS | PAIRED_CHARTS
            or not isinstance(reason.get("panel_id"), str)):
        raise SnapshotError("invalid_freeze_downgrade")
    return deepcopy(reason)


def _distribution_limitations(resolved: Any) -> list[dict[str, str]]:
    """Disclose frozen distribution marks that have no source-owned key.

    Histogram bins and boxplot groups are exact frozen geometry, but the
    author declares no stable key for an individual bin/group. The document
    must not invent one from labels, order or geometry; it records the
    render-scoped limitation so the UI can show it, and editing stays
    fail-closed because no semantic object id exists for those marks.
    """
    from src.figures.declarative_snapshot import DISTRIBUTION_CHARTS
    limitations = []
    for leaf in resolved.panels or (resolved,):
        if leaf.chart in DISTRIBUTION_CHARTS:
            limitations.append({
                "code": "distribution_marks_render_scoped",
                "chart": leaf.chart,
                "panel_id": leaf.panel_id,
                "reason": "no_source_owned_distribution_key",
            })
    return limitations


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
    downgrade = _freeze_downgrade(source)
    if script and downgrade is not None:
        # Script sources are frozen by their existing receipt guard, not by the
        # declarative distribution contract. Mixing the markers is ambiguous.
        from src.figures.declarative_snapshot import SnapshotError
        raise SnapshotError("invalid_freeze_downgrade")
    distribution: list[dict[str, str]] = []
    if not script and "resolved_snapshot" in source:
        from src.figures.declarative_snapshot import SnapshotError, load_snapshot
        if downgrade is not None:
            raise SnapshotError("invalid_freeze_downgrade")
        resolved, _ = load_snapshot(source)
        if resolved is None:
            raise SnapshotError("invalid_resolved_snapshot")
        distribution = _distribution_limitations(resolved)
        science.update(state="resolved_snapshot_recorded",
                       style_replay="resolved_snapshot_no_resolution",
                       snapshot_ref="/source_json/resolved_snapshot",
                       resolved_sha256=source["resolved_snapshot"]["resolved_sha256"])
    elif downgrade is not None:
        # The original spec stays renderable by resolution, but it was not
        # frozen exactly. Report the reason instead of hiding the downgrade.
        from src.figures.declarative_snapshot import DISTRIBUTION_CHARTS
        science.update(state="downgraded",
                       style_replay="legacy_reanalysis",
                       downgrade_ref="/source_json/freeze_downgrade",
                       downgrade=deepcopy(downgrade))
        if downgrade["chart"] in DISTRIBUTION_CHARTS:
            # A downgraded distribution still has marks but no frozen or
            # source-owned identity; disclose that rather than implying one.
            distribution = [{
                "code": "distribution_marks_render_scoped",
                "chart": downgrade["chart"],
                "panel_id": downgrade["panel_id"],
                "reason": "freeze_downgraded",
            }]
    if script and isinstance(receipt, dict):
        # Hash the existing evidence, do not upgrade it to verified by reading it.
        science.update(receipt_ref="/source_json/frozen_render/receipt",
                       receipt_sha256=_digest(receipt))
    render_bindings = [binding for binding in bindings
                       if binding["identity_scope"] != "semantic"]
    identity = {
        "semantic": len(bindings) - len(render_bindings),
        "render": len(render_bindings),
        "render_scoped_marks": sorted({binding["role"] for binding in render_bindings
                                       if binding["role"] in {"series", "bar", "point",
                                                              "errorbar"}}),
        "distribution": distribution,
    }
    return {
        "schema": SCHEMA, "document_id": figure_id,
        "revision": int(project.get("render_version") or 0),
        "render_id": render_id.hex,
        "source_sha256": _digest(source),
        "refs": {name: "/" + name + "_json" for name in
                 ("source", "manifest", "overrides", "exports", "data_refs")},
        "science": science,
        "identity": identity,
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

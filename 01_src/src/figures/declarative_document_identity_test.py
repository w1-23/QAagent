"""Engineering-only document checks for frozen distributions and downgrades.

A downgrade reason must be displayed, distribution marks without a
source-owned key must stay explicitly render-scoped, and contradictory or
malformed persisted state must fail closed instead of claiming a freeze.
"""
from copy import deepcopy
from dataclasses import replace

import pytest

from citrus_figures.core import build_resolved_figure
from citrus_figures.manifest import build_manifest
from citrus_figures.resolve import resolve_spec
from citrus_figures.spec import parse_spec
from src.figures.declarative_snapshot import SnapshotError, prepare_source
from src.figures.document import build_figure_document

VALUES = [1, 2, 2, 4, 7, 13, 50]
DISTRIBUTION_REASON = {
    "code": "distribution_marks_render_scoped",
    "reason": "no_source_owned_distribution_key",
}


def payload(chart, values=None):
    return {"chart": chart, "title": "ENGINEERING_ONLY document identity",
            "x_label": "value",
            "y_label": "count" if chart == "histogram" else "value",
            "y": {"kind": "inline", "values": list(values if values is not None else VALUES)},
            "qa_profile": {"caption": "ENGINEERING_ONLY software fixture."}}


def resolved(chart, values=None, raw=None):
    spec = parse_spec(raw or payload(chart, values))
    value, info = resolve_spec(spec)
    return spec, value, info


def project(source, manifest=None, *, source_kind="spec", render_version=1):
    return {"figure_id": "a" * 32, "source_kind": source_kind,
            "source_json": source, "manifest_json": manifest or {},
            "render_version": render_version}


def document_for(chart, values=None, raw=None):
    spec, value, info = resolved(chart, values, raw)
    source = prepare_source(spec, value, info)
    manifest = build_manifest(build_resolved_figure(value), include_qa=False)
    return source, build_figure_document(project(source, manifest))


def test_downgrade_reason_is_displayed_and_never_claims_a_freeze():
    spec, value, info = resolved("histogram")
    damaged = deepcopy(value.scientific_geometry)
    damaged["version"] = 999
    source = prepare_source(spec, replace(value, scientific_geometry=damaged), info)
    assert "resolved_snapshot" not in source

    document = build_figure_document(project(source))
    science = document["science"]
    assert science["state"] == "downgraded"
    assert science["style_replay"] == "legacy_reanalysis"
    assert science["downgrade_ref"] == "/source_json/freeze_downgrade"
    assert science["downgrade"] == source["freeze_downgrade"]
    assert science["downgrade"] == {
        "code": "scientific_geometry_not_exactly_replayable",
        "chart": "histogram", "panel_id": ""}
    assert "snapshot_ref" not in science
    assert "resolved_sha256" not in science
    assert document["identity"]["distribution"] == [{
        "code": "distribution_marks_render_scoped", "chart": "histogram",
        "panel_id": "", "reason": "freeze_downgraded"}]


def test_frozen_histogram_discloses_render_scoped_bars():
    _, document = document_for("histogram")
    assert document["science"]["state"] == "resolved_snapshot_recorded"
    assert document["identity"]["distribution"] == [
        {**DISTRIBUTION_REASON, "chart": "histogram", "panel_id": ""}]
    assert "bar" in document["identity"]["render_scoped_marks"]

    bars = [item for item in document["objects"] if item["role"] == "bar"]
    assert bars and all(item["identity_scope"] == "render" for item in bars)
    assert all(item["identity_reason"] == "unregistered" for item in bars)
    assert all(item["resolvable"] for item in bars)


def test_frozen_boxplot_groups_stay_render_scoped_without_invented_ids():
    raw = payload("boxplot", [1, 2, 2, 4, 7, 13, 50, 2, 3, 9])
    raw["group"] = {"kind": "inline", "values": ["a"] * 7 + ["b"] * 3}
    _, document = document_for("boxplot", raw=raw)
    assert document["science"]["state"] == "resolved_snapshot_recorded"
    assert document["identity"]["distribution"] == [
        {**DISTRIBUTION_REASON, "chart": "boxplot", "panel_id": ""}]
    # No per-group key exists in the source contract, so no mark object may be
    # invented from the frozen labels, order or geometry.
    assert not [item for item in document["objects"]
                if item["role"] in {"series", "bar", "point", "errorbar"}]
    assert document["identity"]["semantic"] == 0


def test_legacy_source_is_not_frozen_and_needs_resolution():
    document = build_figure_document(project({"kind": "spec", "spec": {}}))
    assert document["science"]["state"] == "not_frozen"
    assert document["science"]["style_replay"] == "declarative_resolve_required"
    assert document["identity"]["distribution"] == []
    assert document["identity"]["semantic"] == 0


@pytest.mark.parametrize("reason", [
    None,
    "scientific_geometry_not_exactly_replayable",
    {"code": "scientific_geometry_not_exactly_replayable",
     "chart": "histogram"},
    {"code": "other_reason", "chart": "histogram", "panel_id": ""},
    {"code": "scientific_geometry_not_exactly_replayable",
     "chart": "pie", "panel_id": ""},
    {"code": "scientific_geometry_not_exactly_replayable",
     "chart": "histogram", "panel_id": None},
    {"code": "scientific_geometry_not_exactly_replayable",
     "chart": "histogram", "panel_id": "", "extra": "x"},
])
def test_malformed_downgrade_fails_closed(reason):
    source = {"kind": "spec", "spec": {}, "freeze_downgrade": reason}
    with pytest.raises(SnapshotError, match="invalid_freeze_downgrade"):
        build_figure_document(project(source))


def test_downgrade_and_frozen_snapshot_cannot_coexist():
    spec, value, info = resolved("histogram")
    source = prepare_source(spec, value, info)
    assert "resolved_snapshot" in source
    source["freeze_downgrade"] = {
        "code": "scientific_geometry_not_exactly_replayable",
        "chart": "histogram", "panel_id": ""}
    with pytest.raises(SnapshotError, match="invalid_freeze_downgrade"):
        build_figure_document(project(source))


def test_corrupt_frozen_snapshot_fails_closed():
    spec, value, info = resolved("histogram")
    source = prepare_source(spec, value, info)
    source["resolved_snapshot"] = deepcopy(source["resolved_snapshot"])
    source["resolved_snapshot"]["resolved_sha256"] = "0" * 64
    with pytest.raises(SnapshotError):
        build_figure_document(project(source))


def test_ambiguous_semantic_key_stays_render_scoped():
    manifest = {"semantic_identity_version": 1, "objects": [
        {"gid": "loc-a", "role": "bar", "semantic_key": "bin/0",
         "identity_scope": "semantic"},
        {"gid": "loc-b", "role": "bar", "semantic_key": "bin/0",
         "identity_scope": "semantic"},
    ]}
    document = build_figure_document(project({"kind": "spec", "spec": {}}, manifest))
    assert [item["identity_scope"] for item in document["objects"]] == ["render", "render"]
    assert [item["identity_reason"] for item in document["objects"]] == [
        "ambiguous_key", "ambiguous_key"]
    assert document["identity"]["render_scoped_marks"] == ["bar"]


def test_explicit_semantic_key_is_still_semantic():
    manifest = {"semantic_identity_version": 1, "objects": [
        {"gid": "loc-a", "role": "bar", "semantic_key": "bin/0",
         "identity_scope": "semantic"},
    ]}
    document = build_figure_document(project({"kind": "spec", "spec": {}}, manifest))
    binding, = document["objects"]
    assert binding["identity_scope"] == "semantic"
    assert binding["identity_reason"] == "explicit_key"
    assert binding["semantic_key"] == "bin/0"
    assert document["identity"]["semantic"] == 1
    assert document["identity"]["render_scoped_marks"] == []

def test_unexpected_paired_geometry_downgrade_is_displayed():
    raw = {"chart": "bar", "title": "ENGINEERING_ONLY paired downgrade",
           "x_label": "category", "y_label": "value",
           "x": {"kind": "inline", "values": ["a", "b"]},
           "y": {"kind": "inline", "values": [1, 2]},
           "qa_profile": {"caption": "ENGINEERING_ONLY software fixture."}}
    spec, value, info = resolved("bar", raw=raw)
    source = prepare_source(spec, replace(value, scientific_geometry={
        "version": 1, "kind": "histogram", "counts": [1.0], "edges": [0.0, 1.0]}), info)
    assert source["freeze_downgrade"]["code"] == "unexpected_scientific_geometry"

    document = build_figure_document(project(source))
    assert document["science"]["state"] == "downgraded"
    assert document["science"]["downgrade"] == {
        "code": "unexpected_scientific_geometry", "chart": "bar", "panel_id": ""}


def test_downgrade_reason_is_scoped_to_the_panel():
    profile = {"caption": "ENGINEERING_ONLY software fixture."}
    panel = {key: item for key, item in payload("histogram").items()
             if key != "qa_profile"}
    panel["panel_id"] = "panelA"
    spec = parse_spec({"qa_profile": profile, "panels": [panel]})
    value, info = resolve_spec(spec)
    leaf = value.panels[0]
    damaged = deepcopy(leaf.scientific_geometry)
    damaged["version"] = 999
    value = replace(value, panels=(replace(leaf, scientific_geometry=damaged),))
    source = prepare_source(spec, value, info)
    assert source["freeze_downgrade"] == {
        "code": "scientific_geometry_not_exactly_replayable",
        "chart": "histogram", "panel_id": "panelA"}

    document = build_figure_document(project(source))
    assert document["science"]["downgrade"]["panel_id"] == "panelA"
    assert document["identity"]["distribution"] == [{
        "code": "distribution_marks_render_scoped", "chart": "histogram",
        "panel_id": "panelA", "reason": "freeze_downgraded"}]

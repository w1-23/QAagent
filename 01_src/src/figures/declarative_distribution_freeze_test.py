"""Exact histogram/box replay from frozen declarative geometry.

Engineering-only software fixtures: no scientific inference. A frozen
distribution must draw bit-identical artist geometry to the legacy path, must
never recompute statistics, and must be downgraded explicitly when its geometry
cannot be replayed exactly.
"""
from copy import deepcopy
from dataclasses import replace
import json

import numpy as np
import pytest

from citrus_figures.core import build_resolved_figure
from citrus_figures.resolve import resolve_spec
from citrus_figures.spec import parse_spec
from src.figures.declarative_snapshot import (SCHEMA, SnapshotError, confirm_render,
                                              digest, load_snapshot, prepare_source,
                                              unsupported_reason)

VALUES = [1, 2, 2, 4, 7, 13, 50]


def payload(chart, values=None):
    return {'chart': chart, 'title': 'ENGINEERING_ONLY exact geometry',
            'x_label': 'value',
            'y_label': 'count' if chart == 'histogram' else 'value',
            'y': {'kind': 'inline', 'values': list(values if values is not None else VALUES)},
            'qa_profile': {'caption': 'ENGINEERING_ONLY software fixture.'}}


def resolved(chart, values=None):
    spec = parse_spec(payload(chart, values))
    value, info = resolve_spec(spec)
    return spec, value, info


def axes_drawn(rendered):
    """Exact drawn geometry plus the readability state that follows it."""
    drawn = []
    for ax in rendered.axes:
        drawn.append({
            'patches': [[float(p.get_x()).hex(), float(p.get_y()).hex(),
                         float(p.get_width()).hex(), float(p.get_height()).hex()]
                        for p in ax.patches],
            'lines': [[[float(v).hex() for v in line.get_xdata()],
                       [float(v).hex() for v in line.get_ydata()]] for line in ax.lines],
            'xlim': [float(v).hex() for v in ax.get_xlim()],
            'ylim': [float(v).hex() for v in ax.get_ylim()],
            'xticks': [float(v).hex() for v in ax.get_xticks()],
            'xticklabels': [text.get_text() for text in ax.get_xticklabels()],
        })
    return drawn


def drawn(rendered):
    value = axes_drawn(rendered)
    assert len(value) == 1
    return value[0]


@pytest.mark.parametrize('chart', ['histogram', 'boxplot'])
def test_frozen_geometry_is_bit_identical_to_the_legacy_render(chart):
    spec, value, info = resolved(chart)
    legacy = build_resolved_figure(replace(value, scientific_geometry={}))
    frozen = build_resolved_figure(value)
    # Same patches/lines, axis limits and ticks: readability cannot drift.
    assert drawn(frozen) == drawn(legacy)


@pytest.mark.parametrize('chart', ['histogram', 'boxplot'])
@pytest.mark.parametrize('values', [
    VALUES,
    [-5, -1, 0, 0.5, 9],
    [3.0],
    [2, 2, 2],
    [0.123, 0.456, 0.789, 1.234, 5.678],
    list(np.linspace(0, 1000, 101)),
])
def test_exact_geometry_holds_across_data_shapes(chart, values):
    spec, value, info = resolved(chart, values)
    assert drawn(build_resolved_figure(value)) == drawn(
        build_resolved_figure(replace(value, scientific_geometry={})))


@pytest.mark.parametrize('chart', ['histogram', 'boxplot'])
def test_frozen_replay_never_recomputes_statistics(monkeypatch, chart):
    spec, value, info = resolved(chart)
    source = prepare_source(spec, value, info)
    restored, _ = load_snapshot(json.loads(json.dumps(source)))
    expected = drawn(build_resolved_figure(value))

    from matplotlib.axes import Axes
    import matplotlib.cbook
    fail = lambda *a, **k: pytest.fail('frozen replay recomputed distribution statistics')
    monkeypatch.setattr(Axes, 'hist', fail)
    monkeypatch.setattr(Axes, 'boxplot', fail)
    monkeypatch.setattr(matplotlib.cbook, 'boxplot_stats', fail)
    monkeypatch.setattr(np, 'histogram', fail)
    monkeypatch.setattr(np, 'percentile', fail)
    assert drawn(build_resolved_figure(restored)) == expected


@pytest.mark.parametrize('chart', ['histogram', 'boxplot'])
def test_snapshot_roundtrip_preserves_exact_geometry_and_digest(chart):
    spec, value, info = resolved(chart)
    source = prepare_source(spec, value, info)
    assert unsupported_reason(value) is None
    restored, restored_info = load_snapshot(json.loads(json.dumps(source)))
    assert restored == value and restored_info == info
    assert digest(restored.as_dict()) == source['resolved_snapshot']['resolved_sha256']
    assert drawn(build_resolved_figure(restored)) == drawn(build_resolved_figure(value))
    confirm_render(source, {'resolved_data_sha256': digest(value.as_dict())})
    with pytest.raises(SnapshotError, match='resolved_render_mismatch'):
        confirm_render(source, {'resolved_data_sha256': '0' * 64})


@pytest.mark.parametrize('chart', ['histogram', 'boxplot'])
def test_unreplayable_geometry_is_downgraded_explicitly(chart):
    spec, value, info = resolved(chart)
    damaged = deepcopy(value.scientific_geometry)
    # Version 1 is the only replayable contract; a future/unknown version must
    # not be approximated. Keep JSON-clean so the persisted case is testable.
    damaged['version'] = 999
    downgraded = prepare_source(spec, replace(value, scientific_geometry=damaged), info)
    assert 'resolved_snapshot' not in downgraded
    assert downgraded['freeze_downgrade'] == {
        'code': 'scientific_geometry_not_exactly_replayable',
        'chart': chart, 'panel_id': ''}
    assert load_snapshot(downgraded) is None
    # Legacy readability is preserved on the old path.
    assert drawn(build_resolved_figure(replace(value, scientific_geometry={})))
    # A persisted snapshot that claims the damaged geometry still fails closed.
    resolved_payload = value.as_dict()
    resolved_payload['scientific_geometry'] = damaged
    snapshot = {'schema': SCHEMA, 'spec_sha256': digest(spec.as_dict()),
                'resolved': resolved_payload,
                'resolved_sha256': digest(resolved_payload), 'provenance': {}}
    snapshot['sha256'] = digest(snapshot)
    with pytest.raises(SnapshotError):
        load_snapshot({'kind': 'spec', 'spec': spec.as_dict(),
                       'resolved_snapshot': snapshot})


@pytest.mark.parametrize('chart', ['histogram', 'boxplot'])
def test_missing_geometry_is_legacy_not_a_downgrade(chart):
    spec, value, info = resolved(chart)
    legacy = prepare_source(spec, replace(value, scientific_geometry={}), info)
    assert legacy == {'kind': 'spec', 'spec': spec.as_dict()}
    assert load_snapshot(legacy) is None
    assert unsupported_reason(replace(value, scientific_geometry={})) == {
        'code': 'no_scientific_geometry', 'chart': chart, 'panel_id': ''}


def test_mixed_panels_replay_each_distribution_exactly():
    profile = payload('histogram')['qa_profile']

    def panel(chart, panel_id):
        return {key: value for key, value in payload(chart).items()
                if key != 'qa_profile'} | {'panel_id': panel_id}

    spec = parse_spec({'qa_profile': profile,
                       'panels': [panel('histogram', 'A'), panel('boxplot', 'B')]})
    value, info = resolve_spec(spec)
    source = prepare_source(spec, value, info)
    restored, _ = load_snapshot(json.loads(json.dumps(source)))
    assert [item.panel_id for item in restored.panels] == ['A', 'B']
    assert [item.scientific_geometry['kind'] for item in restored.panels] == [
        'histogram', 'boxplot']
    assert digest(restored.as_dict()) == digest(value.as_dict())
    legacy = build_resolved_figure(replace(
        value, panels=tuple(replace(leaf, scientific_geometry={})
                            for leaf in value.panels)))
    frozen = build_resolved_figure(restored)
    assert axes_drawn(frozen) == axes_drawn(legacy)


def test_boxplot_group_geometry_is_exact_for_multiple_groups():
    values = [1, 2, 2, 4, 7, 13, 50, 2, 3, 9]
    raw = payload('boxplot', values)
    raw['group'] = {'kind': 'inline', 'values': ['a'] * 7 + ['b'] * 3}
    spec = parse_spec(raw)
    value, info = resolve_spec(spec)
    assert len(value.scientific_geometry['stats']) == 2
    frozen = build_resolved_figure(value)
    legacy = build_resolved_figure(replace(value, scientific_geometry={}))
    assert drawn(frozen) == drawn(legacy)


@pytest.mark.parametrize('chart', ['histogram', 'boxplot'])
def test_frozen_replay_preserves_the_object_inventory(chart):
    from citrus_figures.manifest import build_manifest
    spec, value, info = resolved(chart)
    frozen = build_manifest(build_resolved_figure(value), include_qa=False)
    legacy = build_manifest(build_resolved_figure(
        replace(value, scientific_geometry={})), include_qa=False)
    inventory = lambda manifest: [(item['gid'], item['role'], item['identity_scope'])
                                  for item in manifest['objects']]
    assert inventory(frozen) == inventory(legacy)

"""Versioned, JSON-only scientific results for declarative style replay.

Resolved paired charts already contain their plotted values, error magnitudes
and contributing row ordinals. Distribution charts carry a closed, versioned
geometry instead: histogram bin counts/edges and boxplot statistics. Replay
draws exactly that geometry and never re-bins or re-computes statistics.

A distribution geometry that cannot be replayed exactly is explicitly
downgraded at creation: source_json keeps the original spec, omits
resolved_snapshot and records freeze_downgrade with the reason. A legacy
figure therefore never claims a creation-time freeze it cannot reproduce,
while a persisted snapshot that fails the decoder always fails instead of
falling back to re-analysis.

Digests detect inconsistent persisted state; they do not replace source access
checks, file pins, or the project compare-and-swap.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

SCHEMA = 'citrus.declarative-resolved/1'
PAIRED_CHARTS = frozenset({'bar', 'line', 'scatter', 'errorbar'})
DISTRIBUTION_CHARTS = frozenset({'histogram', 'boxplot'})
GEOMETRY_VERSION = 1
MAX_BYTES = 16 * 1024 * 1024


class SnapshotError(ValueError):
    pass


def digest(value):
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(blob) > MAX_BYTES:
        raise SnapshotError('resolved_snapshot_too_large')
    return hashlib.sha256(blob).hexdigest()


def exact_distribution_replay(resolved):
    """Whether one distribution leaf can be drawn exactly as frozen.

    Empty geometry is the ordinary legacy path, not a freeze. Present geometry
    is replayable only when the closed validator accepts this exact versioned
    kind; anything else is downgraded instead of approximated. Validation never
    recomputes bins or statistics.
    """
    if not resolved.scientific_geometry:
        return False
    from citrus_figures.distribution_geometry import validate_distribution
    from citrus_figures.spec import SpecError
    try:
        validate_distribution(resolved)
    except SpecError:
        return False
    geometry = resolved.scientific_geometry
    return (isinstance(geometry, dict) and geometry.get('version') == GEOMETRY_VERSION
            and geometry.get('kind') == resolved.chart)


def unsupported_reason(resolved):
    """First leaf that prevents an exact creation-time freeze, else None.

    ``no_scientific_geometry`` is the compatible legacy path. Any other
    distribution code means geometry was present but cannot be replayed
    exactly; creation must stay on the legacy path and record the downgrade.
    """
    leaves = resolved.panels or (resolved,)
    for leaf in leaves:
        if leaf.panels:
            return {'code': 'nested_panels', 'panel_id': leaf.panel_id}
        if leaf.chart in PAIRED_CHARTS:
            if leaf.scientific_geometry:
                return {'code': 'unexpected_scientific_geometry',
                        'chart': leaf.chart, 'panel_id': leaf.panel_id}
            continue
        if leaf.chart not in DISTRIBUTION_CHARTS:
            return {'code': 'unsupported_chart',
                    'chart': leaf.chart, 'panel_id': leaf.panel_id}
        if not leaf.scientific_geometry:
            return {'code': 'no_scientific_geometry',
                    'chart': leaf.chart, 'panel_id': leaf.panel_id}
        if not exact_distribution_replay(leaf):
            return {'code': 'scientific_geometry_not_exactly_replayable',
                    'chart': leaf.chart, 'panel_id': leaf.panel_id}
    return None


def _supported(resolved):
    return unsupported_reason(resolved) is None


def prepare_source(spec, resolved, provenance):
    """Record the creation result, never silently freeze a legacy project."""
    source = {'kind': 'spec', 'spec': spec.as_dict()}
    reason = unsupported_reason(resolved)
    if reason is not None:
        # A present-but-unreplayable geometry is downgraded explicitly; it is
        # never persisted as a frozen capability. Missing geometry is the
        # ordinary legacy path and needs no downgrade marker.
        if reason['code'] in {'scientific_geometry_not_exactly_replayable',
                              'unexpected_scientific_geometry'}:
            source['freeze_downgrade'] = reason
        return source
    payload = resolved.as_dict()
    snapshot = {'schema': SCHEMA, 'spec_sha256': digest(source['spec']),
                'resolved': payload, 'resolved_sha256': digest(payload),
                'provenance': deepcopy(provenance)}
    snapshot['sha256'] = digest(snapshot)
    source['resolved_snapshot'] = snapshot
    # Exercise the same bounded closed decoder used on every subsequent replay.
    load_snapshot(source)
    return source


def load_snapshot(source):
    """Return a closed resolved value, without reading tables or using Artists.

    None means legacy/unsupported source. A present but invalid snapshot always
    fails, including null values; it must never fall back to re-analysis.
    """
    if 'resolved_snapshot' not in source:
        return None
    if 'freeze_downgrade' in source:
        # An explicit downgrade and a frozen snapshot contradict each other.
        raise SnapshotError('invalid_resolved_snapshot')
    try:
        snapshot = source['resolved_snapshot']
        fields = {'schema', 'spec_sha256', 'resolved', 'resolved_sha256', 'provenance', 'sha256'}
        if not isinstance(snapshot, dict) or set(snapshot) != fields or snapshot['schema'] != SCHEMA:
            raise SnapshotError('invalid_resolved_snapshot')
        if (snapshot['spec_sha256'] != digest(source['spec'])
                or snapshot['resolved_sha256'] != digest(snapshot['resolved'])
                or snapshot['sha256'] != digest({k:v for k,v in snapshot.items() if k != 'sha256'})
                or not isinstance(snapshot['provenance'], dict)):
            raise SnapshotError('resolved_snapshot_changed')
        # This function imports only dataclasses and the pure specification
        # vocabulary. No worker is started and no Matplotlib object is built.
        from src.figures.trusted_worker import decode_resolved
        resolved = decode_resolved(deepcopy(snapshot['resolved']))
        if not _supported(resolved) or digest(resolved.as_dict()) != snapshot['resolved_sha256']:
            raise SnapshotError('unsupported_resolved_snapshot')
        return resolved, deepcopy(snapshot['provenance'])
    except SnapshotError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
        raise SnapshotError('invalid_resolved_snapshot') from exc


def confirm_render(source, manifest):
    """Require the fresh child to acknowledge the exact recorded numeric input."""
    if load_snapshot(source) is not None:
        if manifest.get('resolved_data_sha256') != source['resolved_snapshot']['resolved_sha256']:
            raise SnapshotError('resolved_render_mismatch')

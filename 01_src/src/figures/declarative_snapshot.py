"""Versioned, JSON-only scientific results for declarative style replay.

Resolved paired charts already contain their plotted values, error magnitudes
and contributing row ordinals. Raw distribution observations are deliberately
excluded until their bins/quantiles have a separate frozen geometry contract.
Digests detect inconsistent persisted state; they do not replace source access
checks, file pins, or the project compare-and-swap.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

SCHEMA = 'citrus.declarative-resolved/1'
PAIRED_CHARTS = frozenset({'bar', 'line', 'scatter', 'errorbar'})
MAX_BYTES = 16 * 1024 * 1024


class SnapshotError(ValueError):
    pass


def digest(value):
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(blob) > MAX_BYTES:
        raise SnapshotError('resolved_snapshot_too_large')
    return hashlib.sha256(blob).hexdigest()


def _supported(resolved):
    leaves = resolved.panels or (resolved,)
    return all(not leaf.panels and (leaf.chart in PAIRED_CHARTS or
               (leaf.chart in {'histogram', 'boxplot'} and bool(leaf.scientific_geometry)))
               for leaf in leaves)


def prepare_source(spec, resolved, provenance):
    """Record the creation result, never silently freeze a legacy project."""
    source = {'kind': 'spec', 'spec': spec.as_dict()}
    if not _supported(resolved):
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

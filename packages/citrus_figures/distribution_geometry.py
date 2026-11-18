"""Closed statistical payloads computed once, before distribution rendering."""
from __future__ import annotations

import math

from .spec import SpecError

BOX_NUMBERS = frozenset({'mean', 'iqr', 'cilo', 'cihi', 'whislo', 'whishi', 'q1', 'med', 'q3'})
MAX_VALUES = 200_000


def freeze_distribution(chart, y_values, series):
    """Use the same default NumPy/Matplotlib statistics as the original plots."""
    if chart == 'histogram':
        import numpy as np
        counts, edges = np.histogram(y_values, bins=20)
        return {'version': 1, 'kind': chart,
                'counts': counts.astype(float).tolist(), 'edges': edges.tolist()}
    if chart == 'boxplot':
        from matplotlib.cbook import boxplot_stats
        rows = boxplot_stats([list(item.values) for item in series],
                             labels=[item.name for item in series], whis=1.5)
        return {'version': 1, 'kind': chart, 'stats': [
            {**{key: float(row[key]) for key in BOX_NUMBERS},
             'label': row['label'], 'fliers': row['fliers'].tolist()} for row in rows]}
    return {}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_distribution(resolved):
    """Validate shape and bounds, without recomputing any scientific result.

    Empty geometry retains legacy rendering; it is never a frozen capability.
    The digest maintained by the caller guards the creation-time exact values.
    """
    geometry = resolved.scientific_geometry
    if geometry == {}:
        return
    try:
        if (not isinstance(geometry, dict) or type(geometry.get('version')) is not int
                or geometry['version'] != 1 or geometry.get('kind') != resolved.chart):
            raise ValueError()
        if resolved.chart == 'histogram':
            if set(geometry) != {'version', 'kind', 'counts', 'edges'}:
                raise ValueError()
            counts, edges = geometry['counts'], geometry['edges']
            if (not isinstance(counts, list) or not isinstance(edges, list)
                    or not 1 <= len(counts) <= MAX_VALUES or len(edges) != len(counts) + 1
                    or any(not _number(v) or v < 0 or int(v) != v for v in counts)
                    or any(not _number(v) for v in edges)
                    or any(a >= b for a,b in zip(edges, edges[1:]))
                    or sum(counts) != len(resolved.y_values)):
                raise ValueError()
        elif resolved.chart == 'boxplot':
            if set(geometry) != {'version', 'kind', 'stats'}:
                raise ValueError()
            rows = geometry['stats']
            if not isinstance(rows, list) or not rows or len(rows) != len(resolved.series):
                raise ValueError()
            total = 0
            for row, series in zip(rows, resolved.series):
                if (not isinstance(row, dict) or set(row) != BOX_NUMBERS | {'label', 'fliers'}
                        or row['label'] != series.name or not isinstance(row['label'], str)
                        or any(not _number(row[key]) for key in BOX_NUMBERS)
                        or not row['whislo'] <= row['q1'] <= row['med'] <= row['q3'] <= row['whishi']
                        or row['cilo'] > row['cihi'] or row['iqr'] < 0
                        or not isinstance(row['fliers'], list)
                        or any(not _number(v) for v in row['fliers'])):
                    raise ValueError()
                total += len(row['fliers'])
            if total > MAX_VALUES:
                raise ValueError()
        else:
            raise ValueError()
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise SpecError('invalid_scientific_geometry', '冻结的统计几何合同无效。') from exc

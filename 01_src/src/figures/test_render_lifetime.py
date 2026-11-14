"""Declarative figures do not accumulate in pyplot or leak global style."""
import matplotlib
import matplotlib.pyplot as plt

from src.figures.core import build_resolved_figure
from src.figures.exporter import export_all
from src.figures.resolve import resolve_spec
from src.figures.spec import parse_spec


def test_build_and_export_restore_style_without_registering_pyplot_figures():
    before = set(plt.get_fignums())
    with matplotlib.rc_context({'font.size': 17, 'svg.fonttype': 'path'}):
        for _ in range(3):
            resolved, _ = resolve_spec(parse_spec({
                'chart': 'line', 'title': 'Research',
                'x': {'kind': 'inline', 'column': 'x', 'values': [1, 10, 100]},
                'y': {'kind': 'inline', 'column': 'y', 'values': [2, 4, 8]},
                'style': {'font_size': 7},
            }))
            rendered = build_resolved_figure(resolved)
            assert set(plt.get_fignums()) == before
            assert matplotlib.rcParams['font.size'] == 17
            svg = export_all(rendered, formats=('svg',))['svg']
            assert b'<text' in svg
            assert matplotlib.rcParams['svg.fonttype'] == 'path'
            assert matplotlib.rcParams['font.size'] == 17
    assert set(plt.get_fignums()) == before

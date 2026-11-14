"""Edits use SVG points and must survive redraw without changing data."""
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from src.figures.core import RenderedFigure
from src.figures.manifest import assign_locators, build_manifest
from src.figures.overrides import OverrideError, apply_overrides, validate_overrides


def drawing(dpi):
    fig = Figure(figsize=(6, 4), dpi=dpi)
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    ax.plot([1, 10, 100], [2, 4, 8], label='sample')
    ax.set_title('Title')
    ax.set_xlabel('X')
    ax.set_ylabel('Y')
    ax.legend(loc='upper right')
    ax.annotate('Note', xy=(10, 4), xytext=(12, 5))
    rendered = assign_locators(RenderedFigure(figure=fig, axes=[ax]))
    fig.canvas.draw()
    return rendered


@pytest.mark.parametrize('dpi', [72, 144])
@pytest.mark.parametrize('role', ['title', 'xlabel', 'ylabel', 'legend', 'annot', 'axes'])
def test_drag_moves_by_svg_points_after_redraw(role, dpi):
    rendered = drawing(dpi)
    manifest = build_manifest(rendered, source='spec')
    entry = next(item for item in manifest['objects'] if item['role'] == role)
    before = entry['bbox']
    changes = validate_overrides([{'gid': entry['gid'], 'prop': 'offset_px', 'value': [12, 7]}], manifest)
    assert apply_overrides(rendered, manifest, changes) == [entry['gid']]
    rendered.figure.canvas.draw()
    after = next(item['bbox'] for item in build_manifest(rendered)['objects']
                 if item['gid'] == entry['gid'])
    assert after['x0'] - before['x0'] == pytest.approx(12, abs=.1)
    assert after['y0'] - before['y0'] == pytest.approx(7, abs=.1)
    assert list(rendered.axes[0].lines[0].get_xdata()) == [1, 10, 100]
    assert list(rendered.axes[0].lines[0].get_ydata()) == [2, 4, 8]


def test_old_manifest_cannot_reenable_moving_data_series():
    manifest = {'objects': [{'gid': 'axes_0.series_0', 'role': 'series', 'editable': ['offset_px']}]}
    with pytest.raises(OverrideError, match='不允许'):
        validate_overrides([{'gid': 'axes_0.series_0', 'prop': 'offset_px', 'value': [1, 1]}], manifest)

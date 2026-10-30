from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault('MPLCONFIGDIR', str(ROOT / '.mpl-cache'))
import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
from PIL import Image
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from matplotlib import font_manager

sys.path.insert(0, r'C:\Users\Administrator\.codex\skills\nature-figure\scripts')
from audit_panel_alignment import require_matplotlib_panel_alignment

DATA = ROOT / '数据'
OUT = ROOT / '绘图产物'
SNAP = json.loads((DATA / 'Fig4_冻结绘图输入.json').read_text(encoding='utf-8'))
STYLE = json.loads((DATA / '绘图样式与标签.json').read_text(encoding='utf-8'))
C = STYLE['colors']
L = STYLE['labels']


def setup():
    font_manager.findfont(STYLE['font'], fallback_to_default=False)
    mpl.rcParams.update({
        'font.family': 'Arial', 'font.sans-serif': ['Arial'],
        'font.size': 8.5, 'axes.labelsize': 9, 'xtick.labelsize': 8,
        'ytick.labelsize': 8.5, 'svg.fonttype': 'none',
        'pdf.fonttype': 42, 'ps.fonttype': 42,
        'text.color': C['text'], 'axes.labelcolor': C['text'],
        'xtick.color': C['secondary'], 'ytick.color': C['text'],
        'figure.facecolor': 'white', 'axes.facecolor': 'white',
        'axes.linewidth': 0.6, 'savefig.facecolor': 'white',
    })


def text(ax, x, y, s, gid, **kw):
    t = ax.text(x, y, s, transform=ax.transAxes, va='center',
                fontsize=8.5, gid=gid, **kw)
    return t


def export(fig, name, **alignment):
    OUT.mkdir(parents=True, exist_ok=True)
    prefix = OUT / name
    fig.canvas.draw()
    require_matplotlib_panel_alignment(
        fig, json_out=str(prefix) + '.alignment.json',
        tolerance_pt=1.5, gutter_tolerance_pt=1.5, strict=True,
        require_panel_labels=False, **alignment)
    fig.savefig(str(prefix) + '.svg', bbox_inches=None, pad_inches=0)
    fig.savefig(str(prefix) + '.pdf', bbox_inches=None, pad_inches=0)
    fig.savefig(str(prefix) + '.eps', bbox_inches=None, pad_inches=0)
    fig.savefig(str(prefix) + '.png', dpi=600, bbox_inches=None, pad_inches=0)
    fig.savefig(str(prefix) + '.tiff', dpi=600, bbox_inches=None, pad_inches=0,
                pil_kwargs={'compression': 'tiff_lzw'})
    with Image.open(str(prefix) + '.tiff') as image:
        image.convert('RGB').save(str(prefix) + '.tiff', dpi=(600, 600), compression='tiff_lzw')
    plt.close(fig)


def panel_a():
    arms = SNAP['arms']
    assert [a['arm'] for a in arms] == ['A', 'B', 'C', "C'", 'D']
    fig = plt.figure(figsize=(174 / 25.4, STYLE['height_a_mm'] / 25.4))
    ax = fig.add_axes([0.235, 0.225, 0.64, 0.675])
    ax.set_xlim(0, 40)
    ax.set_ylim(4.6, -0.6)
    ax.set_xticks([0, 10, 20, 30, 40])
    ax.set_yticks(range(len(arms)))
    ax.set_yticklabels([f"{a['arm'].replace(chr(39), '′')} · {a['label']}" for a in arms])
    ax.set_xlabel(L['a_axis'], labelpad=7)
    ax.tick_params(axis='y', length=0, pad=8)
    ax.tick_params(axis='x', length=3, width=0.6, pad=4)
    for side in ['top', 'right', 'left']:
        ax.spines[side].set_visible(False)
    ax.spines['bottom'].set_color(C['control'])
    ax.axvline(0, color=C['control'], lw=0.5, zorder=0, gid='zero-baseline')
    for i, arm in enumerate(arms):
        col = C['control'] if arm['arm'] == 'A' else C['web']
        bar = ax.barh(i, arm['percent'], height=0.57, color=col,
                      edgecolor='none', zorder=2)[0]
        bar.set_gid('rate-bar-' + arm['arm'].replace("'", 'prime'))
        ax.text(arm['percent'] + 0.65, i, f"{arm['percent']:.1f}%", ha='left',
                va='center', fontsize=8.5, gid=f'percent-{i}')
        ax.text(1.07, i, f"{arm['events']}/{arm['verdicts']}",
                transform=ax.get_yaxis_transform(), ha='center', va='center',
                fontsize=8.5, gid=f'events-verdicts-{i}')
    ax.text(1.07, 1.065, L['a_counts'], transform=ax.transAxes,
            ha='center', va='center', fontsize=8, gid='count-column-label')
    for i, t in enumerate(ax.get_yticklabels()):
        t.set_gid(f'condition-label-{i}')
    # Separate the control from the four web-source mixing conditions.
    y_fig = fig.transFigure.inverted().transform(ax.transData.transform((0, .5)))[1]
    fig.add_artist(Line2D([.03, .98], [y_fig, y_fig], transform=fig.transFigure,
                          color=C['border'], lw=.6, dashes=(3, 3),
                          gid='control-web-divider'))
    export(fig, '图4a_旧架构联网混入风险')


def band(ax, x, y, width, height, fraction, color, gid):
    assert 0 <= fraction <= 1
    ax.add_line(Line2D([x, x+width], [y, y], transform=ax.transAxes,
                       color=C['border'], lw=.6, gid=gid+'-denominator'))
    if fraction > 0:
        ax.add_patch(Rectangle((x, y), width*fraction, height, transform=ax.transAxes,
                               facecolor=color, edgecolor='none', gid=gid+'-numerator'))


def panel_b():
    b, m = SNAP['boundary'], SNAP['integrity']
    fig = plt.figure(figsize=(174 / 25.4, STYLE['height_b_mm'] / 25.4))
    grid = fig.add_gridspec(1, 2, left=.04, right=.97, bottom=.07,
                           top=.94, wspace=.14)
    left, right = [fig.add_subplot(grid[0, i]) for i in range(2)]
    for ax in (left, right):
        ax.set_axis_off()
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
    text(left, 0, .97, L['web_unit'], 'occurrence-unit')
    text(right, 0, .97, L['marker_unit'], 'marker-unit')
    # The top band partitions web-citation occurrences by output section.
    total = b['local_occurrences'] + b['web_occurrences']
    y, h = .65, .105
    band(left, 0, y, 1, h, b['web_occurrences']/total, C['web'], 'web-allocation')
    text(left, 0, .83, f"{L['local_section']}: {b['local_occurrences']}", 'local-occurrence-label')
    text(left, 1, .83, f"{L['web_section']}: {b['web_occurrences']}", 'web-occurrence-label', ha='right')
    # A zero-width local allocation remains represented by its exact live-text count.
    text(left, 0, .395, L['violations'], 'answer-violation-label')
    text(left, 1, .395, f"{b['local_violation_answers']}/{b['all_answers']} answers",
         'answer-violation-value', ha='right')
    text(left, 0, .135, L['pool_c'], 'pool-c-label')
    text(left, 1, .135, f"{b['pool_c_with_web']}/{b['pool_c_answers']} answers",
         'pool-c-value', ha='right')
    # Each marker band uses the same total of in-text local citation occurrences.
    for key, label, y_label, y_bar, col in [
        ('resolvable', L['resolvable'], .83, .65, C['resolvable']),
        ('out_of_pool', L['out_of_pool'], .395, .20, C['control'])]:
        text(right, 0, y_label, label, 'marker-label-'+key)
        text(right, 1, y_label, f"{m[key]:,}/{m['markers']:,}", 'marker-value-'+key, ha='right')
        band(right, 0, y_bar, 1, h, m[key]/m['markers'], col, 'marker-'+key)
    export(fig, '图4b_新架构结构核验', panel_ids=['occurrences-and-answers', 'citation-markers'])


if __name__ == '__main__':
    setup()
    panel_a()
    panel_b()
    print('Exported two independent panels in SVG/PDF/EPS/PNG/TIFF.')

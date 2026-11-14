"""Deterministic synthetic UI fixtures; not research measurements or conclusions.

Three groups, eight time points and three replicates expose error bars, labels,
legends, curves, individual scatter points and grouped bars in the real editor.
No model, external data or production storage is used.
"""
import json
import math

COLORS = ('#2a78d6', '#eb6834', '#1baf7a')
GROUPS = ('Series A', 'Series B', 'Series C')
TIMES = (0, 2, 4, 7, 10, 14, 18, 21)


def synthetic_rows():
    rows = []
    for group_index, group in enumerate(GROUPS):
        for time in TIMES:
            centre = (12, 18, 24)[group_index] + (65, 47, 30)[group_index] * (1 - math.exp(-time / (6, 9, 12)[group_index]))
            for replicate, deviation in enumerate((-1.7, 0.1, 1.6), 1):
                value = round(centre + deviation * (1 + time / 16) + group_index * .25, 3)
                rows.append({'group': group, 'day': time, 'replicate': replicate, 'response': value})
    return rows


def fixture_specs():
    rows = synthetic_rows()
    common = {
        'x_label': 'Time (days)', 'y_label': 'Synthetic response (a.u.)',
        'x': {'kind': 'inline', 'values': [row['day'] for row in rows]},
        'y': {'kind': 'inline', 'values': [row['response'] for row in rows]},
        'group': {'kind': 'inline', 'values': [row['group'] for row in rows]},
        'legend': True,
        'style': {'palette': 'muted', 'font_size': 9, 'figure_size_in': [7.2, 4.7], 'dpi': 160},
    }
    # End labels provide identity without relying on hue. Every value is synthetic.
    end_labels = [{'text': group, 'x': 19.5, 'y': next(
        row['response'] for row in rows if row['group'] == group and row['day'] == 21 and row['replicate'] == 2
    ) + 4} for group in GROUPS]
    return [
        {**common, 'chart': 'line', 'title': '合成验收 · 三组时间趋势（均值 ± SD）',
         'error': 'sd', 'annotations': [{'text': 'Synthetic fixture · n = 3 per time point', 'x': 1, 'y': 77}, *end_labels]},
        {**common, 'chart': 'scatter', 'title': '合成验收 · 72 个原始数据点',
         'error': 'none', 'annotations': [{'text': 'Synthetic fixture · each dot is one simulated observation', 'x': 1, 'y': 77}, *end_labels]},
        {**common, 'chart': 'bar', 'title': '合成验收 · 分组比较（均值 ± SD）',
         'error': 'sd', 'annotations': [{'text': 'Synthetic fixture · not research results', 'x': 0.2, 'y': 77}]},
    ]


def publish_fixtures(owner, session_id):
    from src.research.figure_tools import create_research_figure
    from src.figures.projects import save_overrides
    figures = []
    # Reverse order means the trend is the newest item in the workspace list.
    for spec in reversed(fixture_specs()):
        _, result = create_research_figure.func(spec_json=json.dumps(spec, ensure_ascii=False))
        figure_id = result.get('figure_id')
        if not figure_id:
            raise RuntimeError(f'Fixture publication failed: {result.get("_tool_outcome")}')
        from src.figures.projects import get_for_owner
        project = get_for_owner(figure_id, owner)
        save_overrides(figure_id, owner, [
            {'gid': f'axes_0.series_{index}', 'prop': 'color', 'value': color}
            for index, color in enumerate(COLORS)
        ], session_id=session_id, expected_revision=project['render_version'])
        figures.append(figure_id)
    return figures

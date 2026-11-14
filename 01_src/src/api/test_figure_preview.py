"""Disposable previews use the save renderer without creating saved state."""
import json

import pytest

from src.research.test_figure_tools import account, _inline_spec


def _project():
    from src.research.figure_tools import create_research_figure
    return create_research_figure.func(spec_json=_inline_spec())[1]['figure_id']


def _edit(value=15):
    return [{'gid': 'axes_0.title', 'prop': 'fontsize', 'value': value}]


def test_preview_is_disposable_and_matches_saved_svg(account, monkeypatch):
    from src.figures import projects, store
    from src.session import files
    figure_id = _project()
    before = store.get_project(figure_id, 101)
    before_files = files.list_account_files(101, touch=False)
    before_revisions = store.list_revisions(figure_id, 101)
    with store._connect() as conn:
        pins = [dict(row) for row in conn.execute('SELECT * FROM figure_project_pins')]
    real_publish = projects.publish_rendered
    monkeypatch.setattr(projects, 'publish_rendered', lambda *a, **k: pytest.fail('preview published'))
    preview = projects.preview_overrides(figure_id, 101, _edit(), expected_revision=1)
    assert preview['render_version'] == 1
    assert preview['overrides'] == _edit()
    assert '<svg' in preview['svg']
    assert store.get_project(figure_id, 101) == before
    assert store.list_revisions(figure_id, 101) == before_revisions
    assert files.list_account_files(101, touch=False) == before_files
    with store._connect() as conn:
        assert [dict(row) for row in conn.execute('SELECT * FROM figure_project_pins')] == pins
    monkeypatch.setattr(projects, 'publish_rendered', real_publish)
    saved = projects.save_overrides(figure_id, 101, _edit(), expected_revision=1)
    entry = files.get_account_file(saved['exports']['svg']['file_id'], 101)
    assert entry['path'].read_bytes() == preview['svg'].encode('utf-8')


def test_pending_overrides_merge_with_saved_without_accumulating_drafts(account):
    from src.figures import projects
    figure_id = _project()
    projects.save_overrides(figure_id, 101, _edit(12), expected_revision=1)
    color = [{'gid': 'axes_0.title', 'prop': 'color', 'value': '#b64342'}]
    preview = projects.preview_overrides(figure_id, 101, color, expected_revision=2)
    assert len(preview['overrides']) == 2
    next_preview = projects.preview_overrides(figure_id, 101, _edit(16), expected_revision=2)
    assert next_preview['overrides'] == _edit(16)


def test_preview_rejects_owner_stale_and_invalid_batch(account):
    from src.figures import projects
    figure_id = _project()
    for owner, edits, revision, code in [
        (202, [], 1, 'not_found'), (101, [], 0, 'revision_conflict'),
        (101, [{'gid': 'axes_0.title', 'prop': 'unsafe_property', 'value': 'changed'}], 1, 'unknown_prop'),
    ]:
        with pytest.raises(projects.ProjectError) as exc:
            projects.preview_overrides(figure_id, owner, edits, expected_revision=revision)
        assert exc.value.code == code


def test_preview_rechecks_revision_after_render(account, monkeypatch):
    from src.figures import projects, store, trusted_render
    figure_id = _project()
    original = trusted_render.render_resolved
    def race(*args, **kwargs):
        blobs = original(*args, **kwargs)
        store.update_project(figure_id, 101, expected_revision=1, render_version=2)
        return blobs
    monkeypatch.setattr(trusted_render, 'render_resolved', race)
    with pytest.raises(projects.ProjectError) as exc:
        projects.preview_overrides(figure_id, 101, _edit(), expected_revision=1)
    assert exc.value.code == 'revision_conflict'


def test_preview_checks_source_hash_before_and_after_render(account, monkeypatch):
    from src.figures import projects, trusted_render
    from src.session import files
    item = files.register_file(session_id=account['session_id'], owner_user_id=101,
                               filename='source.csv', content=b'x,y\nA,1\nB,2\n')
    imported = projects.import_from_library(101, account['session_id'], item['file_id'])
    original = trusted_render.render_resolved
    path = files.get_account_file(item['file_id'], 101)['path']
    def mutate(*args, **kwargs):
        blobs = original(*args, **kwargs)
        path.write_bytes(b'x,y\nA,9\nB,8\n')
        return blobs
    monkeypatch.setattr(trusted_render, 'render_resolved', mutate)
    with pytest.raises(projects.ProjectError) as exc:
        projects.preview_overrides(imported['figure_id'], 101, [], expected_revision=1)
    assert exc.value.code == 'source_changed'
    monkeypatch.setattr(trusted_render, 'render_resolved', lambda *a, **k: pytest.fail('changed source rendered'))
    with pytest.raises(projects.ProjectError) as exc:
        projects.preview_overrides(imported['figure_id'], 101, [], expected_revision=1)
    assert exc.value.code == 'source_changed'


def test_preview_output_budget_rejects_without_publishing(account, monkeypatch):
    from src.figures import projects, store
    figure_id = _project()
    monkeypatch.setattr(projects, 'MAX_PREVIEW_BYTES', 10)
    with pytest.raises(projects.ProjectError) as exc:
        projects.preview_overrides(figure_id, 101, [])
    assert exc.value.code == 'render_too_large'
    assert store.get_project(figure_id, 101)['render_version'] == 1


def test_api_preview_holds_account_lease_without_creating_session(account, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api import figure_router
    from src.figures import projects
    from src.core.account_leases import current_account_lease, active_count
    figure_id = _project()
    monkeypatch.setattr(figure_router, '_owner', lambda request: 101)
    monkeypatch.setattr(figure_router, '_resolve_session', lambda *a: pytest.fail('preview created session'))
    original = projects.preview_overrides
    def check(*args, **kwargs):
        assert current_account_lease().owner == 101
        assert active_count(101) >= 1
        return original(*args, **kwargs)
    monkeypatch.setattr(projects, 'preview_overrides', check)
    app = FastAPI()
    app.include_router(figure_router.router)
    with TestClient(app) as client:
        response = client.post(f'/api/v2/figures/projects/{figure_id}/preview',
                               json={'overrides': _edit(), 'expected_revision': 1})
        assert response.status_code == 200
        assert response.json()['render_version'] == 1
        assert 'no-store' in response.headers['cache-control']
    assert active_count(101) == 0


@pytest.mark.parametrize('old_version', [1, 2])
def test_legacy_manifest_upgrade_is_read_only(account, old_version):
    from src.figures import projects, store
    figure_id = _project()
    # Model a persisted version-1 snapshot without going through mutation API.
    with store._connect() as conn:
        row = conn.execute('SELECT manifest_json FROM figure_projects WHERE figure_id=?', (figure_id,)).fetchone()
        manifest = json.loads(row[0])
        manifest['version'] = old_version
        manifest['objects'] = [item for item in manifest['objects'] if item['role'] not in {'spine', 'ticklabel', 'point', 'bar'}]
        conn.execute('UPDATE figure_projects SET manifest_json=? WHERE figure_id=?', (json.dumps(manifest), figure_id))
    before = store.get_project(figure_id, 101)
    detail = projects.detail_for_owner(figure_id, 101, upgrade_manifest=True)
    assert detail['manifest']['version'] == 3
    title = next(item for item in detail['manifest']['objects'] if item['role'] == 'title')
    assert 'text' in title['editable']
    assert any(item['role'] == 'spine' for item in detail['manifest']['objects'])
    assert store.get_project(figure_id, 101) == before
    spine = next(item for item in detail['manifest']['objects'] if item['role'] == 'spine')
    preview = projects.preview_overrides(figure_id, 101, [{'gid': spine['gid'], 'prop': 'visible', 'value': False}])
    assert next(item for item in preview['manifest']['objects'] if item['gid'] == spine['gid'])['visible'] is False


def test_scatter_styles_labels_and_literal_title_survive_successive_project_saves(account, monkeypatch):
    import numpy as np
    from src.research.figure_tools import create_research_figure
    from src.figures import trusted_render, projects, store
    from src.session import files
    spec = {'chart': 'scatter', 'title': 'original', 'error': 'none',
            'x': {'kind': 'inline', 'values': [1, 4, 9, 2, 5, 10]},
            'y': {'kind': 'inline', 'values': [2, 6, 4, 3, 7, 5]},
            'group': {'kind': 'inline', 'values': ['A', 'A', 'A', 'B', 'B', 'B']}}
    result = create_research_figure.func(spec_json=json.dumps(spec), formats_json=['svg'])[1]
    figure_id = result['figure_id']
    observed = []
    real_render = trusted_render.render_resolved
    def observe(*args, **kwargs):
        result = real_render(*args, **kwargs)
        # These are JSON measurements of the actual child Artists, not a
        # second parent-process proxy render.
        item = result.observations['series'][0]
        observed.append((item['offsets'], item['sizes'], np.asarray(item['facecolors']),
                         item['legend']['label'], item['legend']['sizes']))
        return result
    monkeypatch.setattr(trusted_render, 'render_resolved', observe)
    series = 'axes_0.series_0'
    point = series + '.point_1'
    edits = [{'gid': series, 'prop': prop, 'value': value} for prop, value in {
        'label': 'Synthetic cohort A', 'marker': 'v', 'markersize': 8,
        'markeredgecolor': '#202020', 'markeredgewidth': 1.5, 'alpha': .75}.items()]
    edits += [{'gid': point, 'prop': 'marker', 'value': 'D'},
              {'gid': point, 'prop': 'color', 'value': '#6250d6'},
              {'gid': 'axes_0.title', 'prop': 'text', 'value': '<b>Literal</b> $x$'}]
    preview = projects.preview_overrides(figure_id, 101, edits, expected_revision=1)
    saved = projects.save_overrides(figure_id, 101, edits, expected_revision=1)
    svg = files.get_account_file(saved['exports']['svg']['file_id'], 101)['path'].read_text(encoding='utf-8')
    assert svg == preview['svg']
    assert 'Synthetic cohort A' in svg and '&lt;b&gt;Literal&lt;/b&gt; $x$' in svg
    projects.save_overrides(figure_id, 101, [{'gid': point, 'prop': 'visible', 'value': False}], expected_revision=2)
    projects.save_overrides(figure_id, 101, [{'gid': point, 'prop': 'visible', 'value': True}], expected_revision=3)
    detail = projects.detail_for_owner(figure_id, 101)
    items = {item['gid']: item for item in detail['manifest']['objects']}
    assert items[series]['label'] == 'Synthetic cohort A'
    assert items[point]['marker'] == 'D' and items[point]['color'] == '#6250d6'
    assert items[point]['visible'] is True and items[point]['alpha'] == .75
    assert store.get_project(figure_id, 101)['render_version'] == 4
    for offsets, sizes, colours, label, legend_sizes in observed:
        np.testing.assert_array_equal(offsets, [[1, 2], [4, 6], [9, 4]])
        np.testing.assert_array_equal(sizes, [64, 64, 64])
        assert label == 'Synthetic cohort A'
        np.testing.assert_array_equal(legend_sizes, [64])
    assert observed[-2][2][1, 3] == 0
    assert observed[-1][2][1, 3] == .75


def test_failed_retired_pin_cleanup_keeps_new_snapshot_protected(account, monkeypatch):
    from src.figures import projects, store
    from src.session import files
    figure_id = _project()
    original = store._release_pin
    monkeypatch.setattr(store, '_release_pin', lambda *a: (_ for _ in ()).throw(OSError('cleanup failure')))
    saved = projects.save_overrides(figure_id, 101, _edit(), expected_revision=1)
    assert saved['render_version'] == 2
    assert files.is_file_referenced(saved['exports']['svg']['file_id'], 101)
    with store._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM figure_project_pins').fetchone()[0] == 2
    monkeypatch.setattr(store, '_release_pin', original)
    projects.save_overrides(figure_id, 101, _edit(16), expected_revision=2)
    with store._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM figure_project_pins').fetchone()[0] == 1


def test_untracked_source_reference_is_rejected_before_reading(account, monkeypatch):
    from src.figures import projects, store
    figure_id = _project()
    with store._connect() as conn:
        source = json.loads(conn.execute('SELECT source_json FROM figure_projects WHERE figure_id=?', (figure_id,)).fetchone()[0])
        source['spec']['x'] = {'kind': 'account_file', 'file_id': 'untracked', 'column': 'x'}
        conn.execute('UPDATE figure_projects SET source_json=? WHERE figure_id=?', (json.dumps(source), figure_id))
    monkeypatch.setattr(projects, '_render_source', lambda *a, **k: pytest.fail('unverified source read'))
    with pytest.raises(projects.ProjectError) as exc:
        projects.preview_overrides(figure_id, 101, [])
    assert exc.value.code == 'source_unverified'


def test_retention_keeps_source_and_other_consumers_pins(account):
    from src.figures import projects, store
    from src.session import files
    item = files.register_file(session_id=account['session_id'], owner_user_id=101,
                               filename='source.csv', content=b'x,y\nA,1\nB,2\n')
    imported = projects.import_from_library(101, account['session_id'], item['file_id'])
    old_svg = imported['exports']['svg']['file_id']
    files.replace_resource_references(101, 'figure', 'other-consumer', [old_svg])
    for revision in (1, 2, 3):
        projects.save_overrides(imported['figure_id'], 101, _edit(10 + revision), expected_revision=revision)
    assert [r['render_version'] for r in store.list_revisions(imported['figure_id'], 101)] == [4]
    assert files.is_file_referenced(item['file_id'], 101)
    assert [d['resource_id'] for d in files.file_dependencies(old_svg, 101)] == ['other-consumer']
    with store._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM figure_revisions').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM figure_project_pins').fetchone()[0] == 1

"""Figure publication and editing failures must be visible and recoverable."""
import json

import pytest

from src.research.test_figure_tools import account, _inline_spec


def test_workspace_exports_resolve_through_artifact_registry(account):
    from src.core import tracing
    from src.research import tools
    from src.research.figure_tools import create_research_figure
    from src.figures import projects

    _, result = create_research_figure.func(spec_json=_inline_spec())
    owner_before = tracing.get_user_id()
    saved = projects.save_overrides(result['figure_id'], 101,
                                   [{'gid': 'axes_0.title', 'prop': 'fontsize', 'value': 12}],
                                   expected_revision=1)
    for exported in saved['exports'].values():
        resolved = tools.resolve_artifact(account['session_id'], exported['artifact_id'])
        assert resolved is not None
        assert resolved['path'].is_file()
    assert tracing.get_user_id() == owner_before
    assert tracing.get_session_id() == account['session_id']


def test_latest_saved_revision_is_immutable_and_owner_scoped(account):
    from src.research.figure_tools import create_research_figure
    from src.figures import projects, store
    from src.session import files

    _, result = create_research_figure.func(spec_json=_inline_spec())
    original = store.list_revisions(result['figure_id'], 101)[0]
    projects.save_overrides(result['figure_id'], 101,
                            [{'gid': 'axes_0.title', 'prop': 'fontsize', 'value': 12}],
                            expected_revision=1)
    history = store.list_revisions(result['figure_id'], 101)
    assert [item['render_version'] for item in history] == [2]
    # Retiring a snapshot drops only this project's dependency. Published
    # bytes remain immutable and ordinary library GC decides their lifetime.
    for item in original['exports_json'].values():
        assert not files.is_file_referenced(item['file_id'], 101)
        assert files.get_account_file(item['file_id'], 101) is not None
    assert store.list_revisions(result['figure_id'], 202) == []
    assert len(projects.detail_for_owner(result['figure_id'], 101)['revisions']) == 1
    for revision in history:
        for item in revision['exports_json'].values():
            assert files.is_file_referenced(item['file_id'], 101)
    with pytest.raises(ValueError, match='new figure revision'):
        store.update_project(result['figure_id'], 101, overrides_json=[])
    assert store.get_project(result['figure_id'], 101)['render_version'] == 2
    assert projects.delete_for_owner(result['figure_id'], 101)
    assert store.list_revisions(result['figure_id'], 101) == []
    for revision in history:
        for item in revision['exports_json'].values():
            assert not files.is_file_referenced(item['file_id'], 101)


def test_stale_revision_cannot_overwrite_newer_edit(account):
    from src.research.figure_tools import create_research_figure
    from src.figures import projects

    _, result = create_research_figure.func(spec_json=_inline_spec())
    edit = [{'gid': 'axes_0.title', 'prop': 'fontsize', 'value': 12}]
    projects.save_overrides(result['figure_id'], 101, edit, expected_revision=1)
    with pytest.raises(projects.ProjectError) as exc:
        projects.save_overrides(result['figure_id'], 101, edit, expected_revision=1)
    assert exc.value.status == 409
    assert projects.detail_for_owner(result['figure_id'], 101)['render_version'] == 2


def test_racing_update_is_rejected_at_commit(account, monkeypatch):
    from src.research.figure_tools import create_research_figure
    from src.figures import projects, store

    _, result = create_research_figure.func(spec_json=_inline_spec())
    real_publish = projects.publish_rendered
    losing_exports = []

    def competing_publish(*args, **kwargs):
        files = real_publish(*args, **kwargs)
        losing_exports.extend(files.values())
        assert store.update_project(result['figure_id'], 101,
                                    expected_revision=1, render_version=2, title='Other tab')
        return files

    monkeypatch.setattr(projects, 'publish_rendered', competing_publish)
    with pytest.raises(projects.ProjectError) as exc:
        projects.save_overrides(result['figure_id'], 101,
                                [{'gid': 'axes_0.title', 'prop': 'fontsize', 'value': 12}],
                                expected_revision=1)
    assert exc.value.code == 'revision_conflict'
    assert projects.detail_for_owner(result['figure_id'], 101)['title'] == 'Other tab'
    from src.session import files
    for exported in losing_exports:
        assert files.file_dependencies(exported['file_id'], 101) == []
    for exported in result['exports']:
        if exported.get('file_id'):
            assert files.is_file_referenced(exported['file_id'], 101)


def test_attachment_persistence_failure_does_not_create_transient_project(account, monkeypatch):
    from src.research.context import set_request_tables, reset_request_tables
    from src.research.figure_tools import create_research_figure
    from src.session import files
    from src.figures import store

    def fail(**kwargs):
        raise OSError('injected disk failure')

    monkeypatch.setattr(files, 'register_file', fail)
    token = set_request_tables([{'name': 'data.csv', 'data': b'x,y\nA,1\nB,2\n', 'type': 'text/csv'}])
    try:
        spec = {'chart': 'bar', 'x': {'kind': 'attachment', 'index': 0, 'column': 'x'},
                'y': {'kind': 'attachment', 'index': 0, 'column': 'y'}}
        _, result = create_research_figure.func(spec_json=json.dumps(spec))
    finally:
        reset_request_tables(token)
    assert result['_tool_outcome']['status'] == 'failed'
    assert store.count_projects(101) == 0


def test_project_failure_is_partial_and_not_editable(account, monkeypatch):
    from src.research import figure_tools

    monkeypatch.setattr(figure_tools, '_record_project', lambda **kwargs: {})
    _, result = figure_tools.create_research_figure.func(spec_json=_inline_spec())
    assert result['_tool_outcome']['status'] == 'partial'
    assert result['editable'] is False
    assert result['_tool_outcome']['artifacts']
    assert isinstance(result['figure_placeholder'], str)


def test_missing_rows_and_uncertainty_warnings_are_persisted(account):
    from src.research.figure_tools import create_research_figure
    from src.figures import projects

    spec = json.loads(_inline_spec())
    spec['x']['values'] = ['A', 'B', 'C']
    spec['y']['values'] = [10, None, 30]
    _, result = create_research_figure.func(spec_json=json.dumps(spec))
    provenance = result['data_provenance']
    assert provenance['retained_row_indices'] == [0, 2]
    assert provenance['excluded_rows'][0]['row_index'] == 1
    detail = projects.detail_for_owner(result['figure_id'], 101)
    assert detail['manifest']['data_provenance'] == provenance


def test_legacy_script_edit_is_rejected_without_executing(account, monkeypatch):
    from src.figures import projects, store, sandbox

    project = store.create_project(owner_user_id=101, source_kind='script',
                                   source={'script': 'raise RuntimeError("never execute")'})
    monkeypatch.setattr(sandbox, 'select_backend', lambda **kwargs: None)
    assert projects.detail_for_owner(project['figure_id'], 101)['editable'] is False
    with pytest.raises(projects.ProjectError) as exc:
        projects.save_overrides(project['figure_id'], 101, [])
    assert exc.value.code == 'sandbox_unavailable'


def test_project_exports_survive_expiry_until_project_deleted(account, monkeypatch):
    from src.research.figure_tools import create_research_figure
    from src.research import tools
    from src.figures import projects
    from src.session import files

    _, result = create_research_figure.func(spec_json=_inline_spec())
    exported = [item for item in result['exports'] if item.get('artifact_id')]
    monkeypatch.setattr(tools, '_ARTIFACT_TTL_SEC', -1)
    assert tools.cleanup_expired_artifacts() == 0
    for item in exported:
        assert tools.resolve_artifact(account['session_id'], item['artifact_id']) is not None
        assert files.is_file_referenced(item['file_id'], 101)
    assert projects.delete_for_owner(result['figure_id'], 101)
    assert tools.cleanup_expired_artifacts() == 3
    for item in exported:
        assert files.get_account_file(item['file_id'], 101) is None


def test_project_pins_source_and_records_source_digest(account):
    from src.session import files
    from src.research.figure_tools import create_research_figure
    from src.figures import projects, store

    source = files.register_file(session_id=account['session_id'], owner_user_id=101,
                                 filename='source.csv', content=b'x,y\nA,1\nB,2\n', mime='text/csv')
    spec = {'chart': 'bar', 'x': {'kind': 'account_file', 'file_id': source['file_id'], 'column': 'x'},
            'y': {'kind': 'account_file', 'file_id': source['file_id'], 'column': 'y'}}
    _, result = create_research_figure.func(spec_json=json.dumps(spec))
    project = store.get_project(result['figure_id'], 101)
    assert project['data_refs_json'][0]['sha256'] == source['sha256']
    assert files.delete_account_file(source['file_id'], 101) is False
    assert projects.delete_for_owner(result['figure_id'], 101)
    assert files.delete_account_file(source['file_id'], 101) is True


def test_source_modified_outside_registry_is_rejected(account):
    from src.session import files
    from src.figures import projects

    source = files.register_file(session_id=account['session_id'], owner_user_id=101,
                                 filename='source.csv', content=b'x,y\nA,1\nB,2\n', mime='text/csv')
    project = projects.import_from_library(101, account['session_id'], source['file_id'])
    path = files.get_account_file(source['file_id'], 101)['path']
    path.write_bytes(b'x,y\nA,100\nB,200\n')
    with pytest.raises(projects.ProjectError) as exc:
        projects.save_overrides(project['figure_id'], 101, [])
    assert exc.value.code == 'source_changed'


def test_failed_initial_project_commit_releases_source_pins(account, monkeypatch):
    from src.session import files
    from src.figures import store
    from src.research.figure_tools import create_research_figure

    source = files.register_file(session_id=account['session_id'], owner_user_id=101,
                                 filename='source.csv', content=b'x,y\nA,1\nB,2\n', mime='text/csv')
    monkeypatch.setattr(store, 'update_project', lambda *args, **kwargs: False)
    spec = {'chart': 'bar', 'x': {'kind': 'account_file', 'file_id': source['file_id'], 'column': 'x'},
            'y': {'kind': 'account_file', 'file_id': source['file_id'], 'column': 'y'}}
    _, result = create_research_figure.func(spec_json=json.dumps(spec))
    assert result['_tool_outcome']['status'] == 'partial'
    assert store.count_projects(101) == 0
    assert files.file_dependencies(source['file_id'], 101) == []


def test_legacy_project_is_pinned_before_cleanup(account, monkeypatch):
    from src.figures import store, projects
    from src.research import tools
    from src.research.figure_tools import create_research_figure
    from src.session import files

    _, result = create_research_figure.func(spec_json=_inline_spec())
    # Synthetic legacy state: project pointers exist, reference tables do not.
    with store._connect() as conn:
        pins = conn.execute('SELECT resource_id FROM figure_project_pins').fetchall()
        conn.execute('DELETE FROM figure_project_pins')
        conn.execute('UPDATE figure_projects SET pins_version=0')
    for pin in pins:
        files.release_resource_references(101, 'figure', pin['resource_id'])
    monkeypatch.setattr(tools, '_ARTIFACT_TTL_SEC', -1)
    assert tools.cleanup_expired_artifacts() == 0
    for item in result['exports']:
        if item.get('file_id'):
            assert files.is_file_referenced(item['file_id'], 101)
    assert store.migrate_resource_references()['migrated'] == 0
    assert projects.delete_for_owner(result['figure_id'], 101)
    assert tools.cleanup_expired_artifacts() == 3

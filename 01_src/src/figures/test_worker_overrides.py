"""Worker protocol checks use only the explicitly development-only LocalBackend.

These synthetic fixtures do not establish Docker/container isolation. Production
still fails closed if the configured sandbox is unavailable.
"""
import json

import pytest

from src.research.test_figure_tools import account
from src.figures.test_sandbox import GOOD_SCRIPT


def _script_project(account):
    from src.figures import store
    return store.create_project(owner_user_id=101, source_kind='script',
                                source={'kind': 'script', 'script': GOOD_SCRIPT},
                                session_id=account['session_id'])['figure_id']


def test_development_worker_replays_override_and_exports_manifest_file():
    from src.figures.sandbox import LocalBackend, SandboxJob
    edit = {'gid': 'axes_0.title', 'prop': 'fontsize', 'value': 16}
    result = LocalBackend().run(SandboxJob(
        script=GOOD_SCRIPT, replay_overrides=True, overrides=[edit], dpi=100,
        outputs={'svg': 'figure.svg', 'png': 'figure.png', 'pdf': 'figure.pdf', 'manifest': 'objects.json'}))
    assert result.ok, result.describe()
    report = json.loads(result.stdout)
    assert report['override_protocol'] == 1
    assert report['applied_count'] == 1
    assert 'manifest' not in report
    manifest = json.loads(result.outputs['manifest'])
    title = next(item for item in manifest['objects'] if item['gid'] == edit['gid'])
    assert title['fontsize'] == 16
    assert result.outputs['svg'].startswith(b'<?xml')
    assert result.outputs['png'].startswith(b'\x89PNG')
    assert result.outputs['pdf'].startswith(b'%PDF-')


@pytest.mark.parametrize('edit', [
    {'gid': 'axes_0.title', 'prop': 'text', 'value': 'x' * 2001},
    {'gid': 'not-an-object', 'prop': 'color', 'value': '#ffffff'},
    {'prop': 'color', 'value': '#ffffff'},
])
def test_development_worker_rejects_invalid_override(edit):
    from src.figures.sandbox import LocalBackend, SandboxJob
    result = LocalBackend().run(SandboxJob(
        script=GOOD_SCRIPT, replay_overrides=True, overrides=[edit],
        outputs={'svg': 'figure.svg', 'manifest': 'objects.json'}))
    assert not result.ok
    assert not result.outputs


def test_missing_worker_input_is_rejected_even_if_script_ignores_it():
    from src.figures.sandbox import LocalBackend, SandboxJob
    result = LocalBackend().run(SandboxJob(
        script=GOOD_SCRIPT, inputs={'data': 'missing.csv'},
        outputs={'svg': 'figure.svg'}))
    assert not result.ok
    assert '输入文件缺失' in result.describe()


def test_script_preview_and_save_share_worker_bytes(account, monkeypatch):
    from src.config import settings
    from src.figures import projects, store, core
    from src.session import files
    # Explicit test-only opt-in. Never enable this backend in a shared service.
    monkeypatch.setattr(settings, 'FIGURES_SANDBOX_BACKEND', 'local')
    monkeypatch.setattr(settings, 'FIGURES_SANDBOX_ALLOW_LOCAL', True)
    monkeypatch.setattr(core, 'capture_figure', lambda *a, **k: pytest.fail('script executed in web process'))
    figure_id = _script_project(account)
    edits = [{'gid': 'axes_0.title', 'prop': 'color', 'value': '#b64342'}]
    preview = projects.preview_overrides(figure_id, 101, edits, expected_revision=0)
    assert preview['render_version'] == 0
    assert store.get_project(figure_id, 101)['exports_json'] == {}
    saved = projects.save_overrides(figure_id, 101, edits, expected_revision=0)
    svg = files.get_account_file(saved['exports']['svg']['file_id'], 101)
    assert svg['path'].read_text(encoding='utf-8') == preview['svg']
    assert saved['render_version'] == 1
    assert projects.detail_for_owner(figure_id, 101)['editable'] is True
    pending = [{'gid': 'axes_0.title', 'prop': 'fontsize', 'value': 14}]
    next_preview = projects.preview_overrides(figure_id, 101, pending, expected_revision=1)
    assert len(next_preview['overrides']) == 2
    title = next(item for item in next_preview['manifest']['objects'] if item['gid'] == 'axes_0.title')
    assert title['fontsize'] == 14
    assert title['color'] == '#b64342'


def test_script_tool_records_complete_manifest_from_worker_file(account, monkeypatch):
    from src.config import settings
    from src.figures import projects
    from src.research.figure_tools import run_research_figure_script
    monkeypatch.setattr(settings, 'FIGURES_SANDBOX_BACKEND', 'local')
    monkeypatch.setattr(settings, 'FIGURES_SANDBOX_ALLOW_LOCAL', True)
    _, result = run_research_figure_script.func(script=GOOD_SCRIPT, formats_json=['svg'])
    assert result['editable'] is True
    detail = projects.detail_for_owner(result['figure_id'], 101)
    assert detail['manifest']['version'] == 3
    assert len(json.dumps(detail['manifest'])) > 8000  # old stdout truncation loses this inventory
    assert any(item['role'] == 'spine' for item in detail['manifest']['objects'])


def test_script_replay_fails_closed_without_sandbox(account, monkeypatch):
    from src.config import settings
    from src.figures import projects, core
    monkeypatch.setattr(settings, 'FIGURES_SANDBOX_BACKEND', 'off')
    monkeypatch.setattr(core, 'capture_figure', lambda *a, **k: pytest.fail('unsafe host fallback'))
    with pytest.raises(projects.ProjectError) as exc:
        projects.preview_overrides(_script_project(account), 101, [])
    assert exc.value.code == 'sandbox_unavailable'
    assert exc.value.status == 503


def test_script_inputs_without_reproducible_mapping_are_not_dropped(account, monkeypatch):
    from src.figures import projects, store, sandbox
    monkeypatch.setattr(sandbox, 'select_backend', lambda **k: pytest.fail('unreproducible script ran'))
    project = store.create_project(owner_user_id=101, source_kind='script',
        source={'kind': 'script', 'script': GOOD_SCRIPT, 'inputs': {'data': 'old.csv'}})
    with pytest.raises(projects.ProjectError) as exc:
        projects.preview_overrides(project['figure_id'], 101, [])
    assert exc.value.code == 'script_inputs_unavailable'


def test_host_accepts_font_verified_in_worker_environment(account, monkeypatch):
    from src.figures import projects, sandbox, overrides
    edit = {'gid': 'axes_0.title', 'prop': 'fontfamily', 'value': 'Container Only Font'}
    manifest = {'version': 2, 'font_families': ['Container Only Font'],
                'objects': [{'gid': 'axes_0.title', 'role': 'title', 'editable': ['fontfamily']}]}
    class FontWorker:
        def run(self, job):
            return sandbox.SandboxResult(ok=True,
                stdout=json.dumps({'override_protocol': 1, 'applied_count': 1}), outputs={
                    'svg': b'<svg/>', 'manifest': json.dumps(manifest).encode(),
                    'overrides': json.dumps([edit]).encode()})
    monkeypatch.setattr(sandbox, 'select_backend', lambda **k: FontWorker())
    monkeypatch.setattr(overrides, '_normalise_fontfamily', lambda *a: pytest.fail('host resolved container font'))
    preview = projects.preview_overrides(_script_project(account), 101, [edit])
    assert preview['overrides'] == [edit]
    assert preview['manifest']['font_families'] == ['Container Only Font']


def test_old_worker_cannot_silently_ignore_overrides(account, monkeypatch):
    from src.figures import projects, sandbox
    class OldWorker:
        def run(self, job):
            return sandbox.SandboxResult(ok=True, stdout=json.dumps({'ok': True}),
                outputs={'svg': b'<svg/>', 'manifest': b'{"objects": []}'})
    monkeypatch.setattr(sandbox, 'select_backend', lambda **k: OldWorker())
    with pytest.raises(projects.ProjectError) as exc:
        projects.preview_overrides(_script_project(account), 101, [])
    assert exc.value.code == 'worker_protocol_invalid'

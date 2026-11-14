"""Temporary-store script dependencies for questions and figure projects."""
import json
import time
from types import SimpleNamespace

import pytest

from src.core import account_leases, jobs, task_events, user_questions as questions
from src.figures import store
from src.research import script_assets as assets
from src.session import files

OWNER = 991890
SESSION = 'lifecycle_session'


@pytest.fixture(autouse=True)
def temporary_lifecycle(monkeypatch, tmp_path):
    from src import auth, config
    monkeypatch.setattr(config, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(config, 'settings', SimpleNamespace(RESEARCH_SCRIPT_IDLE_DAYS=7))
    monkeypatch.setattr(auth, 'user_by_id', lambda owner: {'id': owner, 'active': True})
    monkeypatch.setattr(account_leases, '_states', {})
    monkeypatch.setattr(jobs, '_DB_PATH', tmp_path / 'jobs.sqlite3')
    monkeypatch.setattr(files, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(files, 'DB_PATH', tmp_path / 'files.sqlite3')
    monkeypatch.setattr(files, 'FILES_ROOT', tmp_path / 'files')
    monkeypatch.setattr(files, 'migrate_legacy_files_for_account', lambda owner: 0)
    store.set_state_dir(tmp_path / 'figures')
    files.init_store()
    token = account_leases.set_current_account_lease(None)
    yield tmp_path
    account_leases.reset_current_account_lease(token)
    store.set_state_dir(None)


def script(source='x=1', *, owner=OWNER, session=SESSION):
    return assets.save_script(owner, session, name='synthetic.py', source=source)['script_ref']


def script_pins(ref=None):
    with assets._connect() as conn:
        if ref is None:
            return [dict(row) for row in conn.execute('SELECT * FROM pins')]
        return [dict(row) for row in conn.execute('SELECT * FROM pins WHERE script_id=? AND version=?',
            (ref['id'], ref['version']))]


def project(ref=None, **kwargs):
    return store.create_project(owner_user_id=OWNER, session_id=SESSION, source_kind='script',
        source={'kind': 'script', 'script': 'x=1', **({'script_ref': ref} if ref else {})}, **kwargs)


def question(ref=None, *, context=None):
    job = jobs.claim_job(SESSION, 'question-request', owner_user_id=OWNER).job_id
    task_events.init_budget(job, time.time() + 120)
    if context is None:
        context = {'script_refs': [ref]} if ref else {}
    value = questions.create_question(job, owner_user_id=OWNER, session_id=SESSION,
        prompt='Which synthetic setting?', options=[], allow_text=True, context=context)
    jobs.update_job(job, status='awaiting_input')
    return job, value


def test_pending_question_keeps_script_and_expiration_releases_it():
    ref = script()
    _, value = question(ref)
    assert script_pins(ref)[0]['resource_id'] == value['id']
    future = time.time() + 8 * 86400
    assert assets.cleanup_scripts(now=future)['expired_scripts'] == 0
    assert questions.expire_questions(now=future) == 1
    assert script_pins(ref) == []
    assert assets.cleanup_scripts(now=future)['expired_scripts'] == 1


def test_question_cancellation_releases_script_dependencies():
    ref = script()
    job, value = question(ref)
    result = questions.answer_question(job, value['id'], owner_user_id=OWNER, session_id=SESSION,
        version=1, cancelled=True, client_request_id='cancel-question')
    assert result['question']['status'] == 'cancelled'
    assert script_pins(ref) == []


def test_answer_transfers_question_pin_to_child_lease_until_real_work_finishes():
    ref = script()
    job, value = question(context={'context_working_state': {'script_refs': [ref]}})
    answer = questions.answer_question(job, value['id'], owner_user_id=OWNER, session_id=SESSION,
        version=1, text='Use it', client_request_id='answer-question')
    child = answer['child_job_id']
    assert questions.get_continuation(child, owner_user_id=OWNER, session_id=SESSION)['script_refs'] == [ref]
    assert len(script_pins(ref)) == 1
    with account_leases.account_lease(OWNER) as lease:
        questions.pin_continuation_inputs(child, owner_user_id=OWNER, session_id=SESSION, lease=lease)
        pins = script_pins(ref)
        assert len(pins) == 1
        assert pins[0]['resource_id'] == 'child:' + child
        account_leases.close_admission(OWNER)
        assert assets.cleanup_scripts(now=time.time() + 8 * 86400)['expired_scripts'] == 0
    assert script_pins(ref) == []
    assert assets.cleanup_scripts(now=time.time() + 8 * 86400)['expired_scripts'] == 1


@pytest.mark.parametrize('answered', [False, True])
def test_new_chat_releases_only_unanswered_question_script_pins(answered):
    ref = script()
    parent, value = question(ref)
    if answered:
        questions.answer_question(parent, value['id'], owner_user_id=OWNER, session_id=SESSION,
            version=1, text='Use it', client_request_id='accepted-before-new-chat')
    assert script_pins(ref)
    new_job = jobs.claim_job(SESSION, 'independent-new-chat', owner_user_id=OWNER).job_id
    count = questions.supersede_pending_questions(new_job, owner_user_id=OWNER, session_id=SESSION)
    assert count == (0 if answered else 1)
    assert bool(script_pins(ref)) is answered
    future = time.time() + 8 * 86400
    assert assets.cleanup_scripts(now=future)['expired_scripts'] == (0 if answered else 1)


def test_saved_question_context_adds_protection_and_preserves_previously_pinned_scripts():
    first, second = script('x=1'), script('x=2')
    job, _ = question(first)
    questions.save_question_context(job, {'context_working_state': {'script_refs': [second]}})
    assert len(script_pins(first)) == len(script_pins(second)) == 1
    questions.expire_questions(now=time.time() + 2 * 86400)
    assert script_pins() == []


@pytest.mark.parametrize('owner,session', [(OWNER + 1, SESSION), (OWNER, 'foreign_session')])
def test_question_rejects_foreign_script_refs_and_does_not_leak_partial_pins(owner, session):
    local = script()
    foreign = script('x=2', owner=owner, session=session)
    with pytest.raises(LookupError):
        question(context={'script_refs': [local, foreign]})
    assert script_pins() == []
    with task_events.transaction() as conn:
        assert conn.execute('SELECT count(*) FROM task_questions').fetchone()[0] == 0


def test_question_publication_failure_rolls_back_script_pins(monkeypatch):
    ref = script()
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic event publication failure')
    monkeypatch.setattr(questions, '_append', fail)
    with pytest.raises(RuntimeError):
        question(ref)
    assert script_pins(ref) == []


def test_figure_creation_pins_script_without_any_file_dependencies_and_delete_releases():
    ref = script()
    value = project(ref)
    assert len(script_pins(ref)) == 1
    assert assets.cleanup_scripts(now=time.time() + 8 * 86400)['expired_scripts'] == 0
    assert store.delete_project(value['figure_id'], OWNER)
    assert script_pins(ref) == []
    assert assets.cleanup_scripts(now=time.time() + 8 * 86400)['expired_scripts'] == 1


def test_figure_update_retains_new_source_and_releases_retired_script_pin():
    first, second = script('x=1'), script('x=2')
    value = project(first)
    assert store.update_project(value['figure_id'], OWNER, expected_revision=0,
        source_json={'kind': 'script', 'script_ref': second, 'script': 'x=2'})
    assert script_pins(first) == []
    assert len(script_pins(second)) == 1
    assert store.delete_project(value['figure_id'], OWNER)
    assert script_pins(second) == []


def test_figure_failed_cas_releases_only_its_new_pin():
    first, second = script('x=1'), script('x=2')
    value = project(first)
    assert not store.update_project(value['figure_id'], OWNER, expected_revision=99,
        source_json={'kind': 'script', 'script_ref': second})
    assert len(script_pins(first)) == 1
    assert script_pins(second) == []


@pytest.mark.parametrize('owner,session', [(OWNER + 1, SESSION), (OWNER, 'foreign_session')])
def test_figure_creation_rejects_script_owner_or_session_mismatch(owner, session):
    foreign = script(owner=owner, session=session)
    with pytest.raises(LookupError):
        project(foreign)
    assert script_pins() == []
    assert store.list_projects(OWNER) == []


def test_figure_quota_failure_compensates_script_and_file_pins(monkeypatch):
    ref = script()
    monkeypatch.setenv('CITRUS_FIGURE_PROJECTS_PER_ACCOUNT', '1')
    first = project(ref)
    source = files.register_file(session_id=SESSION, owner_user_id=OWNER, filename='synthetic.csv', content=b'x\n1\n')
    with pytest.raises(store.ProjectQuotaExceeded):
        project(ref, data_refs=[{'file_id': source['file_id'], 'sha256': source['sha256']}])
    assert len(script_pins(ref)) == 1
    with files._connect() as conn:
        assert conn.execute('SELECT count(*) FROM session_file_resource_refs WHERE file_id=?',
            (source['file_id'],)).fetchone()[0] == 0
    store.delete_project(first['figure_id'], OWNER)


def test_figure_existing_file_pins_survive_script_dependency_addition():
    ref = script()
    source = files.register_file(session_id=SESSION, owner_user_id=OWNER, filename='synthetic.csv', content=b'x\n1\n')
    value = project(ref, data_refs=[{'file_id': source['file_id'], 'sha256': source['sha256']}])
    with files._connect() as conn:
        assert conn.execute('SELECT count(*) FROM session_file_resource_refs WHERE file_id=?',
            (source['file_id'],)).fetchone()[0] == 1
    store.update_project(value['figure_id'], OWNER, title='updated')
    assert len(script_pins(ref)) == 1
    with files._connect() as conn:
        assert conn.execute('SELECT count(*) FROM session_file_resource_refs WHERE file_id=?',
            (source['file_id'],)).fetchone()[0] == 1
    store.delete_project(value['figure_id'], OWNER)
    assert script_pins(ref) == []


def test_script_pin_preserves_existing_dataset_parent_closure():
    import pandas as pd
    from src.research.analysis_results import persist_prepared_dataset
    ref = script()
    source = files.register_file(session_id=SESSION, owner_user_id=OWNER,
        filename='synthetic.csv', content=b'x\n1\n')
    dataset = persist_prepared_dataset(OWNER, pd.DataFrame({'x': [1]}), session_id=SESSION,
        parents=[{'kind': 'input_snapshot', 'file_id': source['file_id'], 'sha256': source['sha256']}],
        execution_id='synthetic-execution', script_sha256=ref['sha256'])
    value = project(ref, data_refs=[dataset.ref])
    with files._connect() as conn:
        protected = {row[0] for row in conn.execute('SELECT file_id FROM session_file_resource_refs')}
    assert protected == {source['file_id'], dataset.payload_file_ref}
    store.update_project(value['figure_id'], OWNER, title='updated')
    assert len(script_pins(ref)) == 1
    with files._connect() as conn:
        assert {row[0] for row in conn.execute('SELECT file_id FROM session_file_resource_refs')} == protected
    store.delete_project(value['figure_id'], OWNER)
    with files._connect() as conn:
        assert conn.execute('SELECT count(*) FROM session_file_resource_refs').fetchone()[0] == 0
    assert script_pins(ref) == []


def test_invalid_context_update_does_not_create_new_script_pins():
    first, second = script('x=1'), script('x=2')
    job, _ = question(first)
    with pytest.raises(TypeError):
        questions.save_question_context(job, {'script_refs': [second], 'invalid': object()})
    assert len(script_pins(first)) == 1
    assert script_pins(second) == []


def test_idle_expiration_is_enforced_on_read_even_before_gc_but_pins_override():
    expired, protected = script('x=1'), script('x=2')
    value = project(protected)
    with assets._connect() as conn:
        conn.execute('UPDATE scripts SET accessed=?', (time.time() - 8 * 86400,))
    with pytest.raises(LookupError, match='expired'):
        assets.get_script(OWNER, SESSION, expired, touch=True)
    with pytest.raises(LookupError, match='expired'):
        assets.pin_script(OWNER, SESSION, expired, 'figure', 'cannot-revive')
    assert assets.get_script(OWNER, SESSION, protected)['script_ref'] == protected
    assert [item['script_ref'] for item in assets.list_scripts(OWNER, SESSION)] == [protected]
    recreated = script('x=1')
    assert recreated['id'] != expired['id']
    store.delete_project(value['figure_id'], OWNER)
    with pytest.raises(LookupError, match='expired'):
        assets.get_script(OWNER, SESSION, protected)
    assert assets.release_script_pin(OWNER, SESSION, protected, 'figure', 'missing-pin') == 0


def test_legacy_script_without_ref_and_account_purge_remain_compatible():
    legacy = project()
    assert script_pins() == []
    assert store.update_project(legacy['figure_id'], OWNER, title='legacy')
    ref = script()
    project(ref)
    account_leases.close_admission(OWNER)
    assert store.purge_account(OWNER) == 2
    assert script_pins() == []
    assert assets.purge_owner(OWNER) == 1

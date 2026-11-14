"""Synthetic script-store contracts; all databases live in pytest tmp_path."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from types import SimpleNamespace

import pytest

from src.research import script_assets as assets
from src.core import account_leases

OWNER = 991870
SESSION = 'script_assets_session'
OTHER_SESSION = 'other_assets_session'


@pytest.fixture(autouse=True)
def temporary_store(monkeypatch, tmp_path):
    from src import auth, config
    monkeypatch.setattr(config, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(config, 'settings', SimpleNamespace(RESEARCH_SCRIPT_IDLE_DAYS=7))
    monkeypatch.setattr(auth, 'user_by_id', lambda owner: {'id': owner, 'active': True})
    monkeypatch.setattr(account_leases, '_states', {})
    token = account_leases.set_current_account_lease(None)
    yield tmp_path
    account_leases.reset_current_account_lease(token)


def save(source='print(1)\n', **kwargs):
    return assets.save_script(OWNER, SESSION, name='analysis.py', source=source, **kwargs)


def begin(ref, **kwargs):
    values = {'inputs': {'dataset_ref': {'id': 'opaque-table'}}, 'parameters': {'seed': 123},
              'output_contract': {'kind': 'table'}, 'environment': {'python': '3.13'},
              'logical_task_id': 'logical-task'}
    values.update(kwargs)
    return assets.begin_run(OWNER, SESSION, ref, **values)


def test_versions_are_immutable_and_compare_and_swap_is_required():
    first = save()
    ref = first['script_ref']
    second = save('print(2)\n', script_ref=ref, expected_version=1)
    assert second['script_ref']['id'] == ref['id']
    assert second['script_ref']['version'] == 2
    assert assets.get_script(OWNER, SESSION, ref)['source'] == 'print(1)\n'
    assert assets.get_script(OWNER, SESSION, second['script_ref'])['source'] == 'print(2)\n'
    for expected in (None, 1, 2, True):
        with pytest.raises(ValueError, match='conflict'):
            save('print(3)', script_ref=ref, expected_version=expected)
    with pytest.raises(ValueError):
        save('print(3)', script_ref=second['script_ref'])
    assert len(assets.list_scripts(OWNER, SESSION)) == 1
    assert 'source' not in assets.list_scripts(OWNER, SESSION)[0]


def test_same_session_body_dedup_and_other_scopes_are_independent():
    first = save()
    duplicate = assets.save_script(OWNER, SESSION, name='another.py', source=first['source'])
    assert duplicate['script_ref'] == first['script_ref']
    assert duplicate['name'] == first['name']
    assert save(script_ref=first['script_ref'], expected_version=1)['script_ref'] == first['script_ref']
    other = assets.save_script(OWNER, OTHER_SESSION, name='same.py', source=first['source'])
    foreign = assets.save_script(OWNER + 1, SESSION, name='same.py', source=first['source'])
    assert len({first['script_ref']['id'], other['script_ref']['id'], foreign['script_ref']['id']}) == 3
    for owner, session in ((OWNER + 1, SESSION), (OWNER, OTHER_SESSION)):
        with pytest.raises(LookupError):
            assets.get_script(owner, session, first['script_ref'])
        with pytest.raises(LookupError):
            assets.begin_run(owner, session, first['script_ref'], inputs={}, parameters={}, output_contract=None, environment={})


@pytest.mark.parametrize('name', ['../bad.py', 'folder/bad.py', 'folder\\bad.py', 'C:\\bad.py',
    '/bad.py', 'bad.PY', '.hidden.py', 'bad.py.exe', 'CON.py', 'COM1.py', 'bad name.py', 'a\n.py'])
def test_reject_unsafe_script_filenames(name):
    with pytest.raises(ValueError):
        assets.save_script(OWNER, SESSION, name=name, source='x=1')


@pytest.mark.parametrize('source', ['', '  ', 'x' * 200001, '\x00', '\ud800'],
                         ids=['empty', 'blank', 'oversized', 'nul', 'surrogate'])
def test_reject_invalid_or_oversized_source(source):
    with pytest.raises(ValueError):
        save(source)


def test_accept_exact_source_limit_and_unicode():
    source = '#' + '橘' * 199999
    value = save(source)
    assert value['source'] == source
    assert value['script_ref']['sha256'] == hashlib.sha256(source.encode()).hexdigest()


@pytest.mark.parametrize('mutate', [lambda r: {**r, 'kind': 'file'}, lambda r: {**r, 'version': True},
    lambda r: {**r, 'version': 0}, lambda r: {**r, 'sha256': 'a'}, lambda r: {**r, 'id': '../secret'},
    lambda r: {**r, 'owner': OWNER}, lambda r: {'id': r['id']}])
def test_strict_refs(mutate):
    ref = save()['script_ref']
    with pytest.raises(ValueError):
        assets.get_script(OWNER, SESSION, mutate(ref))


def test_source_hash_verified_on_all_reads_and_runs():
    ref = save()['script_ref']
    run = begin(ref)
    with assets._connect() as conn:
        conn.execute("UPDATE versions SET source='tampered' WHERE id=?", (ref['id'],))
    for operation in (lambda: assets.get_script(OWNER, SESSION, ref),
                      lambda: assets.list_scripts(OWNER, SESSION),
                      lambda: begin(ref), lambda: assets.get_run(OWNER, SESSION, run['run_id']),
                      lambda: assets.list_runs(OWNER, SESSION)):
        with pytest.raises(ValueError, match='integrity'):
            operation()


def test_runs_retry_idempotency_and_new_inputs_new_runs():
    ref = save()['script_ref']
    first = begin(ref, request_key='request-one')
    assert begin(ref, request_key='request-one')['run_id'] == first['run_id']
    for changed in ({'inputs': {'dataset_ref': 'different'}}, {'parameters': {'seed': 4}},
                    {'environment': {'python': 'different'}}, {'output_contract': None},
                    {'logical_task_id': 'another-task'}):
        with pytest.raises(ValueError, match='conflict'):
            begin(ref, request_key='request-one', **changed)
    changed = begin(ref, inputs={'dataset_ref': 'different'}, request_key='request-two')
    assert changed['run_id'] != first['run_id']
    assert begin(ref)['run_id'] != begin(ref)['run_id']
    assert len(assets.list_runs(OWNER, SESSION)) == 4
    with pytest.raises(LookupError):
        assets.get_run(OWNER + 1, SESSION, first['run_id'])
    with pytest.raises(LookupError):
        assets.finish_run(OWNER, OTHER_SESSION, first['run_id'], status='failed')


def test_failed_runs_retain_exact_version_inputs_and_redacted_bounded_errors():
    ref = save()['script_ref']
    run = begin(ref)
    save('print(2)', script_ref=ref, expected_version=1)
    error = 'Traceback File "C:\\Users\\private-name\\analysis.py", line 2\nPermissionError /home/private-name/key.txt ' + 'x' * 10000
    finished = assets.finish_run(OWNER, SESSION, run['run_id'], status='failed', error=error)
    assert finished['script_ref'] == ref
    assert finished['inputs'] == run['inputs']
    assert finished['parameters'] == run['parameters']
    assert 'private-name' not in finished['error']
    assert len(finished['error']) <= 4000
    assert finished['finished_at'] >= finished['created_at']
    assert assets.finish_run(OWNER, SESSION, run['run_id'], status='failed', error=error) == finished
    with pytest.raises(ValueError, match='terminal'):
        assets.finish_run(OWNER, SESSION, run['run_id'], status='succeeded', result={'ok': True})
    assert len(assets.list_runs(OWNER, SESSION, script_ref=ref)) == 1
    assert assets.list_runs(OWNER, SESSION, script_ref=assets.list_scripts(OWNER, SESSION)[0]['script_ref']) == []


@pytest.mark.parametrize('status', ['running', 'success', 'pending', '', None, []])
def test_run_terminal_states_are_strict(status):
    run = begin(save()['script_ref'])
    with pytest.raises(ValueError):
        assets.finish_run(OWNER, SESSION, run['run_id'], status=status)
    assert assets.get_run(OWNER, SESSION, run['run_id'])['status'] == 'running'


@pytest.mark.parametrize('status', ['succeeded', 'partial', 'failed', 'cancelled'])
def test_valid_terminal_states(status):
    run = begin(save()['script_ref'])
    assert assets.finish_run(OWNER, SESSION, run['run_id'], status=status)['status'] == status


def test_json_provenance_validation_and_bounds():
    ref = save()['script_ref']
    for values in ({'inputs': []}, {'parameters': {'bad': float('nan')}},
                   {'environment': {'large': 'x' * 64000}}, {'output_contract': []},
                   {'parameters': {'not_json': object()}}, {'request_key': '../secret'}):
        with pytest.raises(ValueError):
            begin(ref, **values)
    assert assets.list_runs(OWNER, SESSION) == []
    run = begin(ref)
    finished = assets.finish_run(OWNER, SESSION, run['run_id'], status='succeeded', result={'large': 'x' * 64000})
    assert finished['status'] == 'succeeded'
    assert finished['result']['run_result_compacted'] is True
    assert finished['result']['omitted_result_fields'] == ['large']


def test_list_limits_and_no_store_identity_or_path_leaks():
    for i in range(4):
        begin(save(f'x={i}')['script_ref'])
    assert len(assets.list_scripts(OWNER, SESSION, limit=2)) == 2
    assert len(assets.list_runs(OWNER, SESSION, limit=2)) == 2
    for invalid in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            assets.list_runs(OWNER, SESSION, limit=invalid)
    public = json.dumps(assets.list_scripts(OWNER, SESSION) + assets.list_runs(OWNER, SESSION))
    for private in ('research_scripts.sqlite3', 'storage_path', 'owner_user_id', SESSION, str(OWNER)):
        assert private not in public
    assert assets._limit(1000000) == 100


def test_idle_ttl_listing_does_not_refresh_but_explicit_use_does(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(assets.time, 'time', lambda: clock[0])
    ref = save()['script_ref']
    clock[0] += 6 * 86400
    assert assets.list_scripts(OWNER, SESSION)[0]['last_used_at'] == 1000
    assert assets.get_script(OWNER, SESSION, ref)['last_used_at'] == 1000
    assert assets.get_script(OWNER, SESSION, ref, touch=True)['last_used_at'] == clock[0]
    assert assets.cleanup_scripts(now=clock[0] + 6 * 86400)['expired_scripts'] == 0
    assert assets.cleanup_scripts(now=clock[0] + 8 * 86400) == {'expired_scripts': 1, 'expired_versions': 1, 'expired_runs': 0}
    with pytest.raises(LookupError):
        assets.get_script(OWNER, SESSION, ref)


def test_gc_keeps_running_and_pinned_families_then_reclaims_all_history(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(assets.time, 'time', lambda: clock[0])
    first = save()['script_ref']
    second = save('print(2)', script_ref=first, expected_version=1)['script_ref']
    run = begin(first)
    # A live execution must be protected even with a short configured idle TTL.
    from src import config
    monkeypatch.setattr(config, 'settings', SimpleNamespace(RESEARCH_SCRIPT_IDLE_DAYS=0.001))
    assert assets.cleanup_scripts(now=1000 + 120)['expired_scripts'] == 0
    monkeypatch.setattr(config, 'settings', SimpleNamespace(RESEARCH_SCRIPT_IDLE_DAYS=7))
    clock[0] = 1000 + 31 * 86400
    assets.finish_run(OWNER, SESSION, run['run_id'], status='failed', error='failure')
    assert assets.cleanup_scripts(now=clock[0] + 6 * 86400)['expired_scripts'] == 0
    assets.pin_script(OWNER, SESSION, first, 'result', 'opaque-result')
    assert assets.cleanup_scripts(now=clock[0] + 30 * 86400)['expired_scripts'] == 0
    assert assets.release_script_pin(OWNER, SESSION, first, 'result', 'opaque-result') == 1
    assert assets.release_script_pin(OWNER, SESSION, first, 'result', 'opaque-result') == 0
    assert assets.cleanup_scripts(now=clock[0] + 30 * 86400) == {'expired_scripts': 1, 'expired_versions': 2, 'expired_runs': 1}
    assert assets.list_runs(OWNER, SESSION) == []
    with pytest.raises(LookupError):
        assets.get_script(OWNER, SESSION, second)


def test_retention_default_and_setting(monkeypatch):
    from src import config
    monkeypatch.setattr(assets.time, 'time', lambda: 1000)
    save()
    monkeypatch.setattr(config, 'settings', SimpleNamespace())
    assert assets.cleanup_scripts(now=1000 + 6 * 86400)['expired_scripts'] == 0
    monkeypatch.setattr(config, 'settings', SimpleNamespace(RESEARCH_SCRIPT_IDLE_DAYS=2))
    assert assets.cleanup_scripts(now=1000 + 3 * 86400)['expired_scripts'] == 1


def test_concurrent_save_compare_swap_and_run_retry_are_atomic():
    ref = save()['script_ref']
    def update(index):
        try:
            return save(f'x={index}', script_ref=ref, expected_version=1)['script_ref']
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        updated = list(pool.map(update, range(4)))
    assert len([v for v in updated if v]) == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(lambda _: begin(ref, request_key='concurrent-retry')['run_id'], range(8)))
    assert len(set(runs)) == 1
    assert len(assets.list_runs(OWNER, SESSION)) == 1


def test_closed_admission_blocks_new_work_retained_execution_can_finish_and_purge_drains():
    ref = save()['script_ref']
    foreign = assets.save_script(OWNER + 1, SESSION, name='foreign.py', source='x=1')['script_ref']
    with account_leases.account_lease(OWNER):
        run = begin(ref)
        account_leases.close_admission(OWNER)
        assert assets.finish_run(OWNER, SESSION, run['run_id'], status='cancelled')['status'] == 'cancelled'
        with pytest.raises(account_leases.AccountDeletionPending):
            assets.purge_owner(OWNER)
    with pytest.raises(account_leases.AccountUnavailable):
        save('x=2')
    assert assets.purge_owner(OWNER) == 1
    assert assets.purge_owner(OWNER) == 0
    assert assets.get_script(OWNER + 1, SESSION, foreign)['source'] == 'x=1'
    with assets._connect() as conn:
        assert conn.execute('SELECT count(*) FROM runs WHERE owner=?', (OWNER,)).fetchone()[0] == 0


def test_abandoned_run_recovery_preserves_failure_for_a_fresh_idle_period(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(assets.time, 'time', lambda: clock[0])
    ref = save()['script_ref']
    run = begin(ref, request_key='abandoned-request')
    assert assets.reconcile_stale_runs(now=1000 + 120) == {'reconciled_runs': 0}
    assert assets.get_run(OWNER, SESSION, run['run_id'])['status'] == 'running'
    clock[0] += 86401
    assert assets.reconcile_stale_runs(now=clock[0]) == {'reconciled_runs': 1}
    failure = assets.get_run(OWNER, SESSION, run['run_id'])
    assert failure['status'] == 'failed'
    assert 'abandoned' in failure['error']
    assert failure['inputs'] == run['inputs']
    assert failure['script_ref'] == ref
    assert begin(ref, request_key='abandoned-request')['run_id'] == run['run_id']
    assert assets.reconcile_stale_runs(now=clock[0]) == {'reconciled_runs': 0}
    assert assets.cleanup_scripts(now=clock[0] + 6 * 86400)['expired_scripts'] == 0
    assert assets.cleanup_scripts(now=clock[0] + 8 * 86400)['expired_scripts'] == 1


def test_cleanup_recovers_stale_runs_without_immediately_destroying_evidence(monkeypatch):
    monkeypatch.setattr(assets.time, 'time', lambda: 1000)
    ref = save()['script_ref']
    run = begin(ref)
    assert assets.cleanup_scripts(now=1000 + 30 * 86400)['expired_scripts'] == 0
    assert assets.get_run(OWNER, SESSION, run['run_id'])['status'] == 'failed'
    assert assets.cleanup_scripts(now=1000 + 38 * 86400)['expired_runs'] == 1


def test_concurrent_identical_body_creation_deduplicates():
    with ThreadPoolExecutor(max_workers=4) as pool:
        refs = list(pool.map(lambda _: save()['script_ref'], range(8)))
    assert all(ref == refs[0] for ref in refs)
    assert len(assets.list_scripts(OWNER, SESSION)) == 1


@pytest.mark.parametrize('owner', [0, -1, True, '1', None])
def test_invalid_host_owner_is_rejected(owner):
    with pytest.raises(ValueError):
        assets.save_script(owner, SESSION, name='safe.py', source='x=1')


def test_new_version_inherits_origin_but_explicit_replacement_is_respected():
    origin = {'kind': 'builtin_script', 'id': 'example', 'version': 1, 'sha256': 'a' * 64}
    first = save(origin_ref=origin)
    second = save('print(2)', script_ref=first['script_ref'], expected_version=1)
    assert second['origin_ref'] == origin
    replacement = {**origin, 'version': 2}
    third = save('print(3)', script_ref=second['script_ref'], expected_version=2, origin_ref=replacement)
    assert third['origin_ref'] == replacement
    assert assets.get_script(OWNER, SESSION, first['script_ref'])['origin_ref'] == origin


def test_public_environment_and_errors_are_redacted_without_breaking_artifact_urls():
    ref = save()['script_ref']
    environment = {'runtime': r'C:\Users\Private Name\bin\python.exe',
                   'image': '/home/Private Name/sandbox.sif', 'nested': {'path': '/mnt/c/secret/model'}}
    run = begin(ref, environment=environment, request_key='private-env')
    assert 'Private Name' not in json.dumps(run)
    assert 'secret' not in json.dumps(run)
    with pytest.raises(ValueError, match='conflict'):
        begin(ref, environment={'runtime': '/another/private/path'}, request_key='private-env')
    url = 'https://example.test/api/artifacts/public-id'
    relative = '/api/artifacts/public-id'
    error = 'Failure at "C:\\Users\\Private Name\\code.py"; see ' + url
    finished = assets.finish_run(OWNER, SESSION, run['run_id'], status='failed', error=error,
        result={'error': 'Cannot open /home/Private Name/key.txt', 'artifact_url': url, 'download_url': relative})
    assert 'Private Name' not in json.dumps(finished)
    assert url in finished['error']
    assert finished['result']['artifact_url'] == url
    assert finished['result']['download_url'] == relative


def test_oversized_result_retains_published_identifiers_and_finishes_idempotently():
    ref = save()['script_ref']
    run = begin(ref)
    result = {'script_report': 'x' * 100000, 'dataset_refs': [{'kind': 'dataset_snapshot', 'id': 'b' * 32,
              'version': 1, 'sha256': 'c' * 64}], '_tool_outcome': {'status': 'complete',
              'reason_code': 'published', 'artifacts': ['/api/artifacts/kept']}}
    finished = assets.finish_run(OWNER, SESSION, run['run_id'], status='succeeded', result=result)
    assert finished['status'] == 'succeeded'
    assert finished['result']['dataset_refs'] == result['dataset_refs']
    assert finished['result']['_tool_outcome'] == result['_tool_outcome']
    assert len(assets._json(finished['result'])) <= assets.MAX_JSON
    assert assets.finish_run(OWNER, SESSION, run['run_id'], status='succeeded', result=result) == finished


def test_budget_reservation_is_atomic_for_concurrent_starts():
    ref = save()['script_ref']
    def reserve(index):
        try:
            return assets.begin_budgeted_run(OWNER, SESSION, ref, inputs={}, parameters={},
                output_contract={}, environment={}, logical_task_id='one-task', request_key=f'request-{index}')
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    assert sum(result is not None for result in results) == 3
    assert len(assets.list_runs(OWNER, SESSION)) == 3


def test_database_path_is_resolved_at_call_time(monkeypatch, temporary_store):
    from src import config
    first = save()['script_ref']
    monkeypatch.setattr(config, 'PROJECT_ROOT', temporary_store / 'second-root')
    assert assets.list_scripts(OWNER, SESSION) == []
    with pytest.raises(LookupError):
        assets.get_script(OWNER, SESSION, first)
    assert save()['script_ref']['id'] != first['id']

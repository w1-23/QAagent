"""Temporary-root execution-loop tests; executor is an offline publication stub."""
import contextvars
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from types import SimpleNamespace

import pytest

from src.core import account_leases, tracing
from src.core.cancel import ToolCancelled
from src.research import script_assets as assets, script_runtime as runtime

OWNER = 991880
SESSION = 'runtime_test_session'
ARTIFACT = '/api/artifacts/accepted-id'
DATASET = {'kind': 'dataset_snapshot', 'id': 'b' * 32, 'version': 1, 'sha256': 'c' * 64}


@pytest.fixture(autouse=True)
def temporary_runtime(monkeypatch, tmp_path):
    from src import auth, config
    monkeypatch.setattr(config, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(config, 'settings', SimpleNamespace(RESEARCH_SCRIPT_IDLE_DAYS=7,
        FIGURES_SANDBOX_BACKEND='stub', FIGURES_SANDBOX_RUNTIME='/home/Private User/bin/runtime',
        FIGURES_SANDBOX_IMAGE=r'C:\Users\Private User\sandbox.sif'))
    monkeypatch.setattr(auth, 'user_by_id', lambda owner: {'id': owner, 'active': True})
    monkeypatch.setattr(account_leases, '_states', {})
    monkeypatch.setattr(runtime, '_task_scope', contextvars.ContextVar('test_script_task', default='task-one'))
    for name, value in [('get_user_id', OWNER), ('get_session_id', SESSION),
                        ('get_job_id', ''), ('get_client_request_id', 'turn-one')]:
        monkeypatch.setattr(tracing, name, lambda value=value: value)
    token = account_leases.set_current_account_lease(None)
    yield tmp_path
    account_leases.reset_current_account_lease(token)


def published(**kwargs):
    return '{}', {'dataset_refs': [DATASET], 'exports': [{'artifact_url': ARTIFACT}],
        '_tool_outcome': {'status': 'complete', 'reason_code': 'accepted', 'artifacts': [ARTIFACT]}}


def execute(executor=published, **kwargs):
    values = {'script': 'print(1)', 'inputs_json': {'data': {'file_id': 'a' * 32}},
              'parameters_json': {'seed': 1}, 'output_name': 'example'}
    values.update(kwargs)
    return runtime.execute_script_request(executor, **values)


def test_parameter_binding_receipts_and_source_dedup_do_not_cache_research_results():
    calls = []
    def executor(**kwargs):
        calls.append(kwargs)
        return published()
    first_content, first = execute(executor)
    second_content, second = execute(executor)
    assert len(calls) == 2
    assert first['script_ref'] == second['script_ref']
    assert first['run_id'] != second['run_id']
    assert first['dataset_refs'] == [DATASET]
    assert json.loads(first_content) == first
    assert json.loads(second_content) == second
    assert calls[0]['script'].endswith('print(1)')
    assert 'parameters = _citrus_parameters_json.loads' in calls[0]['script']
    assert calls[0]['script_record']['parameters'] == {'seed': 1}
    assert 'request_key' not in calls[0]
    run = assets.get_run(OWNER, SESSION, first['run_id'])
    assert run['status'] == 'succeeded'
    assert 'Private User' not in json.dumps(run)


@pytest.mark.parametrize('exc', [ToolCancelled(where='/home/Private User/code.py'),
    type('CancelledError', (Exception,), {})('cancel C:\\Users\\Private User\\run.py')])
def test_cancellation_exception_stays_cancelled_in_receipt_and_record(exc):
    def cancel(**kwargs):
        raise exc
    content, payload = execute(cancel)
    assert payload['_tool_outcome']['status'] == 'cancelled'
    assert payload['_tool_outcome']['reason_code'] == 'script_execution_cancelled'
    assert assets.get_run(OWNER, SESSION, payload['run_id'])['status'] == 'cancelled'
    assert 'Private User' not in content


def test_baseexception_cancellation_is_recorded_before_propagating():
    def cancel(**kwargs):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        execute(cancel)
    assert assets.list_runs(OWNER, SESSION)[0]['status'] == 'cancelled'
    assert account_leases.active_count(OWNER) == 0


def test_exception_paths_are_private_in_both_live_and_persisted_receipts():
    def fail(**kwargs):
        raise RuntimeError('Cannot open "C:\\Users\\Private User\\data.csv" or /home/Private User/data')
    content, payload = execute(fail)
    assert payload['_tool_outcome']['status'] == 'failed'
    assert 'Private User' not in content
    assert 'Private User' not in json.dumps(assets.get_run(OWNER, SESSION, payload['run_id']))


def test_large_published_receipt_survives_bounded_log_and_does_not_leave_running():
    large = 'x' * 100000
    def executor(**kwargs):
        _, payload = published()
        payload['script_report'] = large
        return '{}', payload
    content, payload = execute(executor)
    assert payload['_tool_outcome']['status'] == 'complete'
    assert payload['_tool_outcome']['artifacts'] == [ARTIFACT]
    assert payload['script_report'] == large
    assert json.loads(content)['dataset_refs'] == [DATASET]
    record = assets.get_run(OWNER, SESSION, payload['run_id'])
    assert record['status'] == 'succeeded'
    assert record['result']['run_result_compacted'] is True
    assert record['result']['dataset_refs'] == [DATASET]
    assert record['result']['_tool_outcome']['artifacts'] == [ARTIFACT]
    assert len(assets._json(record['result'])) <= assets.MAX_JSON


def test_non_json_ancillary_metadata_does_not_erase_accepted_receipts():
    def executor(**kwargs):
        _, payload = published()
        payload['debug_metadata'] = {'number': float('nan'), 'unexpected': object()}
        return '{}', payload
    _, payload = execute(executor)
    assert payload['_tool_outcome']['artifacts'] == [ARTIFACT]
    assert payload['dataset_refs'] == [DATASET]
    assert payload['receipt_metadata_normalized'] is True
    assert assets.get_run(OWNER, SESSION, payload['run_id'])['status'] == 'succeeded'


def test_invalid_executor_result_still_terminates_its_reserved_run():
    _, payload = execute(lambda **kwargs: ('bad protocol', 42))
    assert payload['_tool_outcome']['status'] == 'failed'
    assert assets.get_run(OWNER, SESSION, payload['run_id'])['status'] == 'failed'


def test_transient_finalize_failure_retries_without_losing_publication(monkeypatch):
    finish = assets.finish_run
    calls = []
    def transient(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise OSError('temporary write error at /home/Private User/state')
        return finish(*args, **kwargs)
    monkeypatch.setattr(assets, 'finish_run', transient)
    content, payload = execute()
    assert len(calls) == 2
    assert payload['_tool_outcome']['artifacts'] == [ARTIFACT]
    assert payload['run_record']['status'] == 'recovered'
    assert assets.get_run(OWNER, SESSION, payload['run_id'])['status'] == 'succeeded'
    assert 'Private User' not in content


def test_lost_commit_acknowledgment_is_idempotent(monkeypatch):
    finish = assets.finish_run
    calls = []
    def committed_then_failed(*args, **kwargs):
        result = finish(*args, **kwargs)
        calls.append(True)
        if len(calls) == 1:
            raise OSError('lost commit acknowledgment')
        return result
    monkeypatch.setattr(assets, 'finish_run', committed_then_failed)
    _, payload = execute()
    assert len(calls) == 2
    assert payload['_tool_outcome']['status'] == 'complete'
    assert assets.get_run(OWNER, SESSION, payload['run_id'])['status'] == 'succeeded'


def test_persistent_finalize_outage_preserves_receipt_and_explicitly_reports_unpersisted_record(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError('Cannot write /home/Private User/state')
    monkeypatch.setattr(assets, 'finish_run', unavailable)
    content, payload = execute()
    assert payload['_tool_outcome']['status'] == 'complete'
    assert payload['_tool_outcome']['artifacts'] == [ARTIFACT]
    assert payload['dataset_refs'] == [DATASET]
    assert payload['run_record']['status'] == 'not_persisted'
    assert 'Private User' not in content
    # A real persistent database failure cannot honestly promise a durable
    # terminal transition; stale recovery owns that explicitly reported record.
    assert assets.get_run(OWNER, SESSION, payload['run_id'])['status'] == 'running'


def test_failure_budget_uses_full_history_and_same_task_repairs_share_budget():
    def failed(**kwargs):
        return '{}', {'error': 'repair needed', '_tool_outcome': {'status': 'failed', 'artifacts': []}}
    failed_runs = []
    for attempt in range(3):
        _, payload = execute(failed, script=f'print({attempt})', parameters_json={'repair': attempt})
        failed_runs.append(payload['run_id'])
    reference = assets.get_run(OWNER, SESSION, failed_runs[0])['script_ref']
    # More than 100 unrelated newer runs must not hide the three failures.
    for index in range(105):
        run = assets.begin_run(OWNER, SESSION, reference, inputs={}, parameters={}, output_contract={},
            environment={}, logical_task_id=f'unrelated-{index}')
        assets.finish_run(OWNER, SESSION, run['run_id'], status='succeeded')
    calls = []
    def must_not_run(**kwargs):
        calls.append(True)
        return published()
    _, blocked = execute(must_not_run, script='print(99)')
    assert not calls
    assert 'run_id' not in blocked
    assert 'repairs' in blocked['error']
    token = runtime.bind_task_scope('task-two')
    try:
        _, allowed = execute(must_not_run)
    finally:
        runtime.reset_task_scope(token)
    assert len(calls) == 1
    assert allowed['_tool_outcome']['status'] == 'complete'


def test_explicit_request_retries_reuse_finished_run_but_changed_parameters_conflict():
    calls = []
    def executor(**kwargs):
        calls.append(kwargs)
        return published()
    _, first = execute(executor, request_key='real-request-1')
    _, repeated = execute(executor, request_key='real-request-1')
    assert first['run_id'] == repeated['run_id']
    assert repeated['_tool_outcome']['artifacts'] == [ARTIFACT]
    assert len(calls) == 1
    for change in ({'parameters_json': {'seed': 2}}, {'qa_profile_json': {'name': 'different'}}, {'title': 'different'}):
        _, rejected = execute(executor, request_key='real-request-1', **change)
        assert rejected['_tool_outcome']['status'] == 'failed'
        assert 'conflict' in rejected['error']
    assert len(calls) == 1


def test_replaying_third_failed_request_does_not_spend_a_fourth_attempt():
    calls = []
    def failed(**kwargs):
        calls.append(True)
        return '{}', {'error': 'failed', '_tool_outcome': {'status': 'failed', 'artifacts': []}}
    last = None
    for attempt in range(3):
        _, last = execute(failed, request_key=f'failed-{attempt}')
    _, repeated = execute(failed, request_key='failed-2')
    assert repeated['run_id'] == last['run_id']
    assert len(calls) == 3
    _, blocked = execute(failed, request_key='failed-3')
    assert 'run_id' not in blocked
    assert len(calls) == 3


def test_simultaneous_real_request_retry_never_executes_twice():
    started, release = threading.Event(), threading.Event()
    calls = []
    def executor(**kwargs):
        calls.append(True)
        started.set()
        assert release.wait(5)
        return published()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execute, executor, request_key='concurrent-request')
        try:
            assert started.wait(5)
            _, duplicate = execute(executor, request_key='concurrent-request')
            assert duplicate['_tool_outcome']['status'] == 'not_run'
            assert duplicate['run_status'] == 'running'
            assert len(calls) == 1
        finally:
            release.set()
        _, first = future.result(timeout=5)
    assert duplicate['run_id'] == first['run_id']
    assert assets.get_run(OWNER, SESSION, first['run_id'])['status'] == 'succeeded'


def test_cancelled_attempts_do_not_consume_failure_budget():
    def cancelled(**kwargs):
        raise ToolCancelled()
    for _ in range(4):
        assert execute(cancelled)[1]['_tool_outcome']['status'] == 'cancelled'
    assert execute()[1]['_tool_outcome']['status'] == 'complete'


def test_input_change_without_request_key_always_creates_new_run():
    _, first = execute()
    _, changed = execute(parameters_json={'seed': 2}, inputs_json={'data': {'file_id': 'd' * 32}})
    assert first['run_id'] != changed['run_id']
    assert first['script_ref'] == changed['script_ref']


def test_returned_error_payload_is_redacted_and_keeps_published_artifacts():
    def partial(**kwargs):
        return '{}', {'error': 'Failure /home/Private User/private.txt', 'dataset_refs': [DATASET],
            '_tool_outcome': {'status': 'partial', 'reason_code': 'one_failed', 'artifacts': [ARTIFACT]}}
    content, payload = execute(partial)
    assert 'Private User' not in content
    assert payload['_tool_outcome']['artifacts'] == [ARTIFACT]
    assert assets.get_run(OWNER, SESSION, payload['run_id'])['status'] == 'partial'

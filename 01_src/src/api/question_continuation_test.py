"""Durable question HTTP/dispatch contracts with disposable stores and no model."""
import asyncio
import json
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.chat_idempotency_test import runtime, _post, _drain, _payload  # noqa: F401
from src.core import user_questions as questions, task_events as events


def app_for(rt):
    app = FastAPI()
    app.add_api_route('/api/tasks/{job_id}/questions', rt.api.get_task_questions, methods=['GET'])
    app.add_api_route('/api/tasks/{job_id}/questions/{question_id}/answer', rt.api.answer_task_question, methods=['POST'])
    return app


def pending(rt, context=None):
    job = rt.jobs.claim_job('question-session', 'parent-request', owner_user_id=rt.owner).job_id
    events.init_budget(job, time.time() + 100)
    q = questions.create_question(job, owner_user_id=rt.owner, session_id='question-session',
        prompt='Which synthetic metric?', options=[{'id': 'b', 'label': 'B over A', 'description': '', 'refs': []}],
        context=context or {'query': 'Plot the synthetic table', 'visualization_enabled': True})
    rt.jobs.update_job(job, status='awaiting_input')
    events.append_event(job, 'done', {'turn_envelope': {'display_status': 'awaiting_input'}})
    return job, q


def test_http_strict_answer_scope_conflict_and_idempotency(runtime, monkeypatch):
    rt = runtime
    job, q = pending(rt)
    dispatched = []
    async def dispatch(child, *_):
        dispatched.append(child)
        return False
    monkeypatch.setattr(rt.api, '_dispatch_question_child', dispatch)
    body = {'version': 1, 'selected_option_ids': ['b'], 'client_request_id': 'answer-key'}
    url = f'/api/tasks/{job}/questions/{q["id"]}/answer'
    with TestClient(app_for(rt)) as client:
        assert client.post(url, json={**body, 'version': True}).status_code == 422
        first = client.post(url, json=body)
        assert first.status_code == 200, first.text
        second = client.post(url, json=body)
        assert first.json()['child_job_id'] == second.json()['child_job_id']
        assert len(set(dispatched)) == 1
        assert client.post(url, json={**body, 'text': 'different'}).status_code == 409
        assert client.post(url, json={**body, 'version': 2}).status_code == 409
        rt.user = rt.auth.user_by_id(rt.other)
        assert client.get(f'/api/tasks/{job}/questions').status_code == 404
        assert client.post(url, json=body).status_code == 404


def test_fresh_child_real_api_capacity_lease_and_budget(runtime, monkeypatch):
    rt = runtime
    from src.graph import general_agent_graph
    monkeypatch.setattr(general_agent_graph, 'request_deadline_from_settings', lambda: time.monotonic() + 120)
    captured = []
    class Graph:
        async def astream(self, state, *args, **kwargs):
            captured.append(state)
            yield {'supervisor': {'answer': 'Synthetic continuation completed', 'tool_status': []}}
            yield {'session_save': {'_trace': {'history_saved': False, 'generation_ms': 1}}}
    rt.build.return_value = Graph()
    async def scenario():
        job, q = pending(rt)
        with events.transaction() as conn:
            conn.execute('UPDATE task_budgets SET calls=2,tokens=777 WHERE job_id=?', (job,))
        old = events.replay(job)
        result = questions.answer_question(job, q['id'], owner_user_id=rt.owner,
            session_id='question-session', version=1, selected_option_ids=['b'], client_request_id='answer')
        child = result['child_job_id']
        assert await rt.api._dispatch_question_child(child, rt.user, object())
        for _ in range(2000):
            if rt.jobs.get_job(child)['status'] == 'completed' and rt.leases.active_count(rt.owner) == 0:
                break
            await asyncio.sleep(.005)
        assert rt.jobs.get_job(child)['status'] == 'completed'
        assert not await rt.api._dispatch_question_child(child, rt.user, object())
        assert len(captured) == 1
        assert captured[0]['parent_job_id'] == job and captured[0]['no_replay']
        assert captured[0]['visualization_enabled'] is True
        assert 'B over A' in captured[0]['query']
        budget = events.projection(child)['budget']
        assert budget['calls'] == 2 and budget['tokens'] == 777
        assert budget['deadline'] > time.time()
        assert events.replay(job) == old
        assert rt.reserve.call_count == rt.refund.call_count == 0
        assert rt.leases.active_count(rt.owner) == 0
    asyncio.run(scenario())


def test_history_pending_child_joins_once_then_real_turn_replaces_it(runtime):
    rt = runtime
    job, q = pending(rt)
    result = questions.answer_question(job, q['id'], owner_user_id=rt.owner,
        session_id='question-session', version=1, text='Custom synthetic purpose', client_request_id='answer')
    parent = rt.api._new_history_turn('parent-request')
    view = rt.api._history_with_jobs([parent], 'question-session', rt.owner)
    assert len(view) == 2 and view[1]['history_pending']
    assert view[1]['job_id'] == result['child_job_id']
    child = rt.jobs.get_job(result['child_job_id'])
    real = rt.api._new_history_turn(child['client_request_id'])
    view = rt.api._history_with_jobs([parent, real], 'question-session', rt.owner)
    assert len(view) == 2 and all(not t.get('history_pending') for t in view)
    assert view[0]['replay_cursor'] == 0 and view[1]['parent_job_id'] == job

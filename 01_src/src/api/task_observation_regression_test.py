"""Task observation with real routes and disposable SQLite state."""
import asyncio
import gc
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.api import main
from src.core import jobs, progress_bus, task_events, task_state, tool_results
from src.session import manager


class TaskObservationTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        # SQLite connection/cursor cycles must be finalized before deleting the
        # temporary database on Windows, where an open file cannot be unlinked.
        self.addCleanup(gc.collect)
        self.enterContext(patch.object(jobs, '_DB_PATH', root / 'jobs.db'))
        self.enterContext(patch.object(manager, 'STATE_DIR', root))
        self.enterContext(patch.object(manager, 'DB_PATH', root / 'history.db'))
        self.enterContext(patch.object(manager.SessionManager, '_instance', None))
        self.sessions = manager.SessionManager()
        self.enterContext(patch.object(main, 'session_manager', self.sessions))
        self.owner = 111
        self.enterContext(patch.object(main, 'current_user', side_effect=lambda _: {'user_id': self.owner}))
        self.sid = 'observation-session'
        asyncio.run(self.sessions.get_or_create_session(self.sid, owner_user_id=self.owner))
        self.claim = jobs.claim_job(self.sid, 'original-server-request', owner_user_id=self.owner,
                                    client_request_id='original-client-request', payload={})
        app = FastAPI()
        app.add_api_route('/jobs/{job_id}', main.get_job_status)
        app.add_api_route('/jobs/{job_id}/events', main.get_job_events)
        app.add_api_route('/jobs/{job_id}/operations/{operation_id}/result', main.get_operation_result)
        app.add_api_route('/sessions/{session_id}/jobs', main.list_session_jobs)
        app.add_api_route('/sessions/{session_id}/messages', main.session_messages)
        app.add_api_route('/sessions/{session_id}/tool-result/{tool_call_id}', main.tool_result_detail_endpoint)
        app.add_api_route('/sessions/{session_id}/task-by-request/{client_request_id}', main.discover_chat_job)
        self.client = self.enterContext(TestClient(app))

    def test_exact_request_discovery_by_owner_and_session(self):
        path = f'/sessions/{self.sid}/task-by-request/original-client-request'
        response = self.client.get(path)
        self.assertEqual(response.json(), {'job_id': self.claim.job_id})
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.client.get(path.replace(self.sid, 'other-session')).status_code, 404)
        self.assertEqual(self.client.get(path.replace('original-client-request', 'original-server-request')).status_code, 404)
        self.owner += 1
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(jobs.get_job(self.claim.job_id)['status'], 'running')

    def test_history_failure_still_discovers_tasks_and_signals_retry(self):
        with patch.object(self.sessions, 'get_display_history', side_effect=OSError('internal detail')):
            response = self.client.get(f'/sessions/{self.sid}/messages')
        self.assertEqual(response.status_code, 503, response.text)
        result = response.json()
        self.assertEqual(result['code'], 'history_unavailable')
        self.assertNotIn('messages', result)
        self.assertNotIn('internal detail', response.text)
        self.assertEqual([turn['job_id'] for turn in result['turns']], [self.claim.job_id])

    def test_historic_ids_are_aligned_even_with_a_synthetic_human_row(self):
        async def seed():
            for key, content in (('original-client-request', 'Question 1'), ('later-client', 'Question 2')):
                await self.sessions.save_messages(self.sid, [HumanMessage(content=content), AIMessage(content='Saved answer')],
                                                  idempotency_key='crid:' + key)
                if key == 'original-client-request':
                    with manager._connect_db(self.sessions.db_path) as conn:
                        conn.execute('INSERT INTO messages (session_id,msg_type,content) VALUES (?,?,?)',
                                     (self.sid, 'human', manager._SYNTH_FORCE_FINAL_MARK))
        asyncio.run(seed())
        msgs, row_ids, keys = asyncio.run(self.sessions.get_display_history(self.sid))
        self.assertEqual(len(msgs), len(row_ids))
        self.assertEqual(keys, ['original-client-request', '', 'later-client'])
        response = self.client.get(f'/sessions/{self.sid}/messages')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([turn['turn_request_id'] for turn in response.json()['turns']],
                         ['original-client-request', 'later-client'])

    def test_terminal_snapshot_and_event_cursor_share_a_view(self):
        job_id = self.claim.job_id
        initial = task_events.append_event(job_id, 'job', {'job_id': job_id, 'status': 'running'})
        page = self.client.get(f'/jobs/{job_id}/events').json()
        self.assertEqual(page['next_cursor'], initial['seq'])
        done = task_events.append_event(job_id, 'done', {'job_id': job_id, 'answer': 'finished',
                                                        'turn_envelope': {'display_status': 'completed'}})
        jobs.update_job(job_id, status='completed')
        status = self.client.get(f'/jobs/{job_id}')
        self.assertEqual(status.headers['Cache-Control'], 'no-store')
        self.assertEqual(status.json()['event_cursor'], done['seq'])
        self.assertEqual(status.json()['terminal_snapshot']['display_status'], 'completed')

    def test_live_tool_progress_uses_catalog_labels_and_opaque_correlation(self):
        private = 'project-birch /volume/research/worksheet.xlsx'
        call_id = 'call_birch'
        queue = task_events.DurableEventQueue(self.claim.job_id)
        progress_bus.set_request_queue(queue)
        progress_bus.set_request_journal([])
        self.addCleanup(progress_bus.clear_request_queue)
        self.addCleanup(progress_bus.clear_request_journal)
        progress_bus.emit_tool_call_start('deepseek_fetch', {'url': 'https://example.test?q=' + private},
                                          call_id, logical_key=private)
        artifact_url = f'/api/v2/session/{self.sid}/artifacts/{"a" * 32}'
        result = progress_bus.emit_tool_result('deepseek_fetch', private, call_id, summary=private,
            action_hint=private, logical_key=private, outcome={
                'status': 'complete', 'reason_code': 'project_birch_private',
                'facts': {'checked': 2, 'file': private, 'n': private},
                'result_refs': [{'kind': 'artifact', 'url': artifact_url, 'path': private},
                                {'kind': 'artifact', 'url': '/volume/' + private}],
            })
        progress_bus.emit_progress('tool_progress', {'tool_call_id': call_id, 'message': private})
        progress_bus.emit_progress('permission_request', {'approval_id': 'approval_birch',
            'tool_name': 'deepseek_fetch', 'status': 'pending', 'args': {'path': private}})
        progress_bus.emit_progress('browse_phase', {'phase': 'downloading', 'path': private})
        progress_bus.emit_progress('assistant_progress', {'text': private, 'tool_call_ids': [call_id]})
        frames = [queue.get_nowait() for _ in range(queue.qsize())]
        self.assertNotIn(private, json.dumps(frames))
        self.assertNotIn(private, json.dumps(progress_bus.get_request_journal()))
        self.assertEqual(result['summary'], private)  # internal readback record
        start, end, heartbeat, approval, browse, note = [json.loads(frame['data']) for frame in frames]
        for frame in (start, end):
            self.assertEqual(frame['tool_call_id'], call_id)
            self.assertEqual(frame['action_hint'], progress_bus.tool_display_name('deepseek_fetch'))
        self.assertEqual(start['logical_key'], end['logical_key'])
        self.assertNotEqual(start['logical_key'], private)
        self.assertEqual(end['facts'], {'checked': 2})
        self.assertEqual(end['reason_code'], '')
        self.assertEqual(end['result_refs'], [{'kind': 'artifact', 'url': artifact_url}])
        self.assertNotIn('summary', end)
        self.assertNotIn('message', heartbeat)
        self.assertNotIn('args', approval)
        self.assertEqual(browse, {'phase': 'downloading', 'seq': int(frames[4]['id'])})
        self.assertEqual(note['tool_call_ids'], [call_id])
        replay = self.client.get(f'/jobs/{self.claim.job_id}/events').json()
        self.assertEqual([row['data'] for row in replay['events']],
                         [json.loads(frame['data']) for frame in frames])

    def test_legacy_replay_and_status_keep_cursor_versions_links_and_private_readback(self):
        job_id = self.claim.job_id
        private = 'project-birch /volume/research/query.txt'
        private_update = 'new private instruction for project birch'
        task_events.submit_update(job_id, 0, private_update)
        jobs.update_job(job_id, progress_summary=private, result_path=private, error=private)
        operation, lease = task_events.start_operation(job_id, self.owner, 'deepseek_fetch',
                                                        {'query': private}, input_refs=[private])
        url = f'/api/v2/session/{self.sid}/artifacts/{"a" * 32}'
        task_events.finish_operation(operation, lease, ToolMessage(name='deepseek_fetch',
            tool_call_id='call_birch', content=private, artifact={
                '_tool_outcome': {'status': 'complete', 'coverage': private},
                'result_refs': [{'kind': 'artifact', 'url': url, 'label': private}],
                'facts': {'query': private, 'total': 7},
            }))
        with task_events.transaction() as conn:
            last = conn.execute('SELECT MAX(seq) FROM task_events WHERE job_id=?', (job_id,)).fetchone()[0]
            for offset, (name, payload) in enumerate([
                ('task_update', {'instruction': private_update, 'version': 1, 'status': 'pending'}),
                ('tool_result', {'summary': private, 'action_hint': private, 'logical_key': private,
                                 'tool_call_id': 'call_birch', 'status': 'complete'}),
                ('task_state', {'schema_version': 1, 'task_contract': {'query': private, 'notes': [private]}}),
            ], 1):
                conn.execute('INSERT INTO task_events VALUES (?,?,?,?,?)',
                             (job_id, last + offset, name, json.dumps(payload), 0.0))
        status_response = self.client.get(f'/jobs/{job_id}')
        self.assertNotIn(private, status_response.text)
        self.assertNotIn(private_update, status_response.text)
        status = status_response.json()
        self.assertEqual(status['event_cursor'], last + 3)
        self.assertEqual(status['task_version'], 1)
        self.assertEqual(status['pending_updates'][0]['version'], 1)
        self.assertNotIn('input_refs', status['operations'][0])
        self.assertEqual(status['operations'][0]['result_refs'], [{'kind': 'artifact', 'url': url}])
        self.assertEqual(status['operations'][0]['outcome']['facts'], {'total': 7})
        readback_url = status['operations'][0]['result_url']
        self.assertEqual(readback_url, f'/jobs/{job_id}/operations/{operation}/result')
        self.assertEqual(self.client.get(readback_url).json()['result']['content'], private)
        self.assertNotIn(private, self.client.get(f'/sessions/{self.sid}/jobs').text)
        self.assertEqual(task_state.current(self.owner, self.sid, job_id)['state']['task_contract']['notes'], [private])
        self.assertEqual(task_events.updates_after(job_id, 0)[0]['instruction'], private_update)
        task_events.acknowledge_updates(job_id, 1)
        self.assertEqual(task_events.updates_after(job_id, 0)[0]['instruction'], private_update)
        done = task_events.append_event(job_id, 'done', {'answer': 'authorized final answer',
            'turn_envelope': {'display_status': 'completed', 'artifact_ids': ['a' * 32],
                              'notices': [private], 'task_snapshot': {'task_contract': {'query': private}}}})
        page_response = self.client.get(f'/jobs/{job_id}/events?after={last}&limit=2')
        self.assertNotIn(private, page_response.text)
        self.assertNotIn(private_update, page_response.text)
        page = page_response.json()
        self.assertEqual(page['next_cursor'], last + 2)
        self.assertTrue(page['has_more'])
        self.assertEqual([item['data']['seq'] for item in page['events']], [last + 1, last + 2])
        end_response = self.client.get(f'/jobs/{job_id}/events?after={page["next_cursor"]}')
        self.assertNotIn(private, end_response.text)
        end = end_response.json()
        self.assertEqual(end['next_cursor'], done['seq'])
        self.assertEqual(end['events'][0]['data'], {'schema_version': 1, 'seq': last + 3})
        self.assertEqual(end['events'][-1]['data']['answer'], 'authorized final answer')
        self.assertEqual(end['terminal_snapshot']['artifact_ids'], ['a' * 32])
        self.assertEqual(end['terminal_snapshot']['terminal_seq'], done['seq'])
        recovery = task_events.append_event(job_id, 'agent_recovery', {
            'job_id': job_id, 'status': 'admitted', 'previous_terminal_seq': done['seq'],
            'reactivated_updates': [{'version': 1, 'instruction': private_update}],
        })
        self.assertEqual(recovery['data']['reactivated_updates'], [{'version': 1}])
        recovery_response = self.client.get(f'/jobs/{job_id}/events?after={done["seq"]}')
        self.assertNotIn(private_update, recovery_response.text)
        self.assertEqual(recovery_response.json()['recovery_cursor'], recovery['seq'])
        self.assertEqual(recovery_response.json()['next_cursor'], recovery['seq'])
        self.owner += 1
        self.assertEqual(self.client.get(readback_url).status_code, 404)

    def test_saved_history_omits_working_text_and_tool_details_but_keeps_chat(self):
        private = 'project-birch /volume/research/raw-input.csv'
        call = {'name': 'deepseek_fetch', 'id': 'call_birch', 'args': {'query': private}}
        msgs = [HumanMessage(content='authorized user question'),
                AIMessage(content='Working on ' + private, tool_calls=[call]),
                ToolMessage(content=private, name=call['name'], tool_call_id=call['id'],
                    artifact={'_citrus_execution': {'status': 'unknown', 'summary': private,
                        'action_hint': private, 'reason_code': 'timeout', 'elapsed_ms': 12}}),
                AIMessage(content='authorized final answer')]
        asyncio.run(self.sessions.save_messages(self.sid, msgs, idempotency_key='crid:original-client-request'))
        response = self.client.get(f'/sessions/{self.sid}/messages')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(private, response.text)
        turn, = response.json()['turns']
        self.assertEqual(turn['user']['content'], 'authorized user question')
        self.assertEqual(turn['answer']['content'], 'authorized final answer')
        record = next(entry for entry in turn['trace'] if entry.get('tool_call_id') == call['id'])
        self.assertEqual(record['name'], progress_bus.tool_display_name(call['name']))
        self.assertNotIn('args', record)
        path = f'/sessions/{self.sid}/tool-result/{call["id"]}'
        with patch.object(tool_results, 'fetch', return_value={'session_id': self.sid,
                'tool_call_id': call['id'], 'args_json': json.dumps(call['args']), 'result_text': private}):
            self.assertEqual(self.client.get(path).json()['result_text'], private)
            self.owner += 1
            self.assertEqual(self.client.get(path).status_code, 404)

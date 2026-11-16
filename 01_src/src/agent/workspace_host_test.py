"""Workspace CAS rejection remains a useful observation in the production host."""
import asyncio
import json
from langchain_core.messages import AIMessage
from src.agent.host_test import environment, install, action
from src.agent import host
from src.core import task_events, tool_results


def test_known_cas_rejection_can_be_corrected_without_unknown_write(environment, monkeypatch, tmp_path):
    from src.session import files
    from src.agent.workspace_storage import WorkspaceStorage
    monkeypatch.setattr(files, 'DB_PATH', tmp_path/'files.sqlite3')
    monkeypatch.setattr(files, 'FILES_ROOT', tmp_path/'files')
    state, job = environment
    def after_conflict(messages):
        observation = next(m for m in messages if getattr(m, 'tool_call_id', '') == 'stale')
        payload = json.loads(observation.content)
        assert payload['write_applied'] is False
        assert payload['_tool_outcome']['status'] == 'not_run'
        ref = payload['latest_ref']
        return action('workspace_edit', cid='edit', path=ref['path'], old_string='original',
                      new_string='corrected', expected_version=ref['version'], expected_sha256=ref['sha256'])
    install(monkeypatch, [
        action('workspace_write', cid='create', path='/workspace/note.txt', content='original', expected_version=0, expected_sha256=''),
        action('workspace_write', cid='stale', path='/workspace/note.txt', content='must not overwrite', expected_version=0, expected_sha256=''),
        after_conflict, AIMessage(content='Saved corrected version.')])
    result = asyncio.run(host.general_agent_node(state))
    assert result['turn_outcome']['status'] == 'completed'
    assert [row['status'] for row in result['tool_status']] == ['complete', 'not_run', 'complete']
    store = WorkspaceStorage(17, 'host-session')
    assert store.read('/workspace/note.txt', version=1)['text'] == 'original'
    assert store.read('/workspace/note.txt')['text'] == 'corrected'
    assert len(task_events.projection(job)['operations']) == 3
    assert all(tool_results.storage_integrity(tool_results.fetch(cid))['complete'] for cid in ('create','stale','edit'))

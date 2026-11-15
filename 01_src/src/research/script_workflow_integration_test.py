"""Real attachment identity/typed assets, temporary stores, synthetic bytes only."""
import base64
import contextvars
import hashlib
import json

import pandas as pd
import pytest

from src.research.prepared_dataset_assets_test import temporary_stores, OWNER, SID, prepared  # noqa: F401
from src.core import tracing
from src.research.context import set_request_tables, reset_request_tables
from src.figures.projects import normalize_script_bindings, snapshot_script_inputs, ProjectError
from src.session import files


@pytest.fixture(autouse=True)
def request_identity(monkeypatch):
    for name, default in (('_user_id', OWNER), ('_session_id', SID), ('_client_request_id', 'synthetic-turn')):
        monkeypatch.setattr(tracing, name, contextvars.ContextVar(name, default=default))


def test_source_identity_stays_on_its_bytes_and_filename_resolves_only_this_request():
    from src.api.main import _prepare_chat_attachments
    from src.research.tools import inspect_uploaded_table
    data = [b'A,B\n1,2\n', b'X,Y\n3,4\n']
    entries = [files.register_file(session_id=SID, owner_user_id=OWNER, filename=f'synthetic_{i}.csv', content=blob) for i, blob in enumerate(data)]
    attachments = [{'name': entry['filename'], 'file_id': entry['file_id'], 'data': base64.b64encode(blob).decode()}
                   for entry, blob in zip(entries, data)]
    refs = [{key: entry[key] for key in ('file_id', 'filename', 'sha256')} for entry in entries]
    _, _, tables = _prepare_chat_attachments(list(reversed(attachments)), refs)
    assert [t['source_ref']['file_id'] for t in tables] == [entries[1]['file_id'], entries[0]['file_id']]
    token = set_request_tables(tables)
    try:
        profile = json.loads(inspect_uploaded_table.func(file_index=0))
        assert profile['inputs_binding']['data']['file_id'] == entries[1]['file_id']
        bindings = normalize_script_bindings({'data': {'file_id': 'synthetic_1.csv'}})
        snapshots, _ = snapshot_script_inputs(bindings, OWNER, SID)
        assert snapshots[0]['sha256'] == hashlib.sha256(data[1]).hexdigest()
        assert files.get_account_file(snapshots[0]['file_id'], OWNER) is None
        assert files.get_account_file(snapshots[0]['file_id'], OWNER, allow_internal=True)
        with pytest.raises(ProjectError):
            normalize_script_bindings({'data': {'file_id': 'outside.csv'}})
    finally:
        reset_request_tables(token)
    _, _, untrusted = _prepare_chat_attachments(attachments)
    assert all(not t['source_ref'] for t in untrusted)


def test_ambiguous_names_paths_and_internal_file_ids_do_not_authorize_reads():
    first = files.register_file(session_id=SID, owner_user_id=OWNER, filename='same.csv', content=b'A\n1\n')
    second = files.register_file(session_id=SID, owner_user_id=OWNER, filename='same.csv', content=b'A\n2\n')
    token = set_request_tables([{'name': 'same.csv', 'source_ref': first}, {'name': 'same.csv', 'source_ref': second}])
    try:
        for raw in ({'file_id': 'same.csv'}, {'file_id': '../same.csv'}, {'path': 'same.csv'}):
            with pytest.raises(ProjectError):
                normalize_script_bindings({'data': raw})
    finally:
        reset_request_tables(token)
    asset = prepared(pd.DataFrame({'value': pd.Series([2**53+1], dtype='Int64')}))
    with pytest.raises(ProjectError):
        snapshot_script_inputs({'data': {'file_id': asset.payload_file_ref}}, OWNER, SID)
    snapshots, refs = snapshot_script_inputs({'data': {'dataset_ref': asset.ref}}, OWNER, SID)
    assert snapshots[0]['dataset_ref'] == asset.ref and asset.ref in refs
    with pytest.raises(Exception):
        snapshot_script_inputs({'data': {'dataset_ref': asset.ref}}, OWNER+1, SID)


def test_saved_project_can_verify_private_snapshots_without_publicly_exposing_them():
    from src.figures.projects import _verify_sources
    entry = files.register_file(session_id=SID, owner_user_id=OWNER, filename='synthetic.csv', content=b'A\n1\n', visibility='internal')
    project = {'source_json': {}, 'data_refs_json': [{'file_id': entry['file_id'], 'sha256': entry['sha256']}]}
    assert files.get_account_file(entry['file_id'], OWNER) is None
    _verify_sources(project, OWNER)
    with pytest.raises(ProjectError, match='不可用'):
        _verify_sources(project, OWNER + 1)
    files.get_account_file(entry['file_id'], OWNER, allow_internal=True)['path'].write_bytes(b'A\n2\n')
    with pytest.raises(ProjectError, match='内容已改变'):
        _verify_sources(project, OWNER)


def test_semantic_question_revalidates_inputs_even_without_field_options():
    from src.core.user_questions import _validate_context_inputs
    entry = files.register_file(session_id=SID, owner_user_id=OWNER, filename='synthetic.csv', content=b'A\n1\n')
    context = {'attachment_refs': [{'file_id': entry['file_id'], 'sha256': entry['sha256']}],
               'context_working_state': {'dataset_refs': [prepared().ref]}}
    _validate_context_inputs(OWNER, context)
    files.get_account_file(entry['file_id'], OWNER, allow_internal=True)['path'].write_bytes(b'A\n2\n')
    with pytest.raises(ValueError, match='source_version_changed'):
        _validate_context_inputs(OWNER, context)
    with pytest.raises(ValueError):
        _validate_context_inputs(OWNER + 1, context)


def test_web_ban_is_not_a_positive_local_search_requirement():
    from src.graph.retrieval_router import parse_request_constraints
    for scope in ('auto', 'local'):
        result = parse_request_constraints('请整理原始数据并绘图；不要联网检索。', scope, has_table_attachment=True)
        assert not result.required_tool_groups
        assert result.requested_mode == 'local'
        from src.graph.retrieval_router import decide_retrieval, required_route_groups
        assert not required_route_groups(decide_retrieval('请整理原始数据并绘图；不要联网检索。', scope))
    explicit = parse_request_constraints('只查本地，不要联网检索。')
    assert explicit.required_tool_groups and explicit.source_policy == 'required'


def test_plotting_command_requires_a_figure_but_discussion_and_negation_do_not():
    from src.graph.export_contract import deliverable_contract
    contract = deliverable_contract('请整理原始数据并绘图')
    assert contract and contract[0]['kinds'] == ['figure']
    assert deliverable_contract('不要绘图') == []
    assert deliverable_contract('如何绘图？') == []
    from src.graph.request_plan import build_request_plan, RequestFacts
    child_query = '请整理原始数据并绘图；不要联网检索。\n用户对澄清问题的答复：B、C 分面，并单独绘制 D'
    plan = build_request_plan(child_query, RequestFacts(attachments=1, visualization=True, research_tools=True))
    assert all('local_hybrid_search' not in group and 'audit_uploaded_data' not in group for group in plan.required_tool_groups)
    assert len(plan.deliverables) == 1 and plan.deliverables[0]['kinds'] == ['figure']


def test_wsl_saved_script_is_editable_without_enabling_local_fallback(monkeypatch):
    from src.figures.projects import _can_edit
    from src.config import settings
    monkeypatch.setattr(settings, 'FIGURES_SANDBOX_BACKEND', 'wsl-docker')
    monkeypatch.setattr(settings, 'FIGURES_SANDBOX_IMAGE', 'synthetic:test')
    monkeypatch.setattr(settings, 'FIGURES_SANDBOX_ALLOW_LOCAL', False)
    assert _can_edit({'source_kind': 'script', 'source_json': {'version': 3, 'script': 'synthetic', 'input_snapshots': []}})
    assert not _can_edit({'source_kind': 'script', 'source_json': {'script': 'synthetic', 'inputs': {'data': 'unfixed'}}})


def test_failed_qa_keeps_exports_but_prevents_complete_publication(monkeypatch):
    from types import SimpleNamespace
    from src.research.script_execution import publish_script_outputs
    from src.research import figure_tools as tools
    from src.figures import projects
    from src.figures.script_outputs import normalize_output_contract
    from src.figures.test_script_outputs_v2 import receipt_for
    from src.figures.spec import parse_qa_profile
    profile = parse_qa_profile({})
    contract = normalize_output_contract({'version': 2, 'tables': [], 'figures': [{'name': 'f', 'formats': ['png'], 'parents': []}]})
    qa = {'version': 1, 'profile': profile, 'status': 'needs_attention', 'checks': {'caption': {'status': 'fail'}, 'scientific_claims': {'status': 'unsupported'}}}
    from src.figures.nature_receipts_test import receipt as synthetic_nature_receipt
    nature_manifest, _ = synthetic_nature_receipt()
    class Runner:
        def run(self, job):
            payloads = {'figure:f:png': b'\x89PNG\r\n\x1a\nsynthetic', 'figure:f:manifest': json.dumps({**nature_manifest, 'objects': [], 'qa_receipt': qa}).encode()}
            payloads['result'] = json.dumps(receipt_for(job, payloads)).encode()
            return SimpleNamespace(ok=True, outputs=payloads, backend='fake', duration_ms=1)
    monkeypatch.setattr(projects, 'script_replay_inputs', lambda *a: ({}, {}, {}))
    monkeypatch.setattr(tools, '_publish', lambda *a: {'artifact_url': '/synthetic-artifact'})
    monkeypatch.setattr(tools, '_record_project', lambda **kw: {'figure_id': 'synthetic-project'})
    _, result = publish_script_outputs(runner=Runner(), text='', title='synthetic', output_name='', contract=contract,
        source={'input_snapshots': []}, data_refs=[], profile=profile, settings=SimpleNamespace())
    assert result['_tool_outcome']['status'] == 'partial'
    assert result['exports'] and result['figures'][0]['editable']
    assert result['figures'][0]['failures'] == [{'reason_code': 'figure_qa_needs_attention', 'checks': ['caption']}]


def test_missing_manifest_preserves_worker_layout_diagnostic(monkeypatch):
    from types import SimpleNamespace
    from src.research.script_execution import publish_script_outputs
    from src.research import figure_tools as tools
    from src.figures import projects
    from src.figures.script_outputs import normalize_output_contract
    from src.figures.test_script_outputs_v2 import receipt_for
    from src.figures.spec import parse_qa_profile
    contract = normalize_output_contract({'version': 2, 'figures': [
        {'name': 'f', 'formats': ['png'], 'parents': []}]})
    diagnostic = 'Full tick labels overlap or exceed the fixed canvas: axes_0.ytick_39'
    class Runner:
        def run(self, job):
            receipt = receipt_for(job, {})
            receipt['status'] = 'partial'
            receipt['figure_count'] = 1
            for slot in receipt['slots'].values():
                slot.update(status='error', error=diagnostic)
            return SimpleNamespace(ok=True, outputs={'result': json.dumps(receipt).encode()},
                                   backend='fake', duration_ms=1)
    monkeypatch.setattr(projects, 'script_replay_inputs', lambda *a: ({}, {}, {}))
    monkeypatch.setattr(tools, '_publish', lambda *a: pytest.fail('No output to publish'))
    _, result = publish_script_outputs(runner=Runner(), text='', title='synthetic', output_name='',
        contract=contract, source={'input_snapshots': []}, data_refs=[],
        profile=parse_qa_profile({}), settings=SimpleNamespace())
    assert result['_tool_outcome']['status'] == 'failed'
    assert diagnostic in result['figures'][0]['error']
    assert not result['exports']


def test_multiple_dataset_receipts_survive_working_state_projection():
    from langchain_core.messages import ToolMessage
    from src.graph.general_agent_graph import _capture_interaction_refs, _task_working_state
    refs = [prepared().ref, prepared(pd.DataFrame({'other': [3]})).ref]
    state = {}
    _capture_interaction_refs(state, [ToolMessage(name='run_research_figure_script', tool_call_id='script', content='{}', artifact={'dataset_refs': refs})])
    assert _task_working_state(state, 'synthetic plot', [], [])['dataset_refs'] == refs

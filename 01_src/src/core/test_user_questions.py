"""Offline clarification protocol: no providers, user files or live services."""
import asyncio
import contextvars
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from src.core import jobs, task_events as events, tracing, user_questions as questions
from src.api.chat_idempotency_test import runtime, _post, _drain  # noqa: F401


_REAL_VALIDATE_CONTEXT_INPUTS = questions._validate_context_inputs
_REAL_PIN_INPUTS = questions._pin_inputs
_REAL_RELEASE_PIN = questions._release_pin


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "tasks.sqlite3")
    for name, default in (("_job_id", ""), ("_user_id", None), ("_session_id", "")):
        monkeypatch.setattr(tracing, name, contextvars.ContextVar(name, default=default))
    monkeypatch.setattr(questions, "_pin_inputs", lambda *a, **kw: None)
    monkeypatch.setattr(questions, "_validate_context_inputs", lambda *a, **kw: None)
    monkeypatch.setattr(questions, "_release_pin", lambda *a, **kw: None)


def setup_question(**kwargs):
    claim = jobs.claim_job("s", "r", owner_user_id=11)
    tracing.set_job_id(claim.job_id)
    tracing.set_user_id(11)
    tracing.set_session_id("s")
    events.init_budget(claim.job_id, time.time() + 100)
    question = questions.create_question(claim.job_id, owner_user_id=11, session_id="s",
        prompt="请选择研究目的", options=[{"id": "compare", "label": "比较处理差异", "description": "", "refs": []}],
        context={"query": "分析现有数据", "context_working_state": {"dataset_refs": [{"id": "real"}]}}, **kwargs)
    jobs.update_job(claim.job_id, status="awaiting_input")
    events.append_event(claim.job_id, "done", {"turn_envelope": {"display_status": "awaiting_input"}})
    return claim.job_id, question


def answer(job, question, **kw):
    defaults = dict(owner_user_id=11, session_id="s", version=question["version"],
        selected_option_ids=["compare"], client_request_id="answer")
    defaults.update(kw)
    return questions.answer_question(job, question["id"], **defaults)


def test_concurrent_same_answer_one_child_and_closed_old_done():
    job, q = setup_question()
    before = events.replay(job)
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda n: answer(job, q, client_request_id="retry" + str(n)), range(4)))
    assert len({r["child_job_id"] for r in replies}) == 1
    assert sum(r["created"] for r in replies) == 1
    assert all(r["dispatch_required"] for r in replies)
    assert events.replay(job) == before
    assert not jobs.update_job(job, status="completed")
    assert jobs.get_job(job)["status"] == "awaiting_input"
    assert not events.append_event(job, "text", {"text": "late"})["event"] == "text"


def test_conflicting_answer_version_owner_and_session():
    job, q = setup_question()
    with pytest.raises(questions.QuestionConflict, match="not_found"):
        answer(job, q, owner_user_id=12)
    with pytest.raises(questions.QuestionConflict, match="not_found"):
        answer(job, q, session_id="other")
    with pytest.raises(questions.QuestionConflict, match="version"):
        answer(job, q, version=2)
    answer(job, q)
    with pytest.raises(questions.QuestionConflict, match="answer_conflict"):
        answer(job, q, selected_option_ids=[], text="another purpose")


def test_answer_validation_and_cancel():
    job, q = setup_question(allow_text=False)
    for values in ({"selected_option_ids": ["invented"]}, {"selected_option_ids": [], "text": "custom"},
                   {"selected_option_ids": ["compare", "compare"]}, {"cancelled": True}):
        with pytest.raises(ValueError):
            answer(job, q, **values)
    result = answer(job, q, selected_option_ids=[], cancelled=True)
    assert not result["child_job_id"] and not result["dispatch_required"]
    assert result["question"]["status"] == "cancelled"
    assert answer(job, q, selected_option_ids=[], cancelled=True)["child_job_id"] == ""


def test_new_segment_deadline_preserves_cumulative_budget_and_attempts():
    job, q = setup_question()
    # Pretend one physical attempt completed in the closed segment.
    with events.transaction() as conn:
        conn.execute("UPDATE task_budgets SET calls=1,tokens=1000 WHERE job_id=?", (job,))
        conn.execute("INSERT INTO task_model_attempts VALUES ('attempt',?,'supervisor','complete',1000,?,'',?)",
            (job, json.dumps({"input_tokens": 900, "output_tokens": 100}), time.time()))
    child = answer(job, q)["child_job_id"]
    payload = questions.get_continuation(child, owner_user_id=11, session_id="s")
    assert payload["no_replay"] and payload["parent_job_id"] == job
    assert payload["context_working_state"]["dataset_refs"] == [{"id": "real"}]
    assert "比较处理差异" in payload["query"]
    assert q['prompt'] not in payload['query']
    assert payload['question_answer']['question']['prompt'] == q['prompt']
    deadline = time.time() + 200
    assert questions.claim_child_dispatch(child, owner_user_id=11, session_id="s", deadline=deadline)
    assert not questions.claim_child_dispatch(child, owner_user_id=11, session_id="s", deadline=deadline)
    budget = events.projection(child)["budget"]
    assert budget["calls"] == 1 and budget["tokens"] == 1000
    assert budget["deadline"] == deadline and budget["max_tokens"] == 250000
    assert budget["reported_attempts"] == 1
    events.init_budget(child, time.time() + 10)
    assert events.projection(child)["budget"]["calls"] == 1
    assert questions.release_child_dispatch(child, owner_user_id=11, session_id="s")
    assert answer(job, q)["dispatch_required"]
    assert questions.claim_child_dispatch(child, owner_user_id=11, session_id="s", deadline=deadline)
    events.append_event(child, "status", {})
    assert not questions.release_child_dispatch(child, owner_user_id=11, session_id="s")
    assert not answer(job, q)["dispatch_required"]


def test_expiry_releases_pins_and_rejects_answer(monkeypatch):
    job, q = setup_question()
    released = []
    with events.transaction() as conn:
        conn.execute("UPDATE task_questions SET context=? WHERE id=?", (json.dumps({"source_refs": [{"file_id": "file"}]}), q["id"]))
    monkeypatch.setattr(questions, "_release_pin", lambda identity, owner, context=None, **kw: released.append((identity, owner)))
    assert questions.expire_questions(q["expires_at"] + 1) == 1
    assert released == [(q["id"], 11)]
    with pytest.raises(questions.QuestionConflict, match="expired"):
        answer(job, q)
    assert jobs.get_job(job)["status"] == "awaiting_input"


def test_new_chat_replaces_only_older_unanswered_choices(monkeypatch):
    job, q = setup_question()
    before = events.replay(job)
    new_job = jobs.claim_job('s', 'new-request', owner_user_id=11).job_id
    releases = []
    monkeypatch.setattr(questions, '_release_pin', lambda identity, owner, context=None, **kw: releases.append((identity, owner)))
    with pytest.raises(questions.QuestionConflict, match='not_found'):
        questions.supersede_pending_questions(new_job, owner_user_id=12, session_id='s')
    other = jobs.claim_job('other', 'other-request', owner_user_id=11).job_id
    assert questions.supersede_pending_questions(other, owner_user_id=11, session_id='other') == 0
    assert questions.supersede_pending_questions(new_job, owner_user_id=11, session_id='s') == 1
    assert releases == [(q['id'], 11)]
    assert events.replay(job) == before
    assert questions.list_questions(job, owner_user_id=11, session_id='s')[0]['status'] == 'expired'
    with pytest.raises(questions.QuestionConflict, match='expired'):
        answer(job, q)
    assert questions.supersede_pending_questions(new_job, owner_user_id=11, session_id='s') == 0


def test_continuation_and_accepted_answers_are_not_superseded():
    job, q = setup_question()
    child = answer(job, q)['child_job_id']
    assert questions.supersede_pending_questions(child, owner_user_id=11, session_id='s') == 0
    new_job = jobs.claim_job('s', 'new-request', owner_user_id=11).job_id
    assert questions.supersede_pending_questions(new_job, owner_user_id=11, session_id='s') == 0
    assert answer(job, q)['child_job_id'] == child


def test_strict_schema_and_real_field_validation(monkeypatch):
    from types import SimpleNamespace
    from src.research import analysis_results
    claim = jobs.claim_job("s", "r", owner_user_id=11)
    dataset = {"kind": "dataset_snapshot", "id": "snapshot", "version": 1, "sha256": "hash"}
    refs = [{"kind": "dataset_field", "dataset_ref": dataset, "column": "real_column",
             "file_id": None, "sha256": None, "sheet": None}]
    options = [{"id": "a", "label": "a", "description": "", "refs": refs}]
    monkeypatch.setattr(analysis_results, "load_dataset_snapshot", lambda owner, ref:
        SimpleNamespace(to_dict=lambda: {"columns": ["real_column"]}))
    questions.create_question(claim.job_id, owner_user_id=11, session_id="s", prompt="x", options=options)
    refs[0]["column"] = "made_up"
    with pytest.raises(ValueError, match="field_not_in_dataset"):
        questions.create_question(claim.job_id, owner_user_id=11, session_id="s", prompt="x", options=options)
    with pytest.raises(ValidationError):
        questions.UserQuestionInput.model_validate(dict(prompt="x", options=[], multi_select=False, allow_text=True, owner_user_id=11))
    with pytest.raises(ValidationError):
        questions.QuestionAnswer.model_validate(dict(version="1", client_request_id="key"))


def test_interaction_is_real_strict_tool_even_when_research_disabled():
    from src.tools.interaction import request_user_input, provider_tools
    from src.graph.tool_availability import Env, available_tool_names
    assert "request_user_input" in available_tool_names(Env(research_tools=False))
    schema = provider_tools([request_user_input])[0]["function"]
    assert schema["strict"] and schema["parameters"]["additionalProperties"] is False
    assert "owner_user_id" not in schema["parameters"]["properties"]
    from src.core.task_result import build_task_result
    result = build_task_result([{"name": "request_user_input", "status": "complete"}],
        ["later"], requested_status="awaiting_input")
    assert result.acceptance_status == "pending" and result.executed_steps == 0


def test_same_batch_question_prevents_all_dependent_execution(monkeypatch):
    import src.graph.general_agent_graph as graph
    from src.tools import registry
    executed = []
    async def fake(tool, call):
        executed.append(call["name"])
        return ToolMessage(content="ok", name=call["name"], tool_call_id=call["id"], artifact={})
    monkeypatch.setattr(registry, "_run_single_tool", fake)
    monkeypatch.setattr(graph, "_emit_completed_tool_step", lambda *a: None)
    response = AIMessage(content="", tool_calls=[
        {"id": "write", "name": "run_research_figure_script", "args": {}},
        {"id": "question", "name": "request_user_input", "args": {}}])
    result = asyncio.run(graph._execute_calls(response,
        tools_by_name={n: object() for n in ("run_research_figure_script", "request_user_input")},
        deadline=time.monotonic() + 100, seen_call_signatures={}))
    assert executed == ["request_user_input"]
    assert len(result) == 2 and result[0].artifact["_tool_outcome"]["status"] == "not_run"


def test_graph_native_question_pauses_without_reflection_synthesis_or_export(monkeypatch):
    import src.core.llm_pool as pool
    import src.graph.general_agent_graph as graph
    from src.tools.interaction import request_user_input
    from src.tools import registry
    job = jobs.claim_job("s", "r", owner_user_id=11).job_id
    tracing.set_job_id(job)
    tracing.set_session_id("s")
    tracing.set_user_id(11)
    events.init_budget(job, time.time()+200)
    class Model:
        def bind_tools(self, tools):
            return self
    calls = []
    async def model(*args, **kwargs):
        calls.append(kwargs.get("purpose", "supervisor"))
        assert len(calls) == 1
        return AIMessage(content="", tool_calls=[{"id": "q", "name": "request_user_input", "args": {
            "prompt": "请选择目的", "options": [{"id": "compare", "label": "比较", "description": "", "refs": []}],
            "multi_select": False, "allow_text": True}}])
    async def forbidden(*a, **kw):
        pytest.fail("No reflection or delivery after durable question")
    monkeypatch.setattr(pool, "get_llm", lambda **kw: Model())
    monkeypatch.setattr(graph, "_call_llm", model)
    monkeypatch.setattr(graph, "_scoped_toolset", lambda *a, **kw: ([request_user_input], []))
    monkeypatch.setattr(graph, "_reflect_on_progress", forbidden)
    monkeypatch.setattr(graph, "_force_deliverable_export", forbidden)
    monkeypatch.setattr(registry, "_check_tool_sandbox", lambda *a, **kw: asyncio.sleep(0, result=""))
    result = asyncio.run(graph.general_agent_node({"query": "生成图并导出报告", "session_id": "s", "request_id": "r", "job_id": job, "messages": []}))
    assert result["awaiting_input"] and result["pending_question"]["status"] == "pending"
    assert result["turn_envelope"]["acceptance_status"] == "pending"
    assert result["turn_envelope"]["display_status"] == "awaiting_input"
    assert result["export_gate"]["status"] == "pending"
    assert result["_trace"]["task_result"]["executed_steps"] == 0
    assert len(calls) == 1
    assert events.replay(job)["events"][-1]["event"] == "operation"
    assert questions.list_questions(job, owner_user_id=11, session_id="s")[0]["id"] == result["pending_question"]["id"]


def test_working_state_preserves_internal_refs_and_single_failure_notice():
    import src.graph.general_agent_graph as graph
    from src.core.turn_notices import notice, INCOMPLETE_PREFIX
    state = {"context_working_state": {"plot_intent": {"goal": "compare"}, "dataset_refs": [{"id": "ds"}]}}
    graph._capture_interaction_refs(state, [ToolMessage(name="inspect_uploaded_table", tool_call_id="i",
        content=json.dumps({"source_refs": [{"file_id": "f", "sha256": "h", "sheet": "S", "columns": ["A"]}]}))])
    working = graph._task_working_state(state, "goal", [], [])
    assert working["plot_intent"]["goal"] == "compare"
    assert working["dataset_refs"] == [{"id": "ds"}]
    assert working["source_refs"][0]["columns"] == ["A"]
    answer = graph._refresh_required_step_notices(notice("tool_timeout") + "\n\n[已验证图](url)", missing_count=1, failed_count=1)
    assert answer.count(INCOMPLETE_PREFIX) == 1
    assert "超时" in answer and "已验证图" in answer


@pytest.fixture
def input_stores(tmp_path, monkeypatch):
    from src import auth, config
    from src.core import account_leases
    from src.session import files
    monkeypatch.setattr(config, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(files, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(files, 'DB_PATH', tmp_path / 'files.sqlite3')
    monkeypatch.setattr(files, 'FILES_ROOT', tmp_path / 'files')
    monkeypatch.setattr(files, 'migrate_legacy_files_for_account', lambda owner: 0)
    monkeypatch.setattr(auth, 'user_by_id', lambda owner: {'id': owner, 'active': True})
    monkeypatch.setattr(account_leases, '_states', {})
    monkeypatch.setattr(questions, '_validate_context_inputs', _REAL_VALIDATE_CONTEXT_INPUTS)
    monkeypatch.setattr(questions, '_pin_inputs', _REAL_PIN_INPUTS)
    monkeypatch.setattr(questions, '_release_pin', _REAL_RELEASE_PIN)
    files.init_store()
    return files


@pytest.mark.parametrize('visibility', ['public', 'internal'])
@pytest.mark.parametrize('location', ['attachment_refs', 'source_refs', 'working_sources'])
def test_host_context_sources_require_owner_hash_and_actual_bytes(input_stores, visibility, location):
    files = input_stores
    entry = files.register_file(session_id='question_inputs', owner_user_id=11, filename='synthetic.csv',
        content=b'A\n1\n', visibility=visibility)
    ref = {'file_id': entry['file_id'], 'sha256': entry['sha256']}
    context = ({'context_working_state': {'source_refs': [ref]}} if location == 'working_sources'
               else {location: [ref]})
    questions._validate_context_inputs(11, context)
    if visibility == 'internal':
        assert files.get_account_file(entry['file_id'], 11) is None
    with pytest.raises(ValueError, match='source_version_changed'):
        questions._validate_context_inputs(12, context)
    ref['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='source_version_changed'):
        questions._validate_context_inputs(11, context)
    ref['sha256'] = entry['sha256']
    path = files.get_account_file(entry['file_id'], 11, allow_internal=True)['path']
    path.write_bytes(b'A\n2\n')
    with pytest.raises(ValueError, match='source_version_changed'):
        questions._validate_context_inputs(11, context)
    path.unlink()
    with pytest.raises(ValueError, match='source_version_changed|source_unavailable'):
        questions._validate_context_inputs(11, context)


@pytest.mark.parametrize('traces, expected', [
    ([{'task_result': {'missing_requirements': []}, 'required_tools_unattempted': ['stale']}], []),
    ([{'task_result': {'missing_requirements': ()}, 'required_tools_unattempted': ['stale']}], []),
    ([{'task_result': {'missing_requirements': ['current']}, 'required_tools_unattempted': ['stale']}], ['current']),
    ([{'required_tools_unattempted': ['stale']}, {'task_result': {'missing_requirements': []}}], []),
    ([{'task_result': {'missing_requirements': []}}, {'task_result': {'missing_requirements': ['later']}}], []),
    ([{'task_result': {}, 'required_tools_unattempted': ['legacy']}], ['legacy']),
    ([{'task_result': {'missing_requirements': None}, 'required_tools_unattempted': ['legacy']}], ['legacy']),
])
def test_terminal_missing_requirements_preserve_authoritative_empty(traces, expected):
    from src.api.main import _terminal_missing_groups
    assert list(_terminal_missing_groups(*traces)) == expected


@pytest.mark.parametrize('answer_text, result_location', [
    ('Completed synthetic analysis', 'supervisor'), ('', 'supervisor'), ('', 'session_save')])
@pytest.mark.parametrize('missing', [[], ()])
def test_api_terminal_does_not_restore_stale_missing_tools(runtime, answer_text, result_location, missing):
    rt = runtime
    trace = {'task_result': {'status': 'completed', 'missing_requirements': missing}}

    class Graph:
        async def astream(self, *args, **kwargs):
            supervisor = {'required_tools_unattempted': ['stale_tool']}
            saved = {'history_saved': False, 'generation_ms': 1, 'required_tools_unattempted': ['stale_saved_tool']}
            (supervisor if result_location == 'supervisor' else saved).update(trace)
            yield {'supervisor': {'answer': answer_text, 'tool_status': [], '_trace': supervisor}}
            yield {'session_save': {'_trace': saved}}

    rt.build.return_value = Graph()

    async def scenario():
        stream = await _drain(await _post(rt))
        done = json.loads(next(frame['data'] for frame in stream if frame['event'] == 'done'))
        assert done['turn_envelope']['missing_requirements'] == []
        if answer_text:
            assert done['turn_envelope']['display_status'] == 'completed'
            assert rt.jobs.get_job(done['job_id'])['status'] == 'completed'

    asyncio.run(scenario())


def test_nested_host_source_is_pinned_through_continuation_lease(input_stores):
    from src.core import account_leases
    files = input_stores
    session = 'question_inputs'
    entry = files.register_file(session_id=session, owner_user_id=11, filename='private.csv',
        content=b'A\n1\n', visibility='internal')
    context = {'context_working_state': {'source_refs': [
        {'file_id': entry['file_id'], 'sha256': entry['sha256']}]}}
    job = jobs.claim_job(session, 'nested-source', owner_user_id=11).job_id
    q = questions.create_question(job, owner_user_id=11, session_id=session,
        prompt='Purpose?', options=[], context=context)
    jobs.update_job(job, status='awaiting_input')

    def pins():
        with files._connect() as conn:
            return [row[0] for row in conn.execute(
                'SELECT resource_id FROM session_file_resource_refs WHERE file_id=?', (entry['file_id'],))]

    assert pins() == [q['id']]
    child = answer(job, q, session_id=session, selected_option_ids=[], text='compare')['child_job_id']
    with account_leases.account_lease(11) as lease:
        questions.pin_continuation_inputs(child, owner_user_id=11, session_id=session, lease=lease)
        assert pins() == ['child:' + child]
    assert pins() == []


def test_internal_field_requires_exact_host_authorization(input_stores):
    files = input_stores
    entry = files.register_file(session_id='question_inputs', owner_user_id=11, filename='private.csv',
        content=b'A\n1\n', visibility='internal')
    ref = {'kind': 'source_field', 'dataset_ref': None, 'file_id': entry['file_id'],
           'sha256': entry['sha256'], 'sheet': '', 'column': 'A'}
    options = [{'refs': [ref]}]
    with pytest.raises(ValueError, match='field_not_in_trusted_source_schema'):
        questions._validate_refs(options, 11, {})
    host = {'source_refs': [{'file_id': entry['file_id'], 'sha256': entry['sha256'],
                            'sheet': '', 'columns': ['A']}]}
    questions._validate_refs(options, 11, host)
    with pytest.raises(ValueError, match='source_version_changed'):
        questions._validate_refs(options, 12, host)
    for key, value in [('file_id', 'f' * 32), ('sha256', '0' * 64), ('sheet', 'other'), ('column', 'B')]:
        with pytest.raises(ValueError, match='field_not_in_trusted_source_schema'):
            questions._validate_refs([{'refs': [ref | {key: value}]}], 11, host)


@pytest.mark.parametrize('location', ['dataset_refs', 'context_working_state'])
def test_context_dataset_refs_revalidate_private_payload(input_stores, location):
    import pandas as pd
    from src.research.analysis_results import persist_prepared_dataset
    snapshot = persist_prepared_dataset(11, pd.DataFrame({'A': [1, 2]}), session_id='question_inputs',
        execution_id='synthetic', script_sha256='a' * 64, logical_task_id='synthetic')
    context = ({'dataset_refs': [snapshot.ref]} if location == 'dataset_refs'
               else {'context_working_state': {'dataset_refs': [snapshot.ref]}})
    questions._validate_context_inputs(11, context)
    with pytest.raises(LookupError):
        questions._validate_context_inputs(12, context)
    input_stores.get_account_file(snapshot.payload_file_ref, 11, allow_internal=True)['path'].write_bytes(b'tampered')
    with pytest.raises(ValueError):
        questions._validate_context_inputs(11, context)


def test_semantic_answer_and_child_handoff_recheck_inputs_but_cancel_still_works(input_stores):
    from types import SimpleNamespace
    files = input_stores
    entry = files.register_file(session_id='question_inputs', owner_user_id=11, filename='synthetic.csv', content=b'A\n1\n')
    context = {'attachment_refs': [{'file_id': entry['file_id'], 'sha256': entry['sha256']}]}
    job = jobs.claim_job('question_inputs', 'inputs', owner_user_id=11).job_id
    q = questions.create_question(job, owner_user_id=11, session_id='question_inputs', prompt='Purpose?', options=[], context=context)
    jobs.update_job(job, status='awaiting_input')
    path = files.get_account_file(entry['file_id'], 11)['path']
    path.write_bytes(b'A\n2\n')
    with pytest.raises(ValueError, match='source_version_changed'):
        answer(job, q, session_id='question_inputs', selected_option_ids=[], text='compare')
    with events.transaction() as conn:
        assert conn.execute('SELECT count(*) FROM task_jobs WHERE parent_job_id=?', (job,)).fetchone()[0] == 0
    path.unlink()
    result = answer(job, q, session_id='question_inputs', selected_option_ids=[], cancelled=True)
    assert result['question']['status'] == 'cancelled' and not result['child_job_id']
    with files._connect() as conn:
        assert conn.execute('SELECT count(*) FROM session_file_resource_refs WHERE resource_id=?', (q['id'],)).fetchone()[0] == 0
    path.write_bytes(b'A\n1\n')
    job = jobs.claim_job('question_inputs', 'child-inputs', owner_user_id=11).job_id
    q = questions.create_question(job, owner_user_id=11, session_id='question_inputs', prompt='Purpose?', options=[], context=context)
    jobs.update_job(job, status='awaiting_input')
    child = answer(job, q, session_id='question_inputs', selected_option_ids=[], text='compare')['child_job_id']
    path.write_bytes(b'A\n2\n')
    with pytest.raises(ValueError, match='source_version_changed'):
        questions.pin_continuation_inputs(child, owner_user_id=11, session_id='question_inputs',
                                         lease=SimpleNamespace(owner=11))
    with events.transaction() as conn:
        assert conn.execute('SELECT pin_until FROM task_questions WHERE id=?', (q['id'],)).fetchone()[0] > 0


def test_supersede_releases_script_pins_and_preserves_newer_or_foreign_questions(input_stores):
    from src.research import script_assets
    ref = script_assets.save_script(11, 'question_inputs', name='synthetic.py', source='x=1')['script_ref']
    job = jobs.claim_job('question_inputs', 'old-script', owner_user_id=11).job_id
    q = questions.create_question(job, owner_user_id=11, session_id='question_inputs', prompt='Purpose?', options=[],
        context={'context_working_state': {'script_refs': [ref]}})
    jobs.update_job(job, status='awaiting_input')
    new_job = jobs.claim_job('question_inputs', 'new-script', owner_user_id=11).job_id
    protected = []
    for owner, session, request in [(11, 'question_inputs', 'newer'), (12, 'question_inputs', 'foreign'), (11, 'other', 'other')]:
        pending = jobs.claim_job(session, request, owner_user_id=owner).job_id
        question = questions.create_question(pending, owner_user_id=owner, session_id=session, prompt='Keep?', options=[])
        protected.append((pending, question, owner, session))
    with events.transaction() as conn:
        conn.execute("UPDATE task_jobs SET created_at='2000-01-01T00:00:00' WHERE job_id=?", (job,))
        conn.execute("UPDATE task_jobs SET created_at='2001-01-01T00:00:00' WHERE job_id=?", (new_job,))
    with script_assets._connect() as conn:
        assert conn.execute('SELECT count(*) FROM pins WHERE resource_id=?', (q['id'],)).fetchone()[0] == 1
    assert questions.supersede_pending_questions(new_job, owner_user_id=11, session_id='question_inputs') == 1
    with script_assets._connect() as conn:
        assert conn.execute('SELECT count(*) FROM pins WHERE resource_id=?', (q['id'],)).fetchone()[0] == 0
    for pending, question, owner, session in protected:
        assert questions.list_questions(pending, owner_user_id=owner, session_id=session)[0]['status'] == 'pending'

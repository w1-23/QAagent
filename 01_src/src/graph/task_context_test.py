"""Request interpretation preserves evidence without execution-only manuals."""
import asyncio
import pytest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.agent.host_test import environment as host_environment, install


def test_host_preserves_history_constraints_and_evidence(host_environment, monkeypatch):
    from src.agent import host

    state, _ = host_environment
    user = HumanMessage(content="Approved file v2; do not use network")
    call = AIMessage(content="", tool_calls=[{"name": "inspect", "args": {}, "id": "read-1"}])
    evidence = ToolMessage(content="full evidence " * 2000, tool_call_id="read-1", name="inspect")
    original = [user, call, evidence]
    state.update(messages=original, query="Revise the prior figure, preserve all original values",
                 source_scope="local", history_evidence_block="Source: approved-file-v2.csv; doi:10.1234/fixture")
    sent = install(monkeypatch, [AIMessage(content="I will preserve the original values.")])
    result = asyncio.run(host.general_agent_node(state))
    assert result["turn_outcome"]["status"] == "completed" and len(sent) == 1
    assert sent[0][0].content == "Synthetic general policy"
    assert [m.content for m in sent[0][1:3]] == [user.content, call.content]
    import json
    historical = json.loads(sent[0][3].content)
    assert {"kind": "tool_result", "tool_call_id": "read-1"} in historical["result_refs"]
    assert "Partial historical facts" in historical["notice"]
    assert "full evidence" in historical["result_digest"]
    assert sent[0][3].tool_call_id == "read-1"
    assert "doi:10.1234/fixture" in sent[0][-2].content
    assert state["query"] in sent[0][-1].content
    assert "do not use network" in sent[0][1].content
    assert evidence.content == "full evidence " * 2000


def test_host_does_not_drop_initial_user_message(host_environment, monkeypatch):
    from src.agent import host

    state, _ = host_environment
    state["query"] = "Only inspect my uploaded data"
    sent = install(monkeypatch, [AIMessage(content="Please provide the uploaded data.")])
    result = asyncio.run(host.general_agent_node(state))
    assert result["turn_outcome"]["status"] == "completed" and len(sent) == 1
    users = [m for m in sent[0] if isinstance(m, HumanMessage)]
    assert len(users) == 1 and state["query"] in users[0].content
    assert users[0].additional_kwargs["user_text"] == state["query"]
    assert users[0].additional_kwargs["context_turn_id"] == state["request_id"]


def test_same_loop_final_validation_keeps_full_turn_and_request():
    from src.agent.identity import ExecutionIdentity
    from src.agent.runtime import AgentRuntime, FinalCheck

    query = "Full original constraints " * 1000
    transcript = [SystemMessage(content="execution policy"), HumanMessage(content=query)]
    for index in range(15):
        transcript.extend([
            AIMessage(content="", tool_calls=[{"name": "read", "args": {}, "id": str(index)}]),
            ToolMessage(content=f"{index}:" + "evidence " * 1000, tool_call_id=str(index), name="read")])
    sent = []
    identity = ExecutionIdentity(17, "context-session", "context-turn", "context-run")

    async def model(messages, current):
        assert current == identity
        sent.append(messages)
        return AIMessage(content="All checked" if len(sent) == 1 else "sheet-B not checked")

    async def check(answer, messages, current):
        if len(sent) == 1:
            return FinalCheck(False, ("sheet-B not checked; report actual findings",))
        return FinalCheck()

    async def execute(*args):
        pytest.fail("final validation must not invent tool calls")

    result = asyncio.run(AgentRuntime(model, execute, lambda *args: None,
        check_final=check).run(identity, transcript))
    assert result.status == "complete" and result.answer.content == "sheet-B not checked"
    assert len(sent) == 2
    for messages in sent:
        assert messages[:len(transcript)] == tuple(transcript)
        assert all(a is b for a, b in zip(messages, transcript))
        assert messages[1].content == query
    assert isinstance(sent[1][-1], SystemMessage)
    assert "sheet-B not checked" in sent[1][-1].content
    assert sent[1][-1].additional_kwargs["context_turn_id"] == identity.user_turn_id
    assert len([m for m in result.messages if isinstance(m, HumanMessage)]) == 1


def test_clarification_reuses_receipt_only_for_same_source_bytes():
    from src.graph.task_continuation import restore_continuation
    from src.graph.coverage import CoverageLedger
    import hashlib
    data = b"reference,doi\nexample,10.1/example\n"
    digest = hashlib.sha256(data).hexdigest()
    coverage = {"coverage": {"source_kind": "attachment", "source_sha256": digest,
        "file_index": 0, "submitted": 1}, "rows": [{"record_id": "ref-0", "record_index": 0,
        "source_sha256": digest, "verification_rule": "identity-v1", "status": "metadata_matched"}]}
    record = {"name": "verify_uploaded_references", "tool_call_id": "parent-call", "status": "complete",
        "logical_key": "verify_uploaded_references:" + "a" * 64, "attempt": 1,
        "op_receipt": {"schema_version": 1, "tool_name": "verify_uploaded_references",
            "tool_call_id": "parent-call", "status": "complete", "coverage": coverage}}
    state = {"parent_job_id": "parent", "no_replay": True, "continuation_context": {"tool_status": [record]}}
    restored, signatures = restore_continuation(state, [{"data": data}], validate_artifacts=lambda _: [])
    assert restored[0]["inherited_from_job_id"] == "parent"
    ledger = CoverageLedger()
    ledger.observe(restored[0]["name"], artifact={"op_receipt": restored[0]["op_receipt"]})
    assert ledger.new_targets == 1
    assert signatures["a" * 64]["retryable"] is False
    assert "inherited_from_job_id" not in record
    assert restore_continuation(state, [{"data": b"changed"}], validate_artifacts=lambda _: []) == ([], {})
    record["op_receipt"]["tool_call_id"] = "different"
    assert restore_continuation(state, [{"data": data}], validate_artifacts=lambda _: []) == ([], {})


def test_clarification_does_not_reuse_deleted_figure_export():
    from src.graph.task_continuation import restore_continuation
    from src.graph.coverage import CoverageLedger
    link = "/api/v2/session/session123/artifacts/" + "a" * 32
    record = {"name": "create_research_figure", "tool_call_id": "fig", "status": "complete",
        "artifact_validations": [{"link": link, "valid": True}],
        "op_receipt": {"schema_version": 1, "tool_name": "create_research_figure",
            "tool_call_id": "fig", "status": "complete", "coverage": {
                "figure_id": "project", "exports": [{"format": "svg", "artifact_url": link}]}}}
    state = {"parent_job_id": "parent", "no_replay": True, "continuation_context": {"tool_status": [record]}}
    checked = []
    def validate(links):
        checked.extend(links)
        return [{"link": link, "valid": False, "reason_code": "file_missing"}]
    restored, _ = restore_continuation(state, [], validate_artifacts=validate)
    ledger = CoverageLedger()
    ledger.observe(restored[0]["name"], artifact={"op_receipt": restored[0]["op_receipt"]})
    assert checked == [link]
    assert restored[0]["artifact_count"] == 0
    assert ledger.figures == {}


def test_capped_output_cannot_complete_an_ordinary_answer():
    from src.core.turn_outcome import decide_turn_outcome
    from src.graph.coverage import CoverageLedger
    from src.graph.task_contract import TaskContract
    outcome = decide_turn_outcome(stop_reason="output_truncated", contract=TaskContract(),
        coverage=CoverageLedger(), answer="An answer cut off halfway through")
    assert outcome.status != "completed"
    assert outcome.reason_code == "output_truncated"


@pytest.mark.parametrize("after_rejected_final", [False, True])
def test_runtime_never_completes_truncated_output(host_environment, monkeypatch, after_rejected_final):
    from src.agent import host
    from src.core import task_events

    state, job = host_environment
    responses = [AIMessage(content="", response_metadata={"finish_reason": "stop"})] if after_rejected_final else []
    responses.append(AIMessage(content="Incomplete sentence", response_metadata={"finish_reason": "length"}))
    sent = install(monkeypatch, responses)
    result = asyncio.run(host.general_agent_node(state))
    assert len(sent) == len(responses)
    assert result["_trace"]["stop_reason"] == "model_length"
    assert result["_trace"]["task_result"]["status"] != "completed"
    assert result["turn_outcome"]["status"] != "completed"
    assert "Incomplete sentence" not in result["answer"]
    assert not task_events.projection(job)["operations"]
    events = [row["data"] for row in task_events.replay(job)["events"] if row["event"] == "agent_loop"]
    assert sum(event["kind"] == "run_started" for event in events) == 1
    assert sum(event["kind"] == "final_rejected" for event in events) == int(after_rejected_final)
    assert events[-1]["kind"] == "run_finished" and events[-1]["reason"] == "model_length"
